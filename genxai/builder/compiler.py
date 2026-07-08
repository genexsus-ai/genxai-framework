"""Deterministic compiler: WorkflowPlan → executable workflow document.

Turns the planner's validated :class:`WorkflowPlan` into a workflow dict in
the canonical YAML DSL shape — accepted by
``genxai.core.graph.workflow_io._validate_workflow_schema`` and executable by
``WorkflowExecutor``. Compilation is pure code, no LLM: everything the LLM
decides lives in the plan; everything mechanical lives here.

Agent definitions are emitted twice on purpose: in ``workflow["agents"]``
(the DSL/CLI path registers them from there) and in each agent node's
``config`` block (``WorkflowExecutor._create_agents_from_nodes`` rebuilds
agents from node config and overwrites the registry).
"""

from __future__ import annotations

from typing import Any

from genxai.builder.schemas import PlanStep, WorkflowPlan
from genxai.core.graph.workflow_io import _validate_workflow_schema

DEFAULT_MODEL = "claude-sonnet-5"

START_NODE_ID = "start"
END_NODE_ID = "end"


class CompileError(ValueError):
    """A plan cannot be compiled into a valid workflow document."""


def _agent_definition(step: PlanStep, default_model: str) -> dict[str, Any]:
    definition = {
        "id": step.id,
        "role": step.title,
        "goal": step.description,
        "llm_model": default_model,
        "tools": list(step.capabilities),
    }
    if step.backstory:
        definition["backstory"] = step.backstory
    if step.temperature is not None:
        definition["llm_temperature"] = step.temperature
    return definition


def _agent_node(step: PlanStep, default_model: str) -> dict[str, Any]:
    config: dict[str, Any] = {
        "role": step.title,
        "goal": step.description,
        "llm_model": default_model,
        "tools": list(step.capabilities),
    }
    if step.backstory:
        config["backstory"] = step.backstory
    if step.temperature is not None:
        config["temperature"] = step.temperature
    if step.inputs:
        config["task"] = f"{step.description}\nInput: {step.inputs}"
    return {"id": step.id, "type": "agent", "agent": step.id, "config": config}


def _tool_node(step: PlanStep) -> dict[str, Any]:
    if not step.capabilities:
        raise CompileError(
            f"step '{step.id}' has kind='{step.kind}' but names no capability to invoke"
        )
    if len(step.capabilities) > 1:
        raise CompileError(
            f"step '{step.id}' has kind='{step.kind}' and must name exactly one capability; "
            f"got {step.capabilities} — split it into one step per capability"
        )
    return {
        "id": step.id,
        "type": "tool",
        "config": {"tool_name": step.capabilities[0], "tool_params": dict(step.params)},
    }


def _condition_node(step: PlanStep) -> dict[str, Any]:
    return {
        "id": step.id,
        "type": "condition",
        "config": {"condition": step.condition},
    }


def _loop_node(step: PlanStep) -> dict[str, Any]:
    config: dict[str, Any] = {"condition": step.condition}
    if "max_iterations" in step.params:
        config["max_iterations"] = step.params["max_iterations"]
    return {"id": step.id, "type": "loop", "config": config}


def _flow_node(step: PlanStep, default_model: str) -> dict[str, Any]:
    config: dict[str, Any] = {
        "flow_type": step.flow_pattern,
        "agents": [
            {"role": member.role, "goal": member.goal, "llm_model": default_model}
            for member in step.team
        ],
        "params": dict(step.params),
        "task": step.description,
    }
    return {"id": step.id, "type": "flow", "config": config}


def _step_node(step: PlanStep, default_model: str) -> dict[str, Any]:
    if step.kind == "agent":
        return _agent_node(step, default_model)
    if step.kind in {"tool", "connector", "mcp"}:
        # Connector and MCP capabilities execute through tool nodes: the
        # runtime exposes them as tools (connector_action / mcp__server__tool).
        return _tool_node(step)
    if step.kind == "decision":
        return _condition_node(step)
    if step.kind == "loop":
        return _loop_node(step)
    if step.kind == "flow":
        return _flow_node(step, default_model)
    raise CompileError(f"step '{step.id}' has unsupported kind '{step.kind}'")


def compile_plan(plan: WorkflowPlan, *, default_model: str = DEFAULT_MODEL) -> dict[str, Any]:
    """Compile a plan into a schema-valid workflow document.

    Raises :class:`CompileError` when the plan cannot produce a valid
    document; the message is written to be fed back to the planner LLM.
    """
    step_ids = {step.id for step in plan.steps}
    if START_NODE_ID in step_ids or END_NODE_ID in step_ids:
        raise CompileError(
            f"step ids '{START_NODE_ID}' and '{END_NODE_ID}' are reserved for the "
            "input/output nodes; rename those steps"
        )

    nodes: list[dict[str, Any]] = [{"id": START_NODE_ID, "type": "input"}]
    agents: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    for step in plan.steps:
        nodes.append(_step_node(step, default_model))
        if step.kind == "agent":
            agents.append(_agent_definition(step, default_model))

        sources = step.depends_on or [START_NODE_ID]
        for source in sources:
            edge: dict[str, Any] = {"from": source, "to": step.id}
            if step.when:
                edge["condition"] = step.when
            edges.append(edge)

    nodes.append({"id": END_NODE_ID, "type": "output"})

    # Steps nothing depends on are sinks: wire them to the output node.
    depended_on = {dep for step in plan.steps for dep in step.depends_on}
    for step in plan.steps:
        if step.id not in depended_on:
            edges.append({"from": step.id, "to": END_NODE_ID})

    # Unconditioned fan-out from one source runs concurrently.
    outgoing: dict[str, list[dict[str, Any]]] = {}
    for edge in edges:
        if not edge.get("condition") and edge["to"] != END_NODE_ID:
            outgoing.setdefault(edge["from"], []).append(edge)
    for fan_out in outgoing.values():
        if len(fan_out) > 1:
            for edge in fan_out:
                edge["parallel"] = True

    workflow: dict[str, Any] = {
        "name": plan.name,
        "description": plan.description,
        "trigger": plan.trigger.model_dump(),
        "agents": agents,
        "graph": {"nodes": nodes, "edges": edges},
    }

    try:
        _validate_workflow_schema(workflow)
    except ValueError as exc:
        raise CompileError(f"compiled workflow failed schema validation: {exc}") from exc
    return workflow


def compile_plan_to_yaml(plan: WorkflowPlan, *, default_model: str = DEFAULT_MODEL) -> str:
    """Compile a plan and render it as workflow YAML (top-level ``workflow:`` key)."""
    import yaml

    workflow = compile_plan(plan, default_model=default_model)
    return yaml.safe_dump({"workflow": workflow}, sort_keys=False)
