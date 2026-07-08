"""Mock LLM provider for tests."""

from collections.abc import AsyncIterator
from typing import Any

from genxai.llm.base import LLMProvider, LLMResponse


class MockLLMProvider(LLMProvider):
    """Deterministic mock LLM provider for tests."""

    def __init__(
        self,
        model: str = "mock-model",
        temperature: float = 0.0,
        response_text: str = (
            "Mock response for testing purposes. This is a deterministic placeholder."
        ),
        responses: list[str] | None = None,
    ) -> None:
        """With ``responses``, each generate() call returns the next entry
        (the last one repeats once exhausted); otherwise ``response_text``
        is returned every time."""
        super().__init__(model=model, temperature=temperature)
        self._response_text = response_text
        self._responses = list(responses) if responses else None
        self.prompts: list[str] = []

    async def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        self.prompts.append(prompt)
        if self._responses is not None:
            index = min(len(self.prompts) - 1, len(self._responses) - 1)
            content = self._responses[index]
        else:
            content = self._response_text
        usage = {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}
        self._update_stats(usage)
        return LLMResponse(content=content, model=self.model, usage=usage, finish_reason="stop")

    async def generate_stream(
        self,
        prompt: str,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        yield "Mock "
        yield "response"
