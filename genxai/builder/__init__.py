"""Natural-language workflow generation building blocks.

The planner → delegator → worker generation crew's foundations: domain
schemas (`WorkflowPlan`, `DelegationPlan`), the capability catalog the crew
is grounded in, golden prompt→workflow exemplars, the planner, the
plan→workflow compiler, and the end-to-end generation pipeline with its eval
harness.
"""

from genxai.builder.catalog import (
    CapabilityCatalog,
    CapabilityEntry,
    build_capability_catalog,
)
from genxai.builder.compiler import CompileError, compile_plan, compile_plan_to_yaml
from genxai.builder.crew import (
    AgentSpec,
    CrewGenerationResult,
    NodeSpec,
    ReviewVerdict,
    crew_generate_workflow,
)
from genxai.builder.generator import (
    DEFAULT_EVAL_PROMPTS,
    EvalOutcome,
    EvalReport,
    GenerationError,
    GenerationResult,
    check_workflow_builds,
    evaluate_generation,
    generate_workflow,
    refine_workflow,
)
from genxai.builder.golden import (
    GoldenExample,
    golden_examples,
    render_golden_examples_for_prompt,
)
from genxai.builder.memory import GenerationMemory, GenerationRecord
from genxai.builder.planner import (
    PlannerResult,
    PlanningError,
    build_planner_prompt,
    plan_workflow,
)
from genxai.builder.schemas import (
    DelegationPlan,
    OpenQuestion,
    PlanStep,
    TeamMember,
    TriggerSpec,
    WorkflowPlan,
    WorkPacket,
)

__all__ = [
    "DEFAULT_EVAL_PROMPTS",
    "AgentSpec",
    "CapabilityCatalog",
    "CapabilityEntry",
    "CompileError",
    "CrewGenerationResult",
    "DelegationPlan",
    "NodeSpec",
    "ReviewVerdict",
    "crew_generate_workflow",
    "EvalOutcome",
    "EvalReport",
    "GenerationError",
    "GenerationMemory",
    "GenerationRecord",
    "GenerationResult",
    "GoldenExample",
    "OpenQuestion",
    "PlanStep",
    "PlannerResult",
    "PlanningError",
    "TeamMember",
    "TriggerSpec",
    "WorkPacket",
    "WorkflowPlan",
    "build_capability_catalog",
    "build_planner_prompt",
    "check_workflow_builds",
    "compile_plan",
    "compile_plan_to_yaml",
    "evaluate_generation",
    "generate_workflow",
    "golden_examples",
    "plan_workflow",
    "refine_workflow",
    "render_golden_examples_for_prompt",
]
