"""Unit tests for the planner and the end-to-end generation pipeline."""

import json

import pytest

from genxai.builder.generator import (
    GenerationError,
    evaluate_generation,
    generate_workflow,
)
from genxai.builder.planner import PlanningError, plan_workflow
from genxai.core.graph.workflow_io import _validate_workflow_schema
from genxai.tools.registry import ToolRegistry
from tests.utils.mock_llm import MockLLMProvider


def _plan_json(steps: list[dict], name: str = "Test Workflow") -> str:
    return json.dumps(
        {
            "name": name,
            "description": "A test workflow",
            "trigger": {"kind": "manual", "config": {}},
            "steps": steps,
            "open_questions": [],
        }
    )


_SIMPLE_STEPS = [
    {
        "id": "summarize",
        "title": "Summarizer",
        "description": "Summarize the input text",
        "kind": "agent",
        "capabilities": [],
        "depends_on": [],
    }
]


@pytest.fixture()
def empty_registry():
    ToolRegistry.clear()
    yield
    ToolRegistry.clear()


class TestPlanWorkflow:
    @pytest.mark.asyncio
    async def test_valid_plan_first_try(self, empty_registry) -> None:
        provider = MockLLMProvider(response_text=_plan_json(_SIMPLE_STEPS))

        result = await plan_workflow("Summarize any text I give you", llm_provider=provider)

        assert result.plan.name == "Test Workflow"
        assert result.grounding_retries == 0
        # The prompt must carry the catalog and the guidelines.
        assert "capabilities" in provider.prompts[0]
        assert "coordinator_worker" in provider.prompts[0]

    @pytest.mark.asyncio
    async def test_unknown_capability_triggers_grounding_retry(self, empty_registry) -> None:
        hallucinated = [dict(_SIMPLE_STEPS[0], capabilities=["quantum_tool"])]
        provider = MockLLMProvider(responses=[_plan_json(hallucinated), _plan_json(_SIMPLE_STEPS)])

        result = await plan_workflow("Summarize text", llm_provider=provider)

        assert result.grounding_retries == 1
        assert "quantum_tool" in provider.prompts[1]

    @pytest.mark.asyncio
    async def test_persistent_unknown_capability_raises(self, empty_registry) -> None:
        hallucinated = [dict(_SIMPLE_STEPS[0], capabilities=["quantum_tool"])]
        provider = MockLLMProvider(responses=[_plan_json(hallucinated)])

        with pytest.raises(PlanningError, match="quantum_tool"):
            await plan_workflow(
                "Summarize text",
                llm_provider=provider,
                max_grounding_retries=1,
            )


class TestGenerateWorkflow:
    @pytest.mark.asyncio
    async def test_end_to_end_generation(self, empty_registry) -> None:
        provider = MockLLMProvider(response_text=_plan_json(_SIMPLE_STEPS))

        result = await generate_workflow("Summarize any text I give you", llm_provider=provider)

        _validate_workflow_schema(result.workflow)
        assert result.workflow["name"] == "Test Workflow"
        assert result.compile_retries == 0
        node_ids = [node["id"] for node in result.workflow["graph"]["nodes"]]
        assert node_ids == ["start", "summarize", "end"]

    @pytest.mark.asyncio
    async def test_compile_error_feeds_back_to_planner(self, empty_registry) -> None:
        # First plan uses the reserved id 'start' -> CompileError -> re-plan.
        bad_steps = [dict(_SIMPLE_STEPS[0], id="start")]
        provider = MockLLMProvider(responses=[_plan_json(bad_steps), _plan_json(_SIMPLE_STEPS)])

        result = await generate_workflow("Summarize text", llm_provider=provider)

        assert result.compile_retries == 1
        assert "reserved" in provider.prompts[1]

    @pytest.mark.asyncio
    async def test_persistent_compile_error_raises(self, empty_registry) -> None:
        bad_steps = [dict(_SIMPLE_STEPS[0], id="start")]
        provider = MockLLMProvider(responses=[_plan_json(bad_steps)])

        with pytest.raises(GenerationError, match="reserved") as exc_info:
            await generate_workflow("Summarize text", llm_provider=provider, max_compile_retries=1)
        assert exc_info.value.plan is not None

    @pytest.mark.asyncio
    async def test_generated_agents_use_default_model(self, empty_registry) -> None:
        provider = MockLLMProvider(response_text=_plan_json(_SIMPLE_STEPS))

        result = await generate_workflow(
            "Summarize text", llm_provider=provider, default_model="claude-haiku-4-5"
        )

        assert result.workflow["agents"][0]["llm_model"] == "claude-haiku-4-5"


class TestEvaluateGeneration:
    @pytest.mark.asyncio
    async def test_all_valid(self, empty_registry) -> None:
        provider = MockLLMProvider(response_text=_plan_json(_SIMPLE_STEPS))

        report = await evaluate_generation(
            llm_provider=provider, prompts=["Summarize text", "Write a report"]
        )

        assert report.validity_rate == 1.0
        assert all(outcome.workflow_name == "Test Workflow" for outcome in report.outcomes)
        assert "2/2" in report.summary()

    @pytest.mark.asyncio
    async def test_failures_recorded_not_raised(self, empty_registry) -> None:
        provider = MockLLMProvider(response_text="not json at all")

        report = await evaluate_generation(
            llm_provider=provider,
            prompts=["Summarize text"],
            max_repair_attempts=0,
        )

        assert report.validity_rate == 0.0
        assert "StructuredOutputError" in report.outcomes[0].error
