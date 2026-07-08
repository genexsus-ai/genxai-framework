"""Unit tests for the plan → workflow compiler."""

import pytest

from genxai.builder.compiler import CompileError, compile_plan, compile_plan_to_yaml
from genxai.builder.schemas import PlanStep, WorkflowPlan
from genxai.core.graph.workflow_io import _validate_workflow_schema


def _step(step_id: str, **kwargs) -> PlanStep:
    defaults = {
        "id": step_id,
        "title": step_id.replace("_", " ").title(),
        "description": f"Step {step_id}",
    }
    defaults.update(kwargs)
    return PlanStep(**defaults)


def _plan(steps: list[PlanStep], **kwargs) -> WorkflowPlan:
    defaults = {"name": "Test Workflow", "steps": steps}
    defaults.update(kwargs)
    return WorkflowPlan(**defaults)


def _edges(workflow: dict) -> set[tuple[str, str]]:
    return {(edge["from"], edge["to"]) for edge in workflow["graph"]["edges"]}


class TestCompilePlan:
    def test_sequential_agents(self) -> None:
        workflow = compile_plan(_plan([_step("research"), _step("write", depends_on=["research"])]))

        node_ids = [node["id"] for node in workflow["graph"]["nodes"]]
        assert node_ids == ["start", "research", "write", "end"]
        assert _edges(workflow) == {
            ("start", "research"),
            ("research", "write"),
            ("write", "end"),
        }
        # Compiler output is always schema-valid.
        _validate_workflow_schema(workflow)

    def test_agent_definitions_emitted_in_both_places(self) -> None:
        workflow = compile_plan(_plan([_step("analyze", capabilities=["csv_processor"])]))

        agent_def = workflow["agents"][0]
        assert agent_def["id"] == "analyze"
        assert agent_def["tools"] == ["csv_processor"]

        node = workflow["graph"]["nodes"][1]
        assert node["agent"] == "analyze"
        assert node["config"]["tools"] == ["csv_processor"]
        assert node["config"]["role"] == "Analyze"

    def test_agent_backstory_and_temperature_emitted(self) -> None:
        workflow = compile_plan(
            _plan([_step("analyze", backstory="A seasoned analyst", temperature=0.2)])
        )

        agent_def = workflow["agents"][0]
        assert agent_def["backstory"] == "A seasoned analyst"
        assert agent_def["llm_temperature"] == 0.2

        node_config = workflow["graph"]["nodes"][1]["config"]
        assert node_config["backstory"] == "A seasoned analyst"
        assert node_config["temperature"] == 0.2

    def test_parallel_fanout_marked(self) -> None:
        workflow = compile_plan(
            _plan(
                [
                    _step("financial"),
                    _step("competitive"),
                    _step("merge", depends_on=["financial", "competitive"]),
                ]
            )
        )

        fan_out = [edge for edge in workflow["graph"]["edges"] if edge["from"] == "start"]
        assert len(fan_out) == 2
        assert all(edge.get("parallel") for edge in fan_out)
        join = [edge for edge in workflow["graph"]["edges"] if edge["to"] == "merge"]
        assert not any(edge.get("parallel") for edge in join)

    def test_decision_branching_with_when(self) -> None:
        workflow = compile_plan(
            _plan(
                [
                    _step("triage", kind="decision", condition="category"),
                    _step("respond", depends_on=["triage"], when="category == 'routine'"),
                    _step("escalate", depends_on=["triage"], when="category == 'urgent'"),
                ]
            )
        )

        triage_node = workflow["graph"]["nodes"][1]
        assert triage_node["type"] == "condition"
        assert triage_node["config"]["condition"] == "category"

        branch_edges = {
            edge["to"]: edge.get("condition")
            for edge in workflow["graph"]["edges"]
            if edge["from"] == "triage"
        }
        assert branch_edges == {
            "respond": "category == 'routine'",
            "escalate": "category == 'urgent'",
        }
        # Conditioned edges must not be marked parallel.
        assert not any(
            edge.get("parallel") for edge in workflow["graph"]["edges"] if edge["from"] == "triage"
        )

    def test_tool_step(self) -> None:
        workflow = compile_plan(_plan([_step("scrape", kind="tool", capabilities=["web_scraper"])]))

        node = workflow["graph"]["nodes"][1]
        assert node["type"] == "tool"
        assert node["config"]["tool_name"] == "web_scraper"

    def test_connector_and_mcp_steps_become_tool_nodes(self) -> None:
        workflow = compile_plan(
            _plan(
                [
                    _step("notify", kind="connector", capabilities=["connector_action"]),
                    _step("lookup", kind="mcp", capabilities=["mcp_action"]),
                ]
            )
        )

        types = {node["id"]: node["type"] for node in workflow["graph"]["nodes"]}
        assert types["notify"] == "tool"
        assert types["lookup"] == "tool"

    def test_tool_step_without_capability_fails(self) -> None:
        with pytest.raises(CompileError, match="names no capability"):
            compile_plan(_plan([_step("scrape", kind="tool")]))

    def test_tool_step_with_multiple_capabilities_fails(self) -> None:
        with pytest.raises(CompileError, match="exactly one capability"):
            compile_plan(
                _plan([_step("scrape", kind="tool", capabilities=["web_scraper", "http_client"])])
            )

    def test_loop_step(self) -> None:
        workflow = compile_plan(
            _plan(
                [
                    _step(
                        "refine",
                        kind="loop",
                        condition="needs_work",
                        params={"max_iterations": 5},
                    )
                ]
            )
        )

        node = workflow["graph"]["nodes"][1]
        assert node["type"] == "loop"
        assert node["config"] == {"condition": "needs_work", "max_iterations": 5}

    def test_flow_step(self) -> None:
        workflow = compile_plan(
            _plan(
                [
                    _step(
                        "draft_team",
                        kind="flow",
                        flow_pattern="critic_review",
                        team=[
                            {"role": "Writer", "goal": "Draft the post"},
                            {"role": "Critic", "goal": "Review the draft"},
                        ],
                        params={"max_iterations": 3},
                    )
                ]
            )
        )

        node = workflow["graph"]["nodes"][1]
        assert node["type"] == "flow"
        assert node["config"]["flow_type"] == "critic_review"
        assert len(node["config"]["agents"]) == 2
        assert node["config"]["params"] == {"max_iterations": 3}

    def test_reserved_step_ids_rejected(self) -> None:
        with pytest.raises(CompileError, match="reserved"):
            compile_plan(_plan([_step("start")]))

    def test_trigger_passed_through(self) -> None:
        workflow = compile_plan(
            _plan(
                [_step("digest")],
                trigger={"kind": "schedule", "config": {"interval_seconds": 86400}},
            )
        )

        assert workflow["trigger"] == {
            "kind": "schedule",
            "config": {"interval_seconds": 86400},
        }

    def test_compile_plan_to_yaml(self) -> None:
        yaml_text = compile_plan_to_yaml(_plan([_step("summarize")]))

        assert yaml_text.startswith("workflow:")
        assert "summarize" in yaml_text
