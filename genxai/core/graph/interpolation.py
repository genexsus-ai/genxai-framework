"""Template interpolation for passing data between workflow nodes.

Node configs (tool parameters, agent tasks) may reference workflow state with
``{{ path.to.value }}`` expressions. Paths are dot-separated lookups into the
state dict — typically a prior node's result stored under its node id:

    {"expression": "{{ calc1.data.result }} + 8"}
    {"task": "Summarize this: {{ fetch.data.content }}"}

Resolution rules:
- A string that is exactly one expression resolves to the raw value
  (numbers, dicts, and lists keep their type).
- Expressions embedded in a longer string are substituted as text.
- Dicts and lists are resolved recursively.
- An unresolvable path raises ``TemplateResolutionError`` naming the path,
  so mistakes surface at the failing node instead of executing with a
  literal ``{{ ... }}`` string.
"""

from __future__ import annotations

import re
from typing import Any

_EXPR_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


class TemplateResolutionError(KeyError):
    """Raised when a template path cannot be resolved against state."""

    def __init__(self, path: str) -> None:
        super().__init__(path)
        self.path = path

    def __str__(self) -> str:
        return (
            f"Cannot resolve template '{{{{ {self.path} }}}}': "
            f"path '{self.path}' not found in workflow state"
        )


def _lookup(path: str, state: dict[str, Any]) -> Any:
    current: Any = state
    for part in path.split("."):
        part = part.strip()
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.lstrip("-").isdigit():
            index = int(part)
            try:
                current = current[index]
            except IndexError:
                raise TemplateResolutionError(path) from None
        else:
            raise TemplateResolutionError(path)
    return current


def resolve_templates(value: Any, state: dict[str, Any]) -> Any:
    """Recursively resolve ``{{ path }}`` expressions in value against state."""
    if isinstance(value, str):
        full_match = _EXPR_RE.fullmatch(value.strip())
        if full_match:
            return _lookup(full_match.group(1), state)
        return _EXPR_RE.sub(lambda m: str(_lookup(m.group(1), state)), value)
    if isinstance(value, dict):
        return {key: resolve_templates(item, state) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_templates(item, state) for item in value]
    return value
