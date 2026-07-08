"""Tests for the `workflow generate` and `workflow eval-generation` CLI commands."""

import json
from pathlib import Path

import yaml
from click.testing import CliRunner

from genxai.cli.commands.workflow import workflow
from genxai.llm.factory import LLMProviderFactory
from tests.utils.mock_llm import MockLLMProvider

_PLAN_JSON = json.dumps(
    {
        "name": "Generated Workflow",
        "description": "A generated workflow",
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
        "open_questions": [
            {
                "question": "How long should the summary be?",
                "why_it_matters": "Length changes the prompt",
                "default_assumption": "Three bullet points",
            }
        ],
    }
)


def _mock_provider_factory(monkeypatch) -> None:
    monkeypatch.setattr(
        LLMProviderFactory,
        "create_provider",
        classmethod(lambda cls, model, **kwargs: MockLLMProvider(response_text=_PLAN_JSON)),
    )


def test_workflow_generate_to_stdout(monkeypatch) -> None:
    _mock_provider_factory(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(workflow, ["generate", "Summarize any text I give you"])

    assert result.exit_code == 0, result.output
    parsed = yaml.safe_load(result.stdout)
    assert parsed["workflow"]["name"] == "Generated Workflow"
    assert "How long should the summary be?" in result.stderr


def test_workflow_generate_with_crew(monkeypatch) -> None:
    crew_responses = [
        _PLAN_JSON,
        json.dumps(
            {
                "packets": [
                    {
                        "id": "p_agents",
                        "worker": "agent_designer",
                        "objective": "Design the summarizer",
                        "step_ids": ["summarize"],
                    }
                ]
            }
        ),
        json.dumps(
            {
                "specs": [
                    {
                        "step_id": "summarize",
                        "role": "Executive Summarizer",
                        "goal": "Summarize crisply",
                        "backstory": "",
                        "tools": [],
                    }
                ]
            }
        ),
        json.dumps({"approved": True, "issues": []}),
    ]
    monkeypatch.setattr(
        LLMProviderFactory,
        "create_provider",
        classmethod(lambda cls, model, **kwargs: MockLLMProvider(responses=crew_responses)),
    )

    runner = CliRunner()
    result = runner.invoke(workflow, ["generate", "Summarize text", "--crew"])

    assert result.exit_code == 0, result.output
    parsed = yaml.safe_load(result.stdout)
    assert parsed["workflow"]["agents"][0]["role"] == "Executive Summarizer"


def test_workflow_generate_to_file(monkeypatch, tmp_path: Path) -> None:
    _mock_provider_factory(monkeypatch)
    output = tmp_path / "generated.yaml"

    runner = CliRunner()
    result = runner.invoke(workflow, ["generate", "Summarize text", "--output", str(output)])

    assert result.exit_code == 0, result.output
    parsed = yaml.safe_load(output.read_text())
    assert parsed["workflow"]["graph"]["nodes"][1]["id"] == "summarize"


def test_workflow_generate_failure_is_click_error(monkeypatch) -> None:
    monkeypatch.setattr(
        LLMProviderFactory,
        "create_provider",
        classmethod(lambda cls, model, **kwargs: MockLLMProvider(response_text="garbage")),
    )

    runner = CliRunner()
    result = runner.invoke(workflow, ["generate", "Summarize text"])

    assert result.exit_code != 0
    assert "workflow generation failed" in result.output


def test_workflow_eval_generation(monkeypatch) -> None:
    _mock_provider_factory(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(
        workflow,
        ["eval-generation", "--prompt", "Summarize text", "--prompt", "Write a report"],
    )

    assert result.exit_code == 0, result.output
    assert "valid workflows: 2/2" in result.output
