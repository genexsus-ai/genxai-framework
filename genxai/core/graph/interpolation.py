"""Template interpolation for passing data between workflow nodes.

Node configs (tool parameters, agent tasks) may reference workflow state with
``{{ path.to.value }}`` expressions. Paths are dot-separated lookups into the
state dict — typically a prior node's result stored under its node id:

    {"expression": "{{ calc1.data.result }} + 8"}
    {"task": "Summarize this: {{ fetch.data.content }}"}

A path may be followed by pipe filters, applied left to right:

    {{ input.name | upper }}
    {{ feed.data.items | first | json }}
    {{ input.threshold | default:10 }}
    {{ input.tags | join:", " }}

Available filters: upper, lower, title, trim, length, json, first, last,
int, float, round[:digits], join[:separator], default:<value>. ``default``
also rescues an unresolvable path (missing optional input fields).

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

import json as _json
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


def _parse_filter_arg(raw: str) -> Any:
    """A filter argument: JSON literal if it parses, else the raw text."""
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    try:
        return _json.loads(text)
    except ValueError:
        return text


def _apply_filter(value: Any, name: str, arg: Any, expression: str) -> Any:
    if name == "upper":
        return str(value).upper()
    if name == "lower":
        return str(value).lower()
    if name == "title":
        return str(value).title()
    if name == "trim":
        return str(value).strip()
    if name == "length":
        return len(value)
    if name == "json":
        return _json.dumps(value, default=str)
    if name == "first":
        return value[0] if value else None
    if name == "last":
        return value[-1] if value else None
    if name == "int":
        return int(float(value))
    if name == "float":
        return float(value)
    if name == "round":
        return round(float(value), int(arg)) if arg is not None else round(float(value))
    if name == "join":
        separator = str(arg) if arg is not None else ", "
        return separator.join(str(item) for item in value)
    if name == "default":
        return arg if value in (None, "") else value
    raise TemplateResolutionError(f"{expression} (unknown filter '{name}')")


def _resolve_expression(expression: str, state: dict[str, Any]) -> Any:
    """Resolve ``path | filter | filter:arg`` against state."""
    parts = [part.strip() for part in expression.split("|")]
    path, filters = parts[0], parts[1:]

    parsed = []
    for spec in filters:
        name, _, raw_arg = spec.partition(":")
        parsed.append((name.strip(), _parse_filter_arg(raw_arg) if raw_arg else None))

    try:
        value = _lookup(path, state)
    except TemplateResolutionError:
        # An explicit default rescues a missing path; anything else is an error
        rescue = next((i for i, (n, _) in enumerate(parsed) if n == "default"), None)
        if rescue is None:
            raise
        value = parsed[rescue][1]
        parsed = parsed[rescue + 1 :]

    for name, arg in parsed:
        try:
            value = _apply_filter(value, name, arg, expression)
        except TemplateResolutionError:
            raise
        except Exception as exc:
            raise TemplateResolutionError(
                f"{expression} (filter '{name}' failed: {exc})"
            ) from exc
    return value


def resolve_templates(value: Any, state: dict[str, Any]) -> Any:
    """Recursively resolve ``{{ path }}`` expressions in value against state."""
    if isinstance(value, str):
        full_match = _EXPR_RE.fullmatch(value.strip())
        if full_match:
            return _resolve_expression(full_match.group(1), state)
        return _EXPR_RE.sub(
            lambda m: str(_resolve_expression(m.group(1), state)), value
        )
    if isinstance(value, dict):
        return {key: resolve_templates(item, state) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_templates(item, state) for item in value]
    return value
