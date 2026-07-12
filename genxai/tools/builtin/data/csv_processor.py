"""CSV processor tool for parsing and manipulating CSV data."""

from typing import Any, Dict, List, Optional
import logging
import csv
import io

from genxai.tools.base import Tool, ToolMetadata, ToolParameter, ToolCategory

logger = logging.getLogger(__name__)


class CSVProcessorTool(Tool):
    """Process, parse, and manipulate CSV data."""

    def __init__(self) -> None:
        """Initialize CSV processor tool."""
        metadata = ToolMetadata(
            name="csv_processor",
            description="Parse, validate, filter, and transform CSV data",
            category=ToolCategory.DATA,
            tags=["csv", "data", "parsing", "tabular", "spreadsheet"],
            version="1.0.0",
        )

        parameters = [
            ToolParameter(
                name="data",
                type="string",
                description="CSV string to process",
                required=True,
            ),
            ToolParameter(
                name="operation",
                type="string",
                description="Operation to perform",
                required=True,
                enum=["parse", "filter", "transform", "aggregate", "validate"],
            ),
            ToolParameter(
                name="delimiter",
                type="string",
                description="CSV delimiter character",
                required=False,
                default=",",
            ),
            ToolParameter(
                name="has_header",
                type="boolean",
                description="Whether CSV has header row",
                required=False,
                default=True,
            ),
            ToolParameter(
                name="filter_column",
                type="string",
                description="Column name to filter by (for filter operation)",
                required=False,
            ),
            ToolParameter(
                name="filter_value",
                type="string",
                description="Value to filter for (for filter operation)",
                required=False,
            ),
        ]

        super().__init__(metadata, parameters)

    async def _execute(
        self,
        data: str,
        operation: str,
        delimiter: str = ",",
        has_header: bool = True,
        filter_column: Optional[str] = None,
        filter_value: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute CSV processing.

        Args:
            data: CSV string
            operation: Operation to perform
            delimiter: CSV delimiter
            has_header: Has header flag
            filter_column: Column to filter
            filter_value: Value to filter

        Returns:
            Dictionary containing processed data
        """
        result: Dict[str, Any] = {
            "operation": operation,
            "success": False,
        }

        try:
            # Parse CSV
            csv_file = io.StringIO(data)
            reader = csv.reader(csv_file, delimiter=delimiter)
            rows = list(reader)

            if not rows:
                raise ValueError("Empty CSV data")

            headers = rows[0] if has_header else [f"col_{i}" for i in range(len(rows[0]))]
            data_rows = rows[1:] if has_header else rows

            if operation == "parse":
                # Convert to list of dictionaries
                parsed_data = []
                for row in data_rows:
                    if len(row) == len(headers):
                        parsed_data.append(dict(zip(headers, row)))
                
                result["data"] = parsed_data
                result["headers"] = headers
                result["row_count"] = len(parsed_data)
                result["column_count"] = len(headers)
                result["success"] = True

            elif operation == "filter":
                if not filter_column or filter_value is None:
                    raise ValueError("filter_column and filter_value required for filter operation")
                
                if filter_column not in headers:
                    raise ValueError(f"Column '{filter_column}' not found in headers")
                
                col_index = headers.index(filter_column)
                filtered_rows = [
                    row for row in data_rows
                    if len(row) > col_index and row[col_index] == filter_value
                ]
                
                filtered_data = [dict(zip(headers, row)) for row in filtered_rows]
                result["data"] = filtered_data
                result["filtered_count"] = len(filtered_data)
                result["original_count"] = len(data_rows)
                result["success"] = True

            elif operation == "transform":
                # Convert to structured format
                transformed = {
                    "headers": headers,
                    "rows": data_rows,
                    "metadata": {
                        "row_count": len(data_rows),
                        "column_count": len(headers),
                        "delimiter": delimiter,
                    }
                }
                result["data"] = transformed
                result["success"] = True

            elif operation == "aggregate":
                # Calculate basic statistics for numeric columns
                aggregates = {}
                
                for col_idx, header in enumerate(headers):
                    column_values = [row[col_idx] for row in data_rows if len(row) > col_idx]
                    
                    # Try to convert to numbers
                    numeric_values = []
                    for val in column_values:
                        try:
                            numeric_values.append(float(val))
                        except (ValueError, TypeError):
                            pass
                    
                    if numeric_values:
                        aggregates[header] = {
                            "count": len(numeric_values),
                            "sum": sum(numeric_values),
                            "mean": sum(numeric_values) / len(numeric_values),
                            "min": min(numeric_values),
                            "max": max(numeric_values),
                        }
                    else:
                        # For non-numeric columns, count unique values
                        unique_values = set(column_values)
                        aggregates[header] = {
                            "count": len(column_values),
                            "unique_count": len(unique_values),
                            "sample_values": list(unique_values)[:5],
                        }
                
                result["data"] = aggregates
                result["success"] = True

            elif operation == "validate":
                # Validate CSV structure
                issues = []
                
                # Check for consistent column count
                expected_cols = len(headers)
                for idx, row in enumerate(data_rows):
                    if len(row) != expected_cols:
                        issues.append({
                            "row": idx + (2 if has_header else 1),
                            "issue": f"Expected {expected_cols} columns, found {len(row)}",
                        })
                
                result["valid"] = len(issues) == 0
                result["issues"] = issues
                result["row_count"] = len(data_rows)
                result["column_count"] = len(headers)
                result["success"] = True

        except Exception as e:
            result["error"] = str(e)

        logger.info(f"CSV {operation} operation completed: success={result['success']}")
        return result
