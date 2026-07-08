"""Domain models for natural-language workflow generation.

These are the contracts between the generation crew's agents:

- ``WorkflowPlan`` — the planner agent's output: an ordered, dependency-aware
  description of what the workflow should do, grounded in catalog capabilities.
- ``DelegationPlan`` / ``WorkPacket`` — the delegator agent's output: typed
  work assignments routing plan steps to specialized worker agents.

Both are designed to be produced via ``genxai.utils.structured.generate_structured``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

StepKind = Literal["agent", "tool", "connector", "mcp", "decision", "loop", "flow"]
TriggerKind = Literal["manual", "webhook", "schedule"]


def _find_cycle(dependencies: dict[str, list[str]]) -> list[str] | None:
    """Return one dependency cycle as a list of ids, or None if acyclic."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = dict.fromkeys(dependencies, WHITE)
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        color[node] = GRAY
        stack.append(node)
        for dep in dependencies.get(node, []):
            if dep not in color:
                continue
            if color[dep] == GRAY:
                return stack[stack.index(dep) :] + [dep]
            if color[dep] == WHITE:
                cycle = visit(dep)
                if cycle:
                    return cycle
        stack.pop()
        color[node] = BLACK
        return None

    for node in dependencies:
        if color[node] == WHITE:
            cycle = visit(node)
            if cycle:
                return cycle
    return None


def _validate_dependency_graph(kind: str, dependencies: dict[str, list[str]]) -> None:
    """Shared id-uniqueness is checked by callers; validate refs and acyclicity."""
    ids = set(dependencies)
    for item_id, deps in dependencies.items():
        for dep in deps:
            if dep == item_id:
                raise ValueError(f"{kind} '{item_id}' depends on itself")
            if dep not in ids:
                raise ValueError(f"{kind} '{item_id}' depends on unknown id '{dep}'")
    cycle = _find_cycle(dependencies)
    if cycle:
        raise ValueError(f"{kind} dependencies contain a cycle: {' -> '.join(cycle)}")


class TriggerSpec(BaseModel):
    """How the generated workflow should start."""

    kind: TriggerKind = "manual"
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Trigger settings, e.g. {'interval_seconds': 300} or webhook filters",
    )


class TeamMember(BaseModel):
    """One agent in a flow step's team."""

    role: str = Field(..., description="The agent's role, e.g. 'Blog Writer'")
    goal: str = Field(..., description="What this agent is responsible for")


class PlanStep(BaseModel):
    """One step of the planned workflow."""

    id: str = Field(..., description="Unique snake_case step identifier")
    title: str = Field(..., description="Short human-readable step name")
    description: str = Field(..., description="What this step does and why")
    kind: StepKind = Field("agent", description="Which workflow node kind implements this step")
    capabilities: list[str] = Field(
        default_factory=list,
        description="Capability names from the catalog (tool/connector/MCP/flow names) this step uses",
    )
    flow_pattern: str | None = Field(
        None,
        description="For kind='flow': the FLOW_TYPES pattern name (e.g. 'coordinator_worker')",
    )
    team: list[TeamMember] = Field(
        default_factory=list,
        description="For kind='flow': the agents that make up the team (at least 2)",
    )
    condition: str | None = Field(
        None,
        description="For kind='decision' or 'loop': the state condition to evaluate",
    )
    when: str | None = Field(
        None,
        description="Condition guarding entry into this step (placed on its incoming edges)",
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Node parameters, e.g. loop max_iterations or flow pattern tunables",
    )
    backstory: str = Field(
        "",
        description="For kind='agent': the agent's persona/backstory",
    )
    temperature: float | None = Field(
        None,
        ge=0.0,
        le=1.0,
        description="For kind='agent': sampling temperature override",
    )
    depends_on: list[str] = Field(
        default_factory=list,
        description="Ids of steps that must complete before this one",
    )
    inputs: str = Field("", description="What data this step consumes")
    outputs: str = Field("", description="What data this step produces")

    @model_validator(mode="after")
    def _check_kind_requirements(self) -> PlanStep:
        if self.kind == "flow":
            if not self.flow_pattern:
                raise ValueError(f"step '{self.id}' has kind='flow' but no flow_pattern")
            if len(self.team) < 2:
                raise ValueError(
                    f"step '{self.id}' has kind='flow' and needs a team of at least 2 agents"
                )
        if self.kind in {"decision", "loop"} and not self.condition:
            raise ValueError(f"step '{self.id}' has kind='{self.kind}' but no condition")
        return self


