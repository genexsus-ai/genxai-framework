"""Unit tests for the golden prompt→workflow examples."""

from genxai.builder.golden import golden_examples, render_golden_examples_for_prompt
from genxai.core.graph.workflow_io import _validate_workflow_schema


def test_all_golden_examples_pass_workflow_schema() -> None:
    examples = golden_examples()

    assert len(examples) >= 5
    for example in examples:
        _validate_workflow_schema(example.workflow)  # raises on invalid
        assert example.prompt
        assert example.workflow["name"]


def test_golden_example_ids_unique() -> None:
    ids = [example.id for example in golden_examples()]
    assert len(ids) == len(set(ids))


def test_golden_examples_cover_key_shapes() -> None:
    """The exemplar set must show sequential, parallel, conditional, and flow shapes."""
    examples = {example.id: example for example in golden_examples()}

    parallel = examples["parallel_market_analysis"].workflow["graph"]["edges"]
    assert any(edge.get("parallel") for edge in parallel)

    conditional = examples["support_ticket_router"].workflow["graph"]["edges"]
    assert any(edge.get("condition") for edge in conditional)

    flow_nodes = [
        node
        for node in examples["drafting_with_review_team"].workflow["graph"]["nodes"]
        if node["type"] == "flow"
    ]
    assert flow_nodes and flow_nodes[0]["config"]["flow_type"] == "critic_review"


def test_render_golden_examples_for_prompt() -> None:
    rendered = render_golden_examples_for_prompt(limit=2)

    assert rendered.count("### Request") == 2
    assert "```yaml" in rendered
    assert "Document Summarizer" in rendered
