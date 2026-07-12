"""Data transformer tool for converting between data formats."""

from typing import Any, Dict, Optional
import logging
import json
import csv
import io

from genxai.tools.base import Tool, ToolMetadata, ToolParameter, ToolCategory

logger = logging.getLogger(__name__)


class DataTransformerTool(Tool):
    """Transform data between different formats (JSON, CSV, XML, etc.)."""

    def __init__(self) -> None:
        """Initialize data transformer tool."""
        metadata = ToolMetadata(
            name="data_transformer",
            description="Convert data between different formats (JSON, CSV, XML, YAML)",
            category=ToolCategory.DATA,
            tags=["transformation", "conversion", "format", "data", "json", "csv", "xml"],
            version="1.0.0",
        )

        parameters = [
            ToolParameter(
                name="data",
                type="string",
                description="Input data to transform",
                required=True,
            ),
            ToolParameter(
                name="operation",
                type="string",
                description="Simple text transformation operation (used by unit tests)",
                required=False,
                default="uppercase",
                enum=["uppercase", "lowercase", "trim", "convert"],
            ),
            ToolParameter(
                name="source_format",
                type="string",
                description="Source data format (for convert operation)",
                required=False,
                default="json",
                enum=["json", "csv", "xml", "yaml"],
            ),
            ToolParameter(
                name="target_format",
                type="string",
                description="Target data format (for convert operation)",
                required=False,
                default="json",
                enum=["json", "csv", "xml", "yaml"],
            ),
            ToolParameter(
                name="csv_delimiter",
                type="string",
                description="CSV delimiter (for CSV operations)",
                required=False,
                default=",",
            ),
        ]

        super().__init__(metadata, parameters)

    async def _execute(
        self,
        data: str,
        operation: str = "convert",
        source_format: str = "json",
        target_format: str = "json",
        csv_delimiter: str = ",",
    ) -> Dict[str, Any]:
        """Execute data transformation.

        Args:
            data: Input data
            source_format: Source format
            target_format: Target format
            csv_delimiter: CSV delimiter

        Returns:
            Dictionary containing transformed data
        """
        result: Dict[str, Any] = {
            "operation": operation,
            "success": False,
        }

        try:
            if operation == "uppercase":
                result["result"] = data.upper()
                result["success"] = True
                return result
            if operation == "lowercase":
                result["result"] = data.lower()
                result["success"] = True
                return result
            if operation == "trim":
                result["result"] = data.strip()
                result["success"] = True
                return result

            if operation not in {"convert"}:
                raise ValueError(f"Unsupported operation: {operation}")

            # Parse source data
            parsed_data = self._parse_data(data, source_format, csv_delimiter)

            # Convert to target format
            transformed_data = self._convert_data(parsed_data, target_format, csv_delimiter)

            result["data"] = transformed_data
            result["source_format"] = source_format
            result["target_format"] = target_format
            result["success"] = True

        except Exception as e:
            result["error"] = str(e)

        logger.info(
            f"Data transformer ({operation}) completed: success={result['success']}"
        )
        return result

    def _parse_data(self, data: str, format: str, delimiter: str) -> Any:
        """Parse data from source format.

        Args:
            data: Input data
            format: Data format
            delimiter: CSV delimiter

        Returns:
            Parsed data
        """
        if format == "json":
            return json.loads(data)
        
        elif format == "csv":
            csv_file = io.StringIO(data)
            reader = csv.DictReader(csv_file, delimiter=delimiter)
            return list(reader)
        
        elif format == "xml":
            import xml.etree.ElementTree as ET
            root = ET.fromstring(data)
            return self._xml_to_dict(root)
        
        elif format == "yaml":
            try:
                import yaml
                return yaml.safe_load(data)
            except ImportError:
                raise ImportError(
                    "pyyaml package not installed. Install with: pip install pyyaml"
                )
        
        else:
            raise ValueError(f"Unsupported source format: {format}")

    def _convert_data(self, data: Any, format: str, delimiter: str) -> str:
        """Convert data to target format.

        Args:
            data: Parsed data
            format: Target format
            delimiter: CSV delimiter

        Returns:
            Converted data string
        """
        if format == "json":
            return json.dumps(data, indent=2)
        
        elif format == "csv":
            if not isinstance(data, list):
                raise ValueError("CSV conversion requires list of dictionaries")
            
            if not data:
                return ""
            
            output = io.StringIO()
            if isinstance(data[0], dict):
                writer = csv.DictWriter(output, fieldnames=data[0].keys(), delimiter=delimiter)
                writer.writeheader()
                writer.writerows(data)
            else:
                writer = csv.writer(output, delimiter=delimiter)
                writer.writerows(data)
            
            return output.getvalue()
        
        elif format == "xml":
            import xml.etree.ElementTree as ET
            root = self._dict_to_xml(data, "root")
            self._indent_xml(root)
            return ET.tostring(root, encoding="unicode")
        
        elif format == "yaml":
            try:
                import yaml
                return yaml.dump(data, default_flow_style=False, sort_keys=False)
            except ImportError:
                raise ImportError(
                    "pyyaml package not installed. Install with: pip install pyyaml"
                )
        
        else:
            raise ValueError(f"Unsupported target format: {format}")

    def _xml_to_dict(self, element: Any) -> Dict[str, Any]:
        """Convert XML element to dictionary.

        Args:
            element: XML element

        Returns:
            Dictionary representation
        """
        result: Dict[str, Any] = {}
        
        # Add attributes
        if element.attrib:
            result["@attributes"] = element.attrib
        
        # Add text content
        if element.text and element.text.strip():
            result["#text"] = element.text.strip()
        
        # Add children
        for child in element:
            child_data = self._xml_to_dict(child)
            if child.tag in result:
                # Multiple children with same tag - convert to list
                if not isinstance(result[child.tag], list):
                    result[child.tag] = [result[child.tag]]
                result[child.tag].append(child_data)
            else:
                result[child.tag] = child_data
        
        return {element.tag: result} if result else {element.tag: element.text}

    def _dict_to_xml(self, data: Any, root_tag: str = "root") -> Any:
        """Convert dictionary to XML element.

        Args:
            data: Dictionary data
            root_tag: Root element tag

        Returns:
            XML element
        """
        import xml.etree.ElementTree as ET
        
        root = ET.Element(root_tag)
        
        if isinstance(data, dict):
            for key, value in data.items():
                if key == "@attributes":
                    root.attrib.update(value)
                elif key == "#text":
                    root.text = str(value)
                else:
                    if isinstance(value, list):
                        for item in value:
                            child = self._dict_to_xml(item, key)
                            root.append(child)
                    else:
                        child = self._dict_to_xml(value, key)
                        root.append(child)
        else:
            root.text = str(data)
        
        return root

    def _indent_xml(self, elem: Any, level: int = 0) -> None:
        """Add indentation to XML element.

        Args:
            elem: XML element
            level: Indentation level
        """
        indent = "\n" + "  " * level
        if len(elem):
            if not elem.text or not elem.text.strip():
                elem.text = indent + "  "
            if not elem.tail or not elem.tail.strip():
                elem.tail = indent
            for child in elem:
                self._indent_xml(child, level + 1)
            if not child.tail or not child.tail.strip():
                child.tail = indent
        else:
            if level and (not elem.tail or not elem.tail.strip()):
                elem.tail = indent