class OpenQuestion(BaseModel):
    """An ambiguity the planner could not resolve from the user's request."""

    question: str
    why_it_matters: str = ""
    default_assumption: str = Field(
        "",
        description="What the plan assumes if the user does not answer",
    )


class WorkflowPlan(BaseModel):
    """The planner agent's structured output for a natural-language request."""

    name: str = Field(..., description="Workflow name")
    description: str = Field("", description="One-paragraph summary of the workflow")
    trigger: TriggerSpec = Field(default_factory=TriggerSpec)
    steps: list[PlanStep] = Field(..., min_length=1)
    open_questions: list[OpenQuestion] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_steps(self) -> WorkflowPlan:
        seen: set[str] = set()
        for step in self.steps:
            if step.id in seen:
                raise ValueError(f"duplicate step id '{step.id}'")
            seen.add(step.id)
        _validate_dependency_graph("step", {step.id: step.depends_on for step in self.steps})
        return self

    def capability_names(self) -> set[str]:
        """All catalog capabilities referenced anywhere in the plan."""
        names: set[str] = set()
        for step in self.steps:
            names.update(step.capabilities)
            if step.flow_pattern:
                names.add(step.flow_pattern)
        return names

    def execution_order(self) -> list[str]:
        """Step ids in a valid topological order (dependencies first)."""
        remaining = {step.id: set(step.depends_on) for step in self.steps}
        ordered: list[str] = []
        while remaining:
            ready = sorted(sid for sid, deps in remaining.items() if not deps)
            if not ready:  # unreachable: validator rejects cycles
                raise ValueError("steps contain a dependency cycle")
            for sid in ready:
                ordered.append(sid)
                del remaining[sid]
            for deps in remaining.values():
                deps.difference_update(ready)
        return ordered


class WorkPacket(BaseModel):
    """A unit of work the delegator assigns to one specialized worker."""

    id: str = Field(..., description="Unique packet identifier")
    worker: str = Field(
        ...,
        description="Capability tag of the worker this packet is routed to, e.g. 'node_designer'",
    )
    objective: str = Field(..., description="What the worker must produce")
    step_ids: list[str] = Field(
        default_factory=list,
        description="WorkflowPlan step ids this packet covers",
    )
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="Extra grounding the worker needs (catalog excerpts, constraints)",
    )
    depends_on: list[str] = Field(
        default_factory=list,
        description="Ids of packets whose results this packet needs",
    )


class DelegationPlan(BaseModel):
    """The delegator agent's structured output: packets routed to workers."""

    packets: list[WorkPacket] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _check_packets(self) -> DelegationPlan:
        seen: set[str] = set()
        for packet in self.packets:
            if packet.id in seen:
                raise ValueError(f"duplicate packet id '{packet.id}'")
            seen.add(packet.id)
        _validate_dependency_graph(
            "packet", {packet.id: packet.depends_on for packet in self.packets}
        )
        return self

    def validate_against_plan(self, plan: WorkflowPlan) -> None:
        """Ensure every packet references real plan steps and all steps are covered."""
        plan_step_ids = {step.id for step in plan.steps}
        covered: set[str] = set()
        for packet in self.packets:
            unknown = set(packet.step_ids) - plan_step_ids
            if unknown:
                raise ValueError(
                    f"packet '{packet.id}' references unknown step ids: {sorted(unknown)}"
                )
            covered.update(packet.step_ids)
        missing = plan_step_ids - covered
        if missing:
            raise ValueError(f"plan steps not covered by any packet: {sorted(missing)}")

    def dispatch_waves(self) -> list[list[WorkPacket]]:
        """Packets grouped into waves executable concurrently (dependencies first)."""
        by_id = {packet.id: packet for packet in self.packets}
        remaining = {packet.id: set(packet.depends_on) for packet in self.packets}
        waves: list[list[WorkPacket]] = []
        while remaining:
            ready = sorted(pid for pid, deps in remaining.items() if not deps)
            if not ready:  # unreachable: validator rejects cycles
                raise ValueError("packets contain a dependency cycle")
            waves.append([by_id[pid] for pid in ready])
            for pid in ready:
                del remaining[pid]
            for deps in remaining.values():
                deps.difference_update(ready)
        return waves
