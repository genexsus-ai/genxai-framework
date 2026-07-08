"""End-to-end generation pipeline: request → plan → workflow document.

The Phase 1 single-agent baseline: plan with the LLM, compile with pure code,
and when compilation fails, feed the compiler's error back to the planner for
a bounded number of re-plan rounds. Every returned workflow has passed the
canonical schema validation inside ``compile_plan``.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from genxai.builder.catalog import CapabilityCatalog, build_capability_catalog
from genxai.builder.compiler import DEFAULT_MODEL, CompileError, compile_plan
from genxai.builder.planner import plan_workflow
from genxai.builder.schemas import OpenQuestion, WorkflowPlan
from genxai.llm.base import LLMProvider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GenerationResult:
    """A generated workflow with its plan and provenance."""

    workflow: dict[str, Any]
    plan: WorkflowPlan
    llm_attempts: int
    compile_retries: int
    generation_id: str | None = None

    @property
    def open_questions(self) -> list[OpenQuestion]:
        return self.plan.open_questions


class GenerationError(Exception):
    """Raised when no valid workflow could be generated."""

    def __init__(self, message: str, plan: WorkflowPlan | None = None) -> None:
        super().__init__(message)
        self.plan = plan


def emit_event(on_event: Any, stage: str, **data: Any) -> None:
    """Fire a progress callback, never letting a listener break generation."""
    if on_event is None:
        return
    try:
        on_event(stage, data)
    except Exception:  # noqa: BLE001 - observers must not affect the pipeline
        logger.debug("generation progress listener raised", exc_info=True)


def _metrics() -> Any:
    from genxai.observability.metrics import get_metrics_collector

    return get_metrics_collector()


def _record_metrics(
    collector: Any, pipeline: str, *, success: bool, started: float, llm_attempts: int
) -> None:
    """Record generation telemetry; observability must never break generation."""
    try:
        tags = {"pipeline": pipeline}
        collector.increment("workflow_generation.requests", tags=tags)
        collector.increment(
            "workflow_generation.success" if success else "workflow_generation.failure",
            tags=tags,
        )
        collector.timing("workflow_generation.duration", time.monotonic() - started, tags=tags)
        collector.histogram("workflow_generation.llm_attempts", llm_attempts, tags=tags)
    except Exception:  # noqa: BLE001
        logger.debug("generation metrics recording failed", exc_info=True)


def _record_generation(memory: Any, request: str, plan: WorkflowPlan) -> str | None:
    if memory is None:
        return None
    try:
        return memory.record(request, plan)
    except Exception:  # noqa: BLE001 - memory must never break generation
        logger.warning("Generation-memory record failed", exc_info=True)
        return None


def check_workflow_builds(workflow: dict[str, Any]) -> str | None:
    """Verify a workflow document builds into an executable graph.

    Goes beyond schema validation: instantiates agents from the nodes,
    constructs the executor graph, and runs its structural validation.
    Returns the error message, or None when the workflow builds. Note:
    registers the workflow's agents in the global AgentRegistry as a side
    effect (same as running it would).
    """
    from genxai.core.graph.executor import WorkflowExecutor

    nodes = workflow["graph"]["nodes"]
    edges = [
        {
            "source": edge["from"],
            "target": edge["to"],
            "condition": edge.get("condition"),
            "parallel": edge.get("parallel", False),
        }
        for edge in workflow["graph"]["edges"]
    ]
    try:
        executor = WorkflowExecutor()
        executor._create_agents_from_nodes(nodes)
        graph = executor._build_graph(nodes, edges)
        graph.validate()
        return None
    except Exception as exc:  # noqa: BLE001 - any failure means "does not build"
        return f"{type(exc).__name__}: {exc}"


async def generate_workflow(
    request: str,
    *,
    llm_provider: LLMProvider,
    catalog: CapabilityCatalog | None = None,
    default_model: str = DEFAULT_MODEL,
    max_compile_retries: int = 1,
    on_event: Any = None,
    memory: Any = None,
    **planner_kwargs: Any,
) -> GenerationResult:
    """Generate a schema-valid workflow document from a natural-language request.

    ``on_event(stage, data)`` receives progress notifications (``planning``,
    ``planned``, ``compile_failed``, ``compiled``). ``memory`` is an optional
    ``GenerationMemory``: similar past plans feed the planner, and the result
    is recorded (its id lands in ``GenerationResult.generation_id``).
    """
    if catalog is None:
        catalog = build_capability_catalog()

    collector = _metrics()
    started = time.monotonic()
    feedback: str | None = None
    plan: WorkflowPlan | None = None
    llm_attempts = 0

    for compile_round in range(max_compile_retries + 1):
        emit_event(on_event, "planning", round=compile_round + 1)
        planned = await plan_workflow(
            request,
            llm_provider=llm_provider,
            catalog=catalog,
            feedback=feedback,
            memory=memory,
            **planner_kwargs,
        )
        plan = planned.plan
        llm_attempts += planned.attempts
        emit_event(
            on_event,
            "planned",
            name=plan.name,
            steps=len(plan.steps),
            open_questions=len(plan.open_questions),
        )

        try:
            workflow = compile_plan(plan, default_model=default_model)
        except CompileError as exc:
            feedback = str(exc)
            emit_event(on_event, "compile_failed", error=str(exc), round=compile_round + 1)
            logger.warning(
                "Compilation failed for %r (round %d): %s",
                request[:80],
                compile_round + 1,
                exc,
            )
            continue

        emit_event(on_event, "compiled", nodes=len(workflow["graph"]["nodes"]))
        generation_id = _record_generation(memory, request, plan)
        _record_metrics(
            collector, "single", success=True, started=started, llm_attempts=llm_attempts
        )
        return GenerationResult(
            workflow=workflow,
            plan=plan,
            llm_attempts=llm_attempts,
            compile_retries=compile_round,
            generation_id=generation_id,
        )

    _record_metrics(collector, "single", success=False, started=started, llm_attempts=llm_attempts)
    raise GenerationError(
        f"could not compile a valid workflow after {max_compile_retries + 1} "
        f"planning rounds: {feedback}",
        plan=plan,
    )


async def refine_workflow(
    instruction: str,
    current_workflow: dict[str, Any],
    *,
    llm_provider: LLMProvider,
    generate_fn: Any = None,
    **generate_kwargs: Any,
) -> Any:
    """Regenerate an existing workflow according to a user instruction.

    The current workflow (library dict or Studio doc dump) is given to the
    planner as context; the pipeline then produces a complete updated
    workflow, not a diff. ``generate_fn`` selects the pipeline (default
    :func:`generate_workflow`; pass ``crew_generate_workflow`` for the crew).
    """
    request = (
        "The user wants their EXISTING workflow modified.\n"
        f"Existing workflow (JSON):\n{json.dumps(current_workflow, indent=2, default=str)}\n\n"
        f"Modification instruction:\n{instruction}\n\n"
        "Produce the complete updated plan — keep every part of the existing "
        "workflow that the instruction does not change (same step ids, "
        "capabilities, and structure), and change only what it asks for."
    )
    if generate_fn is None:
        generate_fn = generate_workflow
    return await generate_fn(request, llm_provider=llm_provider, **generate_kwargs)


@dataclass
class EvalOutcome:
    """Result of generating a workflow for one eval prompt."""

    prompt: str
    ok: bool
    workflow_name: str | None = None
    error: str | None = None
    llm_attempts: int = 0
    buildable: bool | None = None
    build_error: str | None = None


@dataclass
class EvalReport:
    """Aggregate results over an eval prompt corpus."""

    outcomes: list[EvalOutcome] = field(default_factory=list)

    @property
    def validity_rate(self) -> float:
        if not self.outcomes:
            return 0.0
        return sum(1 for outcome in self.outcomes if outcome.ok) / len(self.outcomes)

    @property
    def build_rate(self) -> float:
        if not self.outcomes:
            return 0.0
        return sum(1 for outcome in self.outcomes if outcome.buildable) / len(self.outcomes)

    def summary(self) -> str:
        passed = sum(1 for outcome in self.outcomes if outcome.ok)
        built = sum(1 for outcome in self.outcomes if outcome.buildable)
        lines = [
            f"valid workflows: {passed}/{len(self.outcomes)} ({self.validity_rate:.0%}), "
            f"buildable: {built}/{len(self.outcomes)} ({self.build_rate:.0%})"
        ]
        for outcome in self.outcomes:
            if not outcome.ok:
                status = f"FAIL ({outcome.error})"
            elif outcome.buildable is False:
                status = f"VALID-BUT-UNBUILDABLE ({outcome.build_error})"
            else:
                status = "PASS"
            lines.append(f"  [{status}] {outcome.prompt[:70]}")
        return "\n".join(lines)


# A small, shape-diverse corpus: sequential, parallel, conditional, looping,
# tool-using, team-flow, and trigger-implying requests.
DEFAULT_EVAL_PROMPTS: list[str] = [
    "Summarize any text I give you into three bullet points.",
    "Research a topic on the web, then write a report about the findings.",
    "Analyze a company from a financial and a competitive angle at the same "
    "time, then merge both into one assessment.",
    "Classify incoming support tickets and answer routine ones automatically, "
    "but escalate urgent ones by email.",
    "Draft a blog post with one agent writing and another critiquing until "
    "the draft is accepted.",
    "Every morning, fetch the top tech headlines and email me a digest.",
    "Keep refining a product description until it passes a quality check, at " "most five times.",
    "Extract tables from a web page, transform them to CSV, and email the " "result to me.",
]


async def evaluate_generation(
    *,
    llm_provider: LLMProvider,
    prompts: list[str] | None = None,
    catalog: CapabilityCatalog | None = None,
    generate_fn: Any = None,
    **generate_kwargs: Any,
) -> EvalReport:
    """Run a generation pipeline over a prompt corpus and report validity.

    ``generate_fn`` selects the pipeline (default :func:`generate_workflow`;
    pass ``crew_generate_workflow`` to measure the multi-agent crew against
    the single-shot baseline).
    """
    if prompts is None:
        prompts = DEFAULT_EVAL_PROMPTS
    if catalog is None:
        catalog = build_capability_catalog()
    if generate_fn is None:
        generate_fn = generate_workflow

    report = EvalReport()
    for prompt in prompts:
        try:
            result = await generate_fn(
                prompt,
                llm_provider=llm_provider,
                catalog=catalog,
                **generate_kwargs,
            )
            build_error = check_workflow_builds(result.workflow)
            report.outcomes.append(
                EvalOutcome(
                    prompt=prompt,
                    ok=True,
                    workflow_name=result.workflow["name"],
                    llm_attempts=result.llm_attempts,
                    buildable=build_error is None,
                    build_error=build_error,
                )
            )
        except Exception as exc:  # noqa: BLE001 - eval must survive any failure mode
            report.outcomes.append(
                EvalOutcome(prompt=prompt, ok=False, error=f"{type(exc).__name__}: {exc}")
            )
    return report
