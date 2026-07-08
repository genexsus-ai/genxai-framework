"""Unit tests for the multi-agent generation crew."""

import json

import pytest

from genxai.builder.catalog import build_capability_catalog
from genxai.builder.crew import (
    AgentSpec,
    NodeSpec,
    apply_specs,
    crew_generate_workflow,
    route_deterministically,
)
from genxai.builder.schemas import PlanStep, WorkflowPlan
from genxai.core.graph.workflow_io import _validate_workflow_schema
from genxai.tools.registry import ToolRegistry
from tests.utils.mock_llm import MockLLMProvider


@pytest.fixture(autouse=True)
def _empty_tool_registry():
    ToolRegistry.clear()
    yield
    ToolRegistry.clear()


def _catalog():
    return build_capability_catalog(
        extra_sections={
            "tool": [
                {"name": "email_sender", "description": "Send an email"},
                {"name": "web_scraper", "description": "Scrape a web page"},
            ]
        }
    )


_PLAN_JSON = json.dumps(
    {
        "name": "Summarize and Send",
        "description": "Summarize text, then email the summary",
        "trigger": {"kind": "manual", "config": {}},
        "steps": [
            {
                "id": "summarize",
                "title": "Summarizer",
                "description": "Summarize the input text",
                "kind": "agent",
                "capabilities": [],
                "depends_on": [],
            },
            {
                "id": "send",
                "title": "Send Email",
                "description": "Email the summary",
                "kind": "tool",
                "capabilities": ["email_sender"],
                "depends_on": ["summarize"],
            },
        ],
        "open_questions": [],
    }
)

_DELEGATION_JSON = json.dumps(
    {
        "packets": [
            {
                "id": "p_agents",
                "worker": "agent_designer",
                "objective": "Design the summarizer agent",
                "step_ids": ["summarize"],
            },
            {
                "id": "p_nodes",
                "worker": "node_designer",
                "objective": "Configure the email step",
                "step_ids": ["send"],
            },
        ]
    }
)

_AGENT_DESIGN_JSON = json.dumps(
    {
        "specs": [
            {
                "step_id": "summarize",
                "role": "Executive Summarizer",
                "goal": "Produce a crisp three-bullet summary",
                "backstory": "A veteran editor of executive briefings",
                "temperature": 0.3,
                "tools": [],
            }
        ]
    }
)

_NODE_DESIGN_JSON = json.dumps(
    {
        "specs": [
            {
                "step_id": "send",
                "capability": "email_sender",
                "params": {"to": "me@example.com"},
            }
        ]
    }
)

_REVIEW_OK_JSON = json.dumps({"approved": True, "issues": []})
_REVIEW_REJECT_JSON = json.dumps(
    {"approved": False, "issues": ["The summary length is not specified"]}
)


def _step(step_id: str, **kwargs) -> PlanStep:
    defaults = {
        "id": step_id,
        "title": step_id.title(),
        "description": f"Step {step_id}",
    }
    defaults.update(kwargs)
    return PlanStep(**defaults)


class TestRouting:
    def test_route_deterministically_splits_by_kind(self):
        plan = WorkflowPlan(
            name="P",
            steps=[
                _step("think"),
                _step("scrape", kind="tool", capabilities=["web_scraper"]),
            ],
        )
        delegation = route_deterministically(plan)
        by_worker = {p.worker: p.step_ids for p in delegation.packets}
        assert by_worker == {
            "agent_designer": ["think"],
            "node_designer": ["scrape"],
        }
        delegation.validate_against_plan(plan)


class TestApplySpecs:
    def test_merges_agent_and_node_specs(self):
        plan = WorkflowPlan(
            name="P",
            steps=[
                _step("think"),
                _step("send", kind="tool", capabilities=["email_sender"]),
            ],
        )
        merged, warnings = apply_specs(
            plan,
            [
                AgentSpec(
                    step_id="think",
                    role="Deep Thinker",
                    goal="Reason carefully",
                    backstory="A logician",
                    temperature=0.2,
                    tools=["web_scraper"],
                )
            ],
            [NodeSpec(step_id="send", capability="email_sender", params={"to": "x"})],
            _catalog(),
        )

        think = merged.steps[0]
        assert think.title == "Deep Thinker"
        assert think.backstory == "A logician"
        assert think.temperature == 0.2
        assert think.capabilities == ["web_scraper"]
        assert merged.steps[1].params == {"to": "x"}
        assert warnings == []

    def test_unknown_tools_and_steps_dropped_with_warnings(self):
        plan = WorkflowPlan(name="P", steps=[_step("think")])
        merged, warnings = apply_specs(
            plan,
            [
                AgentSpec(step_id="think", role="R", goal="G", tools=["made_up_tool"]),
                AgentSpec(step_id="ghost", role="R", goal="G"),
            ],
            [NodeSpec(step_id="think", capability="email_sender")],
            _catalog(),
        )

        assert merged.steps[0].capabilities == []
        assert any("made_up_tool" in warning for warning in warnings)
        assert any("ghost" in warning for warning in warnings)
        # Node spec for an agent step is also dropped.
        assert any("agent step" in warning for warning in warnings)


