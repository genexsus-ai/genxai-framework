"""Workflow CLI commands."""

from pathlib import Path
from typing import Any

import click

from genxai.core.graph import load_workflow_yaml, register_workflow_agents
from genxai.core.graph.executor import WorkflowExecutor


@click.group()
def workflow() -> None:
    """Manage and run workflows."""


@workflow.command("run")
@click.argument("workflow_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--input", "input_payload", required=True, help="JSON input payload")
def run_workflow(workflow_path: Path, input_payload: str) -> None:
    """Run a workflow from a YAML file."""
    import json

    try:
        workflow = load_workflow_yaml(workflow_path)
    except (ValueError, FileNotFoundError) as exc:
        raise click.ClickException(str(exc)) from exc
    register_workflow_agents(workflow)

    nodes = _build_nodes_from_workflow(workflow)
    edges = _build_edges_from_workflow(workflow)

    executor = WorkflowExecutor()
    input_data = json.loads(input_payload)
    shared_memory = workflow.get("memory", {}).get("shared", False)

    result = _run_executor(executor, nodes, edges, input_data, shared_memory=shared_memory)
    click.echo(json.dumps(result, indent=2))


@workflow.command("generate")
@click.argument("request")
@click.option(
    "--model",
    default="claude-sonnet-5",
    help="LLM used for planning and assigned to generated agents",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write the workflow YAML here instead of stdout",
)
@click.option(
    "--crew",
    is_flag=True,
    default=False,
    help="Use the multi-agent crew (planner + delegator + designer workers + reviewer)",
)
def generate_workflow_cmd(request: str, model: str, output_path: Path | None, crew: bool) -> None:
    """Generate a workflow YAML from a natural-language REQUEST."""
    import asyncio

    import yaml

    from genxai.builder.crew import crew_generate_workflow
    from genxai.builder.generator import generate_workflow
    from genxai.llm.factory import LLMProviderFactory

    generate_fn = crew_generate_workflow if crew else generate_workflow

    async def _generate():
        provider = LLMProviderFactory.create_provider(model=model)
        try:
            return await generate_fn(request, llm_provider=provider, default_model=model)
        finally:
            await provider.aclose()

    try:
        result = asyncio.run(_generate())
    except Exception as exc:
        raise click.ClickException(f"workflow generation failed: {exc}") from exc

    yaml_text = yaml.safe_dump({"workflow": result.workflow}, sort_keys=False)
    if output_path:
        output_path.write_text(yaml_text)
        click.echo(f"Wrote workflow '{result.workflow['name']}' to {output_path}")
    else:
        click.echo(yaml_text)

    for question in result.open_questions:
        click.echo(
            f"Open question: {question.question}"
            + (f" (assumed: {question.default_assumption})" if question.default_assumption else ""),
            err=True,
        )
    review = getattr(result, "review", None)
    if review is not None and not review.approved:
        for issue in review.issues:
            click.echo(f"Reviewer concern: {issue}", err=True)


@workflow.command("eval-generation")
@click.option(
    "--model",
    default="claude-sonnet-5",
    help="LLM used for planning during the eval",
)
@click.option(
    "--prompt",
    "prompts",
    multiple=True,
    help="Eval prompt (repeatable); defaults to the built-in corpus",
)
@click.option(
    "--crew",
    is_flag=True,
    default=False,
    help="Evaluate the multi-agent crew instead of the single-shot baseline",
)
def eval_generation_cmd(model: str, prompts: tuple[str, ...], crew: bool) -> None:
    """Measure how often NL requests yield schema-valid workflows."""
    import asyncio

    from genxai.builder.crew import crew_generate_workflow
    from genxai.builder.generator import evaluate_generation
    from genxai.llm.factory import LLMProviderFactory

    async def _evaluate():
        provider = LLMProviderFactory.create_provider(model=model)
        try:
            return await evaluate_generation(
                llm_provider=provider,
                prompts=list(prompts) or None,
                generate_fn=crew_generate_workflow if crew else None,
            )
        finally:
            await provider.aclose()

    report = asyncio.run(_evaluate())
    click.echo(report.summary())
    if report.validity_rate < 1.0:
        raise SystemExit(1)


def _build_nodes_from_workflow(workflow: dict[str, Any]):
    nodes = workflow.get("graph", {}).get("nodes", [])
    if not isinstance(nodes, list):
        raise click.ClickException("workflow.graph.nodes must be a list")
    return nodes


def _build_edges_from_workflow(workflow: dict[str, Any]):
    edges = workflow.get("graph", {}).get("edges", [])
    if not isinstance(edges, list):
        raise click.ClickException("workflow.graph.edges must be a list")
    # Map YAML edge keys to executor expectations.
    return [
        {
            "source": edge.get("from"),
            "target": edge.get("to"),
            "condition": edge.get("condition"),
            "parallel": edge.get("parallel", False),
        }
        for edge in edges
    ]


def _run_executor(
    executor: WorkflowExecutor,
    nodes,
    edges,
    input_data,
    shared_memory: bool = False,
):
    import asyncio

    async def _execute():
        return await executor.execute(
            nodes=nodes,
            edges=edges,
            input_data=input_data,
            shared_memory=shared_memory,
        )

    return asyncio.run(_execute())
