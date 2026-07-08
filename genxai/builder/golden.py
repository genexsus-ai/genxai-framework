"""Golden examples: natural-language request → workflow document pairs.

Curated few-shot anchors for the workflow-generation crew. Each example pairs
a user prompt with a workflow dict in the canonical YAML DSL shape accepted by
``genxai.core.graph.workflow_io._validate_workflow_schema`` and executable by
``WorkflowExecutor``. Executor-recognized settings live under each node's
``config`` block; agent nodes reference definitions in ``agents`` via
``agent:``.
"""

from __future__ import annotations

import yaml
from pydantic import BaseModel, Field

from genxai.core.graph.workflow_io import _validate_workflow_schema


class GoldenExample(BaseModel):
    """One prompt → workflow exemplar."""

    id: str
    prompt: str = Field(..., description="The natural-language request")
    workflow: dict = Field(..., description="The workflow document it should produce")

    def to_prompt_block(self) -> str:
        """Render as a few-shot block for planner prompts."""
        workflow_yaml = yaml.safe_dump(self.workflow, sort_keys=False).strip()
        return f"### Request\n{self.prompt}\n### Workflow\n```yaml\n{workflow_yaml}\n```"


_RAW_EXAMPLES: list[dict] = [
    {
        "id": "summarize_document",
        "prompt": "Summarize any text I give you into a short executive summary.",
        "workflow": {
            "name": "Document Summarizer",
            "description": "Produce an executive summary of the input text",
            "agents": [
                {
                    "id": "summarizer",
                    "role": "Summarization Specialist",
                    "goal": "Condense input text into a clear executive summary",
                    "llm_model": "claude-sonnet-5",
                }
            ],
            "graph": {
                "nodes": [
                    {"id": "start", "type": "input"},
                    {
                        "id": "summarize",
                        "type": "agent",
                        "agent": "summarizer",
                        "config": {"task": "Summarize: {{ input.text }}"},
                    },
                    {"id": "end", "type": "output"},
                ],
                "edges": [
                    {"from": "start", "to": "summarize"},
                    {"from": "summarize", "to": "end"},
                ],
            },
        },
    },
    {
        "id": "research_and_report",
        "prompt": (
            "Research a topic on the web and write a report about it, "
            "with sources gathered before writing."
        ),
        "workflow": {
            "name": "Research and Report",
            "description": "Gather web sources on a topic, then write a report from them",
            "agents": [
                {
                    "id": "researcher",
                    "role": "Research Specialist",
                    "goal": "Collect relevant, current information on the topic",
                    "llm_model": "claude-sonnet-5",
                    "tools": ["web_scraper", "http_client"],
                },
                {
                    "id": "writer",
                    "role": "Report Writer",
                    "goal": "Turn research findings into a structured report",
                    "llm_model": "claude-sonnet-5",
                },
            ],
            "graph": {
                "nodes": [
                    {"id": "start", "type": "input"},
                    {
                        "id": "research",
                        "type": "agent",
                        "agent": "researcher",
                        "config": {"task": "Research: {{ input.topic }}"},
                    },
                    {
                        "id": "write",
                        "type": "agent",
                        "agent": "writer",
                        "config": {"task": "Write a report from: {{ research.output }}"},
                    },
                    {"id": "end", "type": "output"},
                ],
                "edges": [
                    {"from": "start", "to": "research"},
                    {"from": "research", "to": "write"},
                    {"from": "write", "to": "end"},
                ],
            },
        },
    },
    {
        "id": "parallel_market_analysis",
        "prompt": (
            "Analyze a company from both a financial and a competitive angle "
            "at the same time, then combine both analyses into one assessment."
        ),
        "workflow": {
            "name": "Parallel Market Analysis",
            "description": "Financial and competitive analyses run in parallel, then merge",
            "agents": [
                {
                    "id": "financial_analyst",
                    "role": "Financial Analyst",
                    "goal": "Assess the company's financial position",
                    "llm_model": "claude-sonnet-5",
                },
                {
                    "id": "competitive_analyst",
                    "role": "Competitive Analyst",
                    "goal": "Assess the company's market and competitors",
                    "llm_model": "claude-sonnet-5",
                },
                {
                    "id": "synthesizer",
                    "role": "Synthesis Specialist",
                    "goal": "Merge the analyses into one assessment",
                    "llm_model": "claude-sonnet-5",
                },
            ],
            "graph": {
                "nodes": [
                    {"id": "start", "type": "input"},
                    {"id": "financial", "type": "agent", "agent": "financial_analyst"},
                    {"id": "competitive", "type": "agent", "agent": "competitive_analyst"},
                    {"id": "synthesize", "type": "agent", "agent": "synthesizer"},
                    {"id": "end", "type": "output"},
                ],
                "edges": [
                    {"from": "start", "to": "financial", "parallel": True},
                    {"from": "start", "to": "competitive", "parallel": True},
                    {"from": "financial", "to": "synthesize"},
                    {"from": "competitive", "to": "synthesize"},
                    {"from": "synthesize", "to": "end"},
                ],
            },
        },
    },
    {
        "id": "support_ticket_router",
        "prompt": (
            "Triage incoming support tickets: classify each ticket, answer the "
            "routine ones automatically, and escalate urgent ones."
        ),
        "workflow": {
            "name": "Support Ticket Router",
            "description": "Classify tickets, auto-answer routine ones, escalate urgent ones",
            "agents": [
                {
                    "id": "classifier",
                    "role": "Ticket Classifier",
                    "goal": "Label each ticket as routine or urgent",
                    "llm_model": "claude-haiku-4-5",
                },
                {
                    "id": "responder",
                    "role": "Support Responder",
                    "goal": "Draft answers for routine tickets",
                    "llm_model": "claude-sonnet-5",
                    "tools": ["email_sender"],
                },
                {
                    "id": "escalator",
                    "role": "Escalation Handler",
                    "goal": "Summarize urgent tickets for the on-call engineer",
                    "llm_model": "claude-sonnet-5",
                    "tools": ["email_sender"],
                },
            ],
            "graph": {
                "nodes": [
                    {"id": "start", "type": "input"},
                    {"id": "classify", "type": "agent", "agent": "classifier"},
                    {"id": "respond", "type": "agent", "agent": "responder"},
                    {"id": "escalate", "type": "agent", "agent": "escalator"},
                    {"id": "end", "type": "output"},
                ],
                "edges": [
                    {"from": "start", "to": "classify"},
                    {"from": "classify", "to": "respond", "condition": "category == 'routine'"},
                    {"from": "classify", "to": "escalate", "condition": "category == 'urgent'"},
                    {"from": "respond", "to": "end"},
                    {"from": "escalate", "to": "end"},
                ],
            },
        },
    },
    {
        "id": "drafting_with_review_team",
        "prompt": (
            "Write a blog post where one agent drafts it and another critiques "
            "it, iterating until the critic accepts the draft."
        ),
        "workflow": {
            "name": "Drafting with Review Team",
            "description": "A writer/critic team iterates on a blog post until accepted",
            "agents": [],
            "graph": {
                "nodes": [
                    {"id": "start", "type": "input"},
                    {
                        "id": "draft_and_review",
                        "type": "flow",
                        "config": {
                            "flow_type": "critic_review",
                            "agents": [
                                {
                                    "role": "Blog Writer",
                                    "goal": "Draft an engaging blog post",
                                    "llm_model": "claude-sonnet-5",
                                },
                                {
                                    "role": "Editor",
                                    "goal": "Critique drafts until publication quality",
                                    "llm_model": "claude-sonnet-5",
                                },
                            ],
                            "params": {"max_iterations": 3},
                            "task": "Write a blog post about {{ input.topic }}",
                        },
                    },
                    {"id": "end", "type": "output"},
                ],
                "edges": [
                    {"from": "start", "to": "draft_and_review"},
                    {"from": "draft_and_review", "to": "end"},
                ],
            },
        },
    },
]


def golden_examples() -> list[GoldenExample]:
    """Load and schema-validate all golden examples."""
    examples = []
    for raw in _RAW_EXAMPLES:
        _validate_workflow_schema(raw["workflow"])
        examples.append(GoldenExample(**raw))
    return examples


def render_golden_examples_for_prompt(limit: int | None = None) -> str:
    """Render examples as few-shot blocks for the planner prompt."""
    examples = golden_examples()
    if limit is not None:
        examples = examples[:limit]
    return "\n\n".join(example.to_prompt_block() for example in examples)
