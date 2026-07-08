"""Natural-language workflow generation with the planner→delegator→worker crew.

Requires an LLM API key (ANTHROPIC_API_KEY or OPENAI_API_KEY). The crew plans
the workflow, routes design work to specialist workers, reviews the result,
and compiles it into a workflow document you can run with WorkflowExecutor or
`genxai workflow run`.

See docs/WORKFLOW_GENERATION.md for the full guide.
"""

import asyncio
import os

import yaml

from genxai.builder import (
    GenerationMemory,
    check_workflow_builds,
    crew_generate_workflow,
)
from genxai.llm.factory import LLMProviderFactory

REQUEST = (
    "Classify incoming support tickets, answer routine ones automatically, "
    "and escalate urgent ones by email."
)

MODEL = "claude-sonnet-5" if os.getenv("ANTHROPIC_API_KEY") else "gpt-4o"


def show_progress(stage: str, data: dict) -> None:
    print(f"  [{stage}] {data}")


async def main() -> None:
    if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAI_API_KEY")):
        print("Set ANTHROPIC_API_KEY or OPENAI_API_KEY to run this example.")
        return

    provider = LLMProviderFactory.create_provider(model=MODEL)
    memory = GenerationMemory("./generation_memory.jsonl")

    print(f"Request: {REQUEST}\n\nCrew at work:")
    try:
        result = await crew_generate_workflow(
            REQUEST,
            llm_provider=provider,
            default_model=MODEL,
            memory=memory,
            on_event=show_progress,
        )
    finally:
        await provider.aclose()

    print("\nGenerated workflow:\n")
    print(yaml.safe_dump({"workflow": result.workflow}, sort_keys=False))

    if result.review is not None:
        verdict = "approved" if result.review.approved else "rejected"
        print(f"Reviewer: {verdict} {result.review.issues or ''}")
    for question in result.plan.open_questions:
        print(f"Open question: {question.question}")

    build_error = check_workflow_builds(result.workflow)
    print(f"Builds into an executable graph: {'yes' if build_error is None else build_error}")

    # If you keep the draft, tell the memory — similar future requests will
    # use this plan as a grounded example.
    if result.generation_id:
        memory.mark_accepted(result.generation_id)
        print(f"Recorded and accepted as {result.generation_id}")


if __name__ == "__main__":
    asyncio.run(main())