class TestCrewGenerateWorkflow:
    @pytest.mark.asyncio
    async def test_full_crew_round(self):
        provider = MockLLMProvider(
            responses=[
                _PLAN_JSON,
                _DELEGATION_JSON,
                _AGENT_DESIGN_JSON,
                _NODE_DESIGN_JSON,
                _REVIEW_OK_JSON,
            ]
        )

        result = await crew_generate_workflow(
            "Summarize text and email it to me",
            llm_provider=provider,
            catalog=_catalog(),
        )

        _validate_workflow_schema(result.workflow)
        assert result.rounds == 1
        assert result.delegation_fallback is False
        assert result.review.approved is True

        agent_def = result.workflow["agents"][0]
        assert agent_def["role"] == "Executive Summarizer"
        assert agent_def["backstory"] == "A veteran editor of executive briefings"
        assert agent_def["llm_temperature"] == 0.3

        send_node = next(node for node in result.workflow["graph"]["nodes"] if node["id"] == "send")
        assert send_node["config"]["tool_params"] == {"to": "me@example.com"}

    @pytest.mark.asyncio
    async def test_review_rejection_triggers_replan_with_feedback(self):
        provider = MockLLMProvider(
            responses=[
                _PLAN_JSON,
                _DELEGATION_JSON,
                _AGENT_DESIGN_JSON,
                _NODE_DESIGN_JSON,
                _REVIEW_REJECT_JSON,
                _PLAN_JSON,
                _DELEGATION_JSON,
                _AGENT_DESIGN_JSON,
                _NODE_DESIGN_JSON,
                _REVIEW_OK_JSON,
            ]
        )

        result = await crew_generate_workflow(
            "Summarize text and email it to me",
            llm_provider=provider,
            catalog=_catalog(),
        )

        assert result.rounds == 2
        # The second-round planner prompt carries the reviewer's issue.
        assert "summary length is not specified" in provider.prompts[5]

    @pytest.mark.asyncio
    async def test_delegation_failure_falls_back_to_deterministic_routing(self):
        provider = MockLLMProvider(
            responses=[
                _PLAN_JSON,
                "garbage",  # delegation attempt 1
                "garbage",  # delegation repair attempt
                _AGENT_DESIGN_JSON,
                _NODE_DESIGN_JSON,
                _REVIEW_OK_JSON,
            ]
        )

        result = await crew_generate_workflow(
            "Summarize text and email it to me",
            llm_provider=provider,
            catalog=_catalog(),
        )

        assert result.delegation_fallback is True
        packet_ids = sorted(p.id for p in result.delegation.packets)
        assert packet_ids == ["design_agents", "design_nodes"]
        _validate_workflow_schema(result.workflow)

    @pytest.mark.asyncio
    async def test_review_disabled_skips_reviewer_call(self):
        provider = MockLLMProvider(
            responses=[
                _PLAN_JSON,
                _DELEGATION_JSON,
                _AGENT_DESIGN_JSON,
                _NODE_DESIGN_JSON,
            ]
        )

        result = await crew_generate_workflow(
            "Summarize text and email it to me",
            llm_provider=provider,
            catalog=_catalog(),
            enable_review=False,
        )

        assert result.review is None
        assert len(provider.prompts) == 4

    @pytest.mark.asyncio
    async def test_evaluate_generation_accepts_crew_fn(self):
        from genxai.builder.generator import evaluate_generation

        provider = MockLLMProvider(
            responses=[
                _PLAN_JSON,
                _DELEGATION_JSON,
                _AGENT_DESIGN_JSON,
                _NODE_DESIGN_JSON,
                _REVIEW_OK_JSON,
            ]
        )

        report = await evaluate_generation(
            llm_provider=provider,
            prompts=["Summarize text and email it to me"],
            catalog=_catalog(),
            generate_fn=crew_generate_workflow,
        )

        assert report.validity_rate == 1.0
        assert report.outcomes[0].workflow_name == "Summarize and Send"

    @pytest.mark.asyncio
    async def test_final_round_compiles_despite_rejection(self):
        provider = MockLLMProvider(
            responses=[
                _PLAN_JSON,
                _DELEGATION_JSON,
                _AGENT_DESIGN_JSON,
                _NODE_DESIGN_JSON,
                _REVIEW_REJECT_JSON,
            ]
        )

        result = await crew_generate_workflow(
            "Summarize text and email it to me",
            llm_provider=provider,
            catalog=_catalog(),
            max_rounds=1,
        )

        assert result.review.approved is False
        _validate_workflow_schema(result.workflow)
