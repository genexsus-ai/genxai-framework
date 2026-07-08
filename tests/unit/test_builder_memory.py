"""Unit tests for generation memory, refine, metrics, and buildability."""

import json

import pytest

from genxai.builder.generator import (
    check_workflow_builds,
    evaluate_generation,
    generate_workflow,
    refine_workflow,
)
from genxai.builder.memory import GenerationMemory
from genxai.builder.planner import plan_workflow
from genxai.builder.schemas import PlanStep, WorkflowPlan
from genxai.tools.registry import ToolRegistry
from tests.utils.mock_llm import MockLLMProvider


@pytest.fixture(autouse=True)
def _empty_registry():
    ToolRegistry.clear()
    yield
    ToolRegistry.clear()


def _plan(name: str = "Summarizer") -> WorkflowPlan:
    return WorkflowPlan(
        name=name,
        steps=[PlanStep(id="summarize", title="Summarizer", description="Summarize input")],
    )


_PLAN_JSON = json.dumps(
    {
        "name": "Summarizer",
        "description": "",
        "trigger": {"kind": "manual", "config": {}},
        "steps": [
            {
                "id": "summarize",
                "title": "Summarizer",
                "description": "Summarize the input text",
                "kind": "agent",
                "capabilities": [],
                "depends_on": [],
            }
        ],
        "open_questions": [],
    }
)


class TestGenerationMemory:
    def test_record_recall_roundtrip(self, tmp_path):
        memory = GenerationMemory(tmp_path / "mem.jsonl")
        memory.record("summarize my meeting notes", _plan())
        memory.record("scrape a website for prices", _plan("Scraper"))

        recalled = memory.recall("please summarize these notes")

        assert recalled and recalled[0].plan["name"] == "Summarizer"

    def test_accepted_records_ranked_first(self, tmp_path):
        memory = GenerationMemory(tmp_path / "mem.jsonl")
        memory.record("summarize text quickly", _plan("First"))
        accepted_id = memory.record("summarize text carefully", _plan("Second"))
        assert memory.mark_accepted(accepted_id) is True

        recalled = memory.recall("summarize text", limit=2)

        assert recalled[0].plan["name"] == "Second"
        assert recalled[0].accepted is True

    def test_mark_accepted_unknown_id(self, tmp_path):
        memory = GenerationMemory(tmp_path / "mem.jsonl")
        assert memory.mark_accepted("nope") is False

    def test_unrelated_prompts_not_recalled(self, tmp_path):
        memory = GenerationMemory(tmp_path / "mem.jsonl")
        memory.record("deploy kubernetes clusters", _plan())

        assert memory.recall("summarize meeting notes") == []

    def test_render_for_prompt(self, tmp_path):
        memory = GenerationMemory(tmp_path / "mem.jsonl")
        record_id = memory.record("summarize text", _plan())
        memory.mark_accepted(record_id)

        rendered = memory.render_for_prompt(memory.recall("summarize text"))

        assert "accepted by the user" in rendered
        assert "Summarizer" in rendered
        assert memory.render_for_prompt([]) == ""


class TestMemoryInPipeline:
    @pytest.mark.asyncio
    async def test_planner_prompt_includes_recalled_plans(self, tmp_path):
        memory = GenerationMemory(tmp_path / "mem.jsonl")
        accepted_id = memory.record("summarize meeting notes", _plan("Remembered Plan"))
        memory.mark_accepted(accepted_id)

        provider = MockLLMProvider(response_text=_PLAN_JSON)
        await plan_workflow("summarize my notes", llm_provider=provider, memory=memory)

        assert "Remembered Plan" in provider.prompts[0]
        assert "accepted by the user" in provider.prompts[0]

    @pytest.mark.asyncio
    async def test_generate_records_and_returns_generation_id(self, tmp_path):
        memory = GenerationMemory(tmp_path / "mem.jsonl")
        provider = MockLLMProvider(response_text=_PLAN_JSON)

        result = await generate_workflow("summarize text", llm_provider=provider, memory=memory)

        assert result.generation_id is not None
        assert memory.mark_accepted(result.generation_id) is True


class TestRefineWorkflow:
    @pytest.mark.asyncio
    async def test_refine_prompt_carries_workflow_and_instruction(self):
        provider = MockLLMProvider(response_text=_PLAN_JSON)
        current = {"name": "Old Workflow", "graph": {"nodes": [], "edges": []}}

        result = await refine_workflow(
            "also email the result to me",
            current,
            llm_provider=provider,
        )

        assert result.workflow["name"] == "Summarizer"
        prompt = provider.prompts[0]
        assert "Old Workflow" in prompt
        assert "also email the result to me" in prompt
        assert "complete updated plan" in prompt


class TestBuildability:
    def test_compiled_workflow_builds(self):
        from genxai.builder.compiler import compile_plan

        assert check_workflow_builds(compile_plan(_plan())) is None

    def test_broken_workflow_reports_error(self):
        broken = {
            "name": "Broken",
            "graph": {
                "nodes": [{"id": "start", "type": "input"}],
                "edges": [{"from": "start", "to": "ghost"}],
            },
        }
        error = check_workflow_builds(broken)
        assert error is not None

    @pytest.mark.asyncio
    async def test_eval_report_includes_build_rate(self):
        provider = MockLLMProvider(response_text=_PLAN_JSON)

        report = await evaluate_generation(llm_provider=provider, prompts=["summarize text"])

        assert report.build_rate == 1.0
        assert report.outcomes[0].buildable is True
        assert "buildable: 1/1" in report.summary()


class TestGenerationMetrics:
    @pytest.mark.asyncio
    async def test_metrics_recorded_on_success(self):
        from genxai.observability.metrics import get_metrics_collector

        collector = get_metrics_collector()
        tags = {"pipeline": "single"}
        before = collector.get_counter("workflow_generation.success", tags=tags)

        provider = MockLLMProvider(response_text=_PLAN_JSON)
        await generate_workflow("summarize text", llm_provider=provider)

        assert collector.get_counter("workflow_generation.success", tags=tags) == before + 1
