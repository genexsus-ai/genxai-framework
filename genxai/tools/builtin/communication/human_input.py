"""Human input tool for interactive workflows."""

from typing import Any

from genxai.tools.base import Tool, ToolCategory, ToolMetadata, ToolParameter


class HumanInputTool(Tool):
    """Collect input from a human (stdin)."""

    def __init__(self) -> None:
        from genxai.tools.registry import ToolRegistry
        super().__init__(
            metadata=ToolMetadata(
                name="human_input",
                description="Collects human input from the console",
                category=ToolCategory.CUSTOM,
            ),
            parameters=[
                ToolParameter(
                    name="prompt",
                    type="string",
                    description="Prompt to show the user",
                )
            ],
        )
        if ToolRegistry.get(self.metadata.name) is None:
            ToolRegistry.register(self)

    async def _execute(self, **kwargs: Any) -> dict[str, Any]:
        prompt = kwargs.get("prompt", "Your response:")
        return {"response": input(f"{prompt} ")}
