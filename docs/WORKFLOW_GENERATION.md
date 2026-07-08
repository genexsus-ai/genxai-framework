# Natural-Language Workflow Generation

Turn a plain-English request into an executable GenXAI workflow, built by a
multi-agent crew: a **planner** designs the workflow, a **delegator** routes
the plan's steps to specialist **workers** (agent designer, node designer),
and a **reviewer** judges the result before it compiles. Everything lives in
`genxai.builder`, is exposed on the CLI, and powers the Workflow Studio's
"✨ Generate…" button.

## Quick start

```bash
export ANTHROPIC_API_KEY=...

# Single-shot planner
genxai workflow generate "Research a topic on the web, then write a report"

# Full multi-agent crew, saved to a file
genxai workflow generate "Classify tickets; answer routine, escalate urgent" \
    --crew --output triage.yaml

# Run what was generated
genxai workflow run triage.yaml --input '{"ticket": "My login is broken"}'

# Measure generation quality over the built-in corpus
genxai workflow eval-generation --crew
```

## Library API

```python
from genxai.builder import (
    build_capability_catalog, generate_workflow, crew_generate_workflow,
    refine_workflow, evaluate_generation, GenerationMemory,
)
from genxai.llm.factory import LLMProviderFactory

provider = LLMProviderFactory.create_provider(model="claude-sonnet-5")
memory = GenerationMemory("./generation_memory.jsonl")  # optional learning

result = await crew_generate_workflow(
    "Summarize any text I give you and email me the summary",
    llm_provider=provider,
    memory=memory,                      # recalls similar accepted plans
    on_event=lambda stage, data: print(stage, data),  # progress stream
)
result.workflow          # dict in the workflow YAML DSL — runs on WorkflowExecutor
result.plan              # the WorkflowPlan (steps, trigger, open_questions)
result.review            # reviewer verdict (crew only)
result.generation_id     # memory record id; mark accepted when the user keeps it

# Iterate on an existing workflow
updated = await refine_workflow(
    "also post the summary to Slack",
    result.workflow,
    llm_provider=provider,
    generate_fn=crew_generate_workflow,
)

# User kept the draft → future similar requests learn from it
memory.mark_accepted(result.generation_id)
```

## How it works

```
request ──► planner ──► WorkflowPlan          (structured output + catalog grounding)
                │
                ▼
            delegator ──► DelegationPlan       (falls back to deterministic routing)
                │
     ┌──────────┴──────────┐   concurrent dependency waves
     ▼                     ▼
 agent designer       node designer            (roles/goals/backstory/tools │ capabilities/params/conditions)
     └──────────┬──────────┘
                ▼
          apply_specs (pure code merge) ──► reviewer ──► compile_plan ──► workflow dict
                                                │ rejected                     │ CompileError
                                                └──── feedback → re-plan ◄─────┘
```

Key properties:

- **Grounded**: the planner and workers only see the `CapabilityCatalog`
  (registered tools, flow patterns, injected connector/MCP inventories);
  hallucinated capability names trigger bounded re-planning, and unknown
  names in worker output are dropped with warnings.
- **Always valid**: every returned workflow passed
  `_validate_workflow_schema`; `check_workflow_builds()` additionally proves
  it constructs an executable graph (used by the eval harness).
- **Structured end-to-end**: all LLM outputs are Pydantic-validated via
  `genxai.utils.structured.generate_structured` with repair retries.
- **Observable**: `on_event(stage, data)` streams progress; the metrics
  collector records `workflow_generation.requests/success/failure/duration/
  llm_attempts` tagged by pipeline.
- **Learning**: `GenerationMemory` records every generation; accepted drafts
  are weighted up when recalling examples for similar future prompts.

## The pieces (all in `genxai/builder/`)

| Module | What it does |
|---|---|
| `schemas.py` | `WorkflowPlan`/`PlanStep` (planner contract), `DelegationPlan`/`WorkPacket` (delegator contract) with cycle/reference validation |
| `catalog.py` | `build_capability_catalog()` — the grounded inventory, extensible via `extra_sections` |
| `planner.py` | `plan_workflow()` — NL → plan, with grounding retries and memory recall |
| `compiler.py` | `compile_plan()` — deterministic plan → workflow dict (no LLM) |
| `crew.py` | `crew_generate_workflow()` — delegator + designer workers + reviewer |
| `generator.py` | `generate_workflow()` baseline, `refine_workflow()`, `evaluate_generation()`, `check_workflow_builds()` |
| `memory.py` | `GenerationMemory` — episodic prompt→plan store with accepted-first recall |
| `golden.py` | Curated prompt→workflow exemplars |

There is also a general-purpose **`DelegatorFlow`** in `genxai/flows/delegator.py`
(`FLOW_TYPES["delegator_worker"]`): a delegator agent routes structured work
packets to tagged worker agents in dependency waves — usable as an agent-team
node in any workflow, independent of generation.

## Workflow Studio integration

- `POST /api/v1/workflows/generate` — prompt → draft `WorkflowDoc` (auto-laid-out,
  validation report included). Set `current_workflow` to refine an existing doc.
- `POST /api/v1/workflows/generate/stream` — same, as SSE progress events.
- `POST /api/v1/workflows/generate/{generation_id}/accept` — mark a draft
  accepted (the UI calls this when a generated draft is saved).

In the UI: **✨ Generate…** in the toolbar. With a workflow on the canvas, the
dialog offers "refine the current canvas workflow" mode.

## Evals

`evaluate_generation()` reports two rates over a prompt corpus: **validity**
(schema-valid workflow produced) and **buildability** (constructs an
executable graph). Compare pipelines:

```bash
genxai workflow eval-generation            # single-shot baseline
genxai workflow eval-generation --crew     # multi-agent crew
```

See also: `examples/code/nl_workflow_generation_example.py`.
