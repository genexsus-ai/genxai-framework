"""Data filter tool: keep only the list items matching a condition (n8n-style)."""

from typing import Any, Dict, List, Optional
import logging

from genxai.tools.base import Tool, ToolMetadata, ToolParameter, ToolCategory

logger = logging.getLogger(__name__)

OPERATORS = [
    "equals",
    "not_equals",
    "greater_than",
    "less_than",
    "greater_or_equal",
    "less_or_equal",
    "contains",
    "not_contains",
    "starts_with",
    "ends_with",
    "is_empty",
    "is_not_empty",
    "is_true",
    "is_false",
]

# Operators that don't use the comparison value
_UNARY = {"is_empty", "is_not_empty", "is_true", "is_false"}


class DataFilterTool(Tool):
    """Filter a list of items, keeping only those that match a condition.

    Mirrors n8n's Filter node: pass a list in ``items``, a ``field`` (a key or
    dot-path evaluated on each item), an ``operator``, and a ``value``. Items
    matching the condition are kept (or dropped when ``keep=False``).
    """

    def __init__(self) -> None:
        metadata = ToolMetadata(
            name="data_filter",
            description="Filter a list, keeping only items that match a condition",
            category=ToolCategory.DATA,
            tags=["filter", "data", "list", "condition"],
            version="1.0.0",
        )
        parameters = [
            ToolParameter(
                name="items",
                type="array",
                description="The list of items (objects) to filter",
                required=True,
            ),
            ToolParameter(
                name="field",
                type="string",
                description="Key or dot-path evaluated on each item (e.g. 'age' or 'user.email'). Leave empty to test the item itself.",
                required=False,
            ),
            ToolParameter(
                name="operator",
                type="string",
                description="Comparison operator",
                required=False,
                enum=OPERATORS,
            ),
            ToolParameter(
                name="value",
                type="object",
                description="Value to compare against (ignored for is_empty / is_true style operators)",
                required=False,
            ),
            ToolParameter(
                name="keep",
                type="boolean",
                description="Keep matching items (true, default) or drop them (false)",
                required=False,
            ),
        ]
        super().__init__(metadata, parameters)

    async def _execute(
        self,
        items: Any,
        field: Optional[str] = None,
        operator: str = "is_not_empty",
        value: Any = None,
        keep: bool = True,
    ) -> Dict[str, Any]:
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            return {
                "success": False,
                "error": "items must be a list (or a single object)",
                "filtered": [],
            }
        if operator not in OPERATORS:
            return {
                "success": False,
                "error": f"Unknown operator '{operator}'. One of: {', '.join(OPERATORS)}",
                "filtered": [],
            }

        kept: List[Any] = []
        removed: List[Any] = []
        for item in items:
            field_value = _resolve_field(item, field) if field else item
            matches = _matches(field_value, operator, value)
            (kept if matches == keep else removed).append(item)

        logger.info(
            "data_filter: %d in -> %d kept, %d removed", len(items), len(kept), len(removed)
        )
        return {
            "success": True,
            "filtered": kept,
            "kept": len(kept),
            "removed": len(removed),
            "total": len(items),
        }


def _resolve_field(item: Any, field: str) -> Any:
    value: Any = item
    for part in field.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return None
    return value


def _matches(field_value: Any, operator: str, value: Any) -> bool:
    if operator == "is_empty":
        return field_value in (None, "", [], {})
    if operator == "is_not_empty":
        return field_value not in (None, "", [], {})
    if operator == "is_true":
        return bool(field_value) is True
    if operator == "is_false":
        return bool(field_value) is False

    if operator == "equals":
        return _coerce_eq(field_value, value)
    if operator == "not_equals":
        return not _coerce_eq(field_value, value)
    if operator == "contains":
        return _contains(field_value, value)
    if operator == "not_contains":
        return not _contains(field_value, value)
    if operator == "starts_with":
        return str(field_value).startswith(str(value))
    if operator == "ends_with":
        return str(field_value).endswith(str(value))

    # Numeric comparisons — fall back to string compare if not numbers
    left, right = _as_number(field_value), _as_number(value)
    if left is None or right is None:
        left, right = str(field_value), str(value)
    if operator == "greater_than":
        return left > right
    if operator == "less_than":
        return left < right
    if operator == "greater_or_equal":
        return left >= right
    if operator == "less_or_equal":
        return left <= right
    return False


def _coerce_eq(a: Any, b: Any) -> bool:
    if a == b:
        return True
    # "5" == 5, "true" == True style leniency
    na, nb = _as_number(a), _as_number(b)
    if na is not None and nb is not None:
        return na == nb
    return str(a).strip().lower() == str(b).strip().lower()


def _contains(haystack: Any, needle: Any) -> bool:
    if isinstance(haystack, (list, dict, str)):
        try:
            return needle in haystack
        except TypeError:
            return False
    return str(needle) in str(haystack)


def _as_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (ValueError, TypeError):
        return None
