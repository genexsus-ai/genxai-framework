"""Structured LLM output: generate a response validated against a Pydantic model.

Generalizes the safe-JSON parsing strategy from ``genxai.utils.llm_ranking``
into a reusable primitive: prompt the LLM with the target JSON schema, parse
its reply leniently (fenced blocks, embedded JSON, quote repair), validate
against the caller's Pydantic model, and on failure feed the validation error
back to the LLM for a bounded number of repair attempts.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from genxai.llm.base import LLMProvider

logger = logging.getLogger(__name__)

ModelT = TypeVar("ModelT", bound=BaseModel)

DEFAULT_STRUCTURED_SYSTEM_PROMPT = (
    "You are a precise assistant that responds only with a single JSON document "
    "matching the provided JSON schema. No markdown fences, no commentary."
)

_FENCED_BLOCK_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


class StructuredOutputError(Exception):
    """Raised when the LLM response cannot be validated after all repair attempts."""

    def __init__(self, message: str, attempts: int, last_response: str | None = None) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.last_response = last_response


@dataclass(frozen=True)
class StructuredResult(Generic[ModelT]):
    """A validated structured response plus provenance about how it was obtained."""

    output: ModelT
    raw_response: str
    attempts: int
    repaired: bool


def extract_json_candidates(text: str) -> list[str]:
    """Return candidate JSON payloads from an LLM reply, most-likely first.

    Tries, in order: the full text, fenced ``` blocks, and the outermost
    ``{...}`` / ``[...]`` span.
    """
    if not text:
        return []

    candidates = [text.strip()]
    candidates.extend(match.strip() for match in _FENCED_BLOCK_RE.findall(text))

    for open_char, close_char in (("{", "}"), ("[", "]")):
        start = text.find(open_char)
        end = text.rfind(close_char)
        if start != -1 and end > start:
            candidates.append(text[start : end + 1])

    seen: set[str] = set()
    unique: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.add(candidate)
            unique.append(candidate)
    return unique


def parse_json_loosely(text: str) -> Any | None:
    """Parse JSON from raw LLM text, tolerating fences, prose, and single quotes.

    Returns None when no candidate parses.
    """
    for candidate in extract_json_candidates(text):
        for payload in (candidate, candidate.replace("'", '"')):
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                continue
    return None


def schema_instructions(response_model: type[BaseModel]) -> str:
    """Render prompt instructions asking for JSON that matches the model's schema."""
    schema = json.dumps(response_model.model_json_schema(), indent=2)
    return (
        "Respond ONLY with a single JSON document (no markdown, no commentary) "
        f"that validates against this JSON schema:\n{schema}"
    )


def _build_repair_prompt(
    original_prompt: str,
    bad_response: str,
    error: str,
    response_model: type[BaseModel],
) -> str:
    return (
        "Your previous response did not validate against the required JSON schema.\n\n"
        f"Original request:\n{original_prompt}\n\n"
        f"Your previous response:\n{bad_response}\n\n"
        f"Validation error:\n{error}\n\n"
        f"{schema_instructions(response_model)}"
    )


async def generate_structured(
    *,
    llm_provider: LLMProvider,
    prompt: str,
    response_model: type[ModelT],
    system_prompt: str = DEFAULT_STRUCTURED_SYSTEM_PROMPT,
    max_repair_attempts: int = 2,
    **generate_kwargs: Any,
) -> StructuredResult[ModelT]:
    """Generate an LLM response validated as ``response_model``.

    The target JSON schema is appended to the prompt. If parsing or validation
    fails, the error is sent back to the LLM for up to ``max_repair_attempts``
    repair rounds before raising :class:`StructuredOutputError`.
    """
    current_prompt = f"{prompt}\n\n{schema_instructions(response_model)}"
    last_response: str | None = None
    last_error = "no response"
    total_attempts = max_repair_attempts + 1

    for attempt in range(1, total_attempts + 1):
        response = await llm_provider.generate(
            prompt=current_prompt,
            system_prompt=system_prompt,
            **generate_kwargs,
        )
        last_response = response.content or ""

        payload = parse_json_loosely(last_response)
        if payload is None:
            last_error = "response contained no parseable JSON"
        else:
            try:
                output = response_model.model_validate(payload)
                return StructuredResult(
                    output=output,
                    raw_response=last_response,
                    attempts=attempt,
                    repaired=attempt > 1,
                )
            except ValidationError as exc:
                last_error = str(exc)

        logger.warning(
            "Structured output attempt %d/%d failed for %s: %s",
            attempt,
            total_attempts,
            response_model.__name__,
            last_error.splitlines()[0] if last_error else last_error,
        )
        current_prompt = _build_repair_prompt(prompt, last_response, last_error, response_model)

    raise StructuredOutputError(
        f"LLM response failed {response_model.__name__} validation after "
        f"{total_attempts} attempts: {last_error}",
        attempts=total_attempts,
        last_response=last_response,
    )
