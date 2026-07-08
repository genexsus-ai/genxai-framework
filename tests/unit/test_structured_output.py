"""Unit tests for the structured LLM output utility."""

import pytest
from pydantic import BaseModel, Field

from genxai.utils.structured import (
    StructuredOutputError,
    generate_structured,
    parse_json_loosely,
)
from tests.utils.mock_llm import MockLLMProvider


class Recipe(BaseModel):
    title: str
    servings: int = Field(..., ge=1)


@pytest.mark.asyncio
async def test_generate_structured_valid_json() -> None:
    provider = MockLLMProvider(response_text='{"title": "Soup", "servings": 4}')

    result = await generate_structured(
        llm_provider=provider,
        prompt="Give me a recipe",
        response_model=Recipe,
    )

    assert result.output == Recipe(title="Soup", servings=4)
    assert result.attempts == 1
    assert result.repaired is False


@pytest.mark.asyncio
async def test_generate_structured_fenced_json_with_prose() -> None:
    provider = MockLLMProvider(
        response_text=('Here you go!\n```json\n{"title": "Stew", "servings": 2}\n```\nEnjoy.')
    )

    result = await generate_structured(
        llm_provider=provider,
        prompt="Give me a recipe",
        response_model=Recipe,
    )

    assert result.output.title == "Stew"
    assert result.attempts == 1


@pytest.mark.asyncio
async def test_generate_structured_repairs_after_validation_error() -> None:
    provider = MockLLMProvider(
        responses=[
            '{"title": "Bread"}',  # missing servings -> validation error
            '{"title": "Bread", "servings": 1}',
        ]
    )

    result = await generate_structured(
        llm_provider=provider,
        prompt="Give me a recipe",
        response_model=Recipe,
    )

    assert result.output.servings == 1
    assert result.attempts == 2
    assert result.repaired is True
    # The repair prompt must feed the validation error back to the LLM.
    assert "did not validate" in provider.prompts[1]
    assert "servings" in provider.prompts[1]


@pytest.mark.asyncio
async def test_generate_structured_raises_after_exhausted_attempts() -> None:
    provider = MockLLMProvider(responses=["not json at all"])

    with pytest.raises(StructuredOutputError) as exc_info:
        await generate_structured(
            llm_provider=provider,
            prompt="Give me a recipe",
            response_model=Recipe,
            max_repair_attempts=1,
        )

    assert exc_info.value.attempts == 2
    assert exc_info.value.last_response == "not json at all"


@pytest.mark.asyncio
async def test_generate_structured_includes_schema_in_prompt() -> None:
    provider = MockLLMProvider(response_text='{"title": "Pie", "servings": 6}')

    await generate_structured(
        llm_provider=provider,
        prompt="Give me a recipe",
        response_model=Recipe,
    )

    assert "JSON schema" in provider.prompts[0]
    assert "servings" in provider.prompts[0]


def test_parse_json_loosely_variants() -> None:
    assert parse_json_loosely('{"a": 1}') == {"a": 1}
    assert parse_json_loosely("prefix {'a': 1} suffix") == {"a": 1}
    assert parse_json_loosely("```json\n[1, 2]\n```") == [1, 2]
    assert parse_json_loosely("no json here") is None
    assert parse_json_loosely("") is None
