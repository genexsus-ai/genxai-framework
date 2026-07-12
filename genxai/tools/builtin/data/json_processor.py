"""JSON processor tool for parsing and manipulating JSON data."""

from typing import Any, Dict, List, Optional, Union
import logging
import json

from genxai.tools.base import Tool, ToolMetadata, ToolParameter, ToolCategory

logger = logging.getLogger(__name__)


class JSONProcessorTool(Tool):
    """Process, parse, and manipulate JSON data."""

    def __init__(self) -> None:
        """Initialize JSON processor tool."""
        metadata = ToolMetadata(
            name="json_processor",
            description="Parse, validate, query, and transform JSON data",
            category=ToolCategory.DATA,
            tags=["json", "data", "parsing", "transformation", "query"],
            version="1.0.0",
        )

        parameters = [
            ToolParameter(
                name="data",
                type="object",
                description="JSON input. Can be a JSON string (for parse/validate/query/etc.) or a Python object (for stringify)",
                required=True,
            ),
            ToolParameter(
                name="operation",
                type="string",
                description="Operation to perform",
                required=True,
                enum=[
                    "parse",
                    "validate",
                    "query",
                    "transform",
                    "minify",
                    "prettify",
                    "stringify",
                ],
            ),
            ToolParameter(
                name="query_path",
                type="string",
                description="JSONPath query (for query operation)",
                required=False,
            ),
            ToolParameter(
                name="transform_rules",
                type="object",
                description="Transformation rules (for transform operation)",
                required=False,
            ),
        ]

        super().__init__(metadata, parameters)

    async def _execute(
        self,
        data: Any,
        operation: str,
        query_path: Optional[str] = None,
        transform_rules: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute JSON processing.

        Args:
            data: JSON string
            operation: Operation to perform
            query_path: JSONPath query
            transform_rules: Transformation rules

        Returns:
            Dictionary containing processed data
        """
        result: Dict[str, Any] = {
            "operation": operation,
            "success": False,
        }

        try:
            # Support passing either a JSON string (common) or a Python object
            # for operations like "stringify".
            if operation == "stringify":
                # Serialize python object to JSON.
                result["data"] = json.dumps(data)
                result["success"] = True
                logger.info(f"JSON {operation} operation completed: success={result['success']}")
                return result

            if not isinstance(data, str):
                raise ValueError("data must be a JSON string for this operation")

            # Parse JSON
            parsed_data = json.loads(data)
            result["parsed"] = True

            if operation == "parse":
                result["data"] = parsed_data
                result["type"] = type(parsed_data).__name__
                if isinstance(parsed_data, dict):
                    result["keys"] = list(parsed_data.keys())
                    result["key_count"] = len(parsed_data)
                elif isinstance(parsed_data, list):
                    result["length"] = len(parsed_data)
                result["success"] = True

            elif operation == "validate":
                result["valid"] = True
                result["data"] = parsed_data
                result["type"] = type(parsed_data).__name__
                result["success"] = True

            elif operation == "query":
                if not query_path:
                    raise ValueError("query_path is required for query operation")
                
                # Simple JSONPath-like query implementation
                query_result = self._query_json(parsed_data, query_path)
                result["data"] = query_result
                result["matches"] = len(query_result) if isinstance(query_result, list) else 1
                result["success"] = True

            elif operation == "transform":
                if not transform_rules:
                    raise ValueError("transform_rules is required for transform operation")
                
                transformed = self._transform_json(parsed_data, transform_rules)
                result["data"] = transformed
                result["success"] = True

            elif operation == "minify":
                minified = json.dumps(parsed_data, separators=(",", ":"))
                result["data"] = minified
                result["original_size"] = len(data)
                result["minified_size"] = len(minified)
                result["reduction_percent"] = (
                    (len(data) - len(minified)) / len(data) * 100
                )
                result["success"] = True

            elif operation == "prettify":
                prettified = json.dumps(parsed_data, indent=2, sort_keys=True)
                result["data"] = prettified
                result["success"] = True

        except json.JSONDecodeError as e:
            result["error"] = f"Invalid JSON: {str(e)}"
            result["error_line"] = e.lineno
            result["error_column"] = e.colno
        except Exception as e:
            result["error"] = str(e)

        logger.info(f"JSON {operation} operation completed: success={result['success']}")
        return result

    def _query_json(self, data: Any, path: str) -> Any:
        """Simple JSONPath-like query implementation.

        Args:
            data: JSON data
            path: Query path (e.g., "$.users[0].name")

        Returns:
            Query result
        """
        # Remove leading $. if present
        path = path.lstrip("$.")
        
        parts = path.split(".")
        current = data

        for part in parts:
            # Handle array indexing
            if "[" in part and "]" in part:
                key = part[:part.index("[")]
                index_str = part[part.index("[") + 1:part.index("]")]
                
                if key:
                    current = current[key]
                
                if index_str == "*":
                    # Return all elements
                    return current if isinstance(current, list) else [current]
                else:
                    index = int(index_str)
                    current = current[index]
            else:
                current = current[part]

        return current

    def _transform_json(self, data: Any, rules: Dict[str, Any]) -> Any:
        """Transform JSON data based on rules.

        Args:
            data: JSON data
            rules: Transformation rules

        Returns:
            Transformed data
        """
        if not isinstance(data, dict):
            return data

        result = {}
        
        for new_key, rule in rules.items():
            if isinstance(rule, str):
                # Simple key mapping
                if rule in data:
                    result[new_key] = data[rule]
            elif isinstance(rule, dict):
                # Complex transformation
                if "source" in rule:
                    value = data.get(rule["source"])
                    
                    # Apply transformations
                    if "uppercase" in rule and rule["uppercase"]:
                        value = value.upper() if isinstance(value, str) else value
                    if "lowercase" in rule and rule["lowercase"]:
                        value = value.lower() if isinstance(value, str) else value
                    if "default" in rule and value is None:
                        value = rule["default"]
                    
                    result[new_key] = value

        return result
