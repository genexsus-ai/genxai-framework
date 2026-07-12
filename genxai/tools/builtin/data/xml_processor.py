"""XML processor tool for parsing and manipulating XML data."""

from typing import Any, Dict, List, Optional
import logging
import xml.etree.ElementTree as ET

from genxai.tools.base import Tool, ToolMetadata, ToolParameter, ToolCategory

logger = logging.getLogger(__name__)


class XMLProcessorTool(Tool):
    """Process, parse, and manipulate XML data."""

    def __init__(self) -> None:
        """Initialize XML processor tool."""
        metadata = ToolMetadata(
            name="xml_processor",
            description="Parse, validate, query, and transform XML data",
            category=ToolCategory.DATA,
            tags=["xml", "data", "parsing", "xpath", "transformation"],
            version="1.0.0",
        )

        parameters = [
            ToolParameter(
                name="data",
                type="string",
                description="XML string to process",
                required=True,
            ),
            ToolParameter(
                name="operation",
                type="string",
                description="Operation to perform",
                required=True,
                enum=["parse", "validate", "query", "to_dict", "prettify"],
            ),
            ToolParameter(
                name="xpath",
                type="string",
                description="XPath query (for query operation)",
                required=False,
            ),
        ]

        super().__init__(metadata, parameters)

    async def _execute(
        self,
        data: str,
        operation: str,
        xpath: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute XML processing.

        Args:
            data: XML string
            operation: Operation to perform
            xpath: XPath query

        Returns:
            Dictionary containing processed data
        """
        result: Dict[str, Any] = {
            "operation": operation,
            "success": False,
        }

        try:
            # Parse XML
            root = ET.fromstring(data)
            result["parsed"] = True

            if operation == "parse":
                result["root_tag"] = root.tag
                result["root_attributes"] = root.attrib
                result["child_count"] = len(list(root))
                result["success"] = True

            elif operation == "validate":
                # Basic validation (well-formed check)
                result["valid"] = True
                result["root_tag"] = root.tag
                result["success"] = True

            elif operation == "query":
                if not xpath:
                    raise ValueError("xpath is required for query operation")
                
                # Execute XPath query
                elements = root.findall(xpath)
                query_results = []
                
                for elem in elements:
                    query_results.append({
                        "tag": elem.tag,
                        "text": elem.text,
                        "attributes": elem.attrib,
                        "children": [child.tag for child in elem],
                    })
                
                result["data"] = query_results
                result["match_count"] = len(query_results)
                result["success"] = True

            elif operation == "to_dict":
                # Convert XML to dictionary
                xml_dict = self._element_to_dict(root)
                result["data"] = xml_dict
                result["success"] = True

            elif operation == "prettify":
                # Pretty print XML
                self._indent(root)
                prettified = ET.tostring(root, encoding="unicode")
                result["data"] = prettified
                result["success"] = True

        except ET.ParseError as e:
            result["error"] = f"Invalid XML: {str(e)}"
        except Exception as e:
            result["error"] = str(e)

        logger.info(f"XML {operation} operation completed: success={result['success']}")
        return result

    def _element_to_dict(self, element: ET.Element) -> Dict[str, Any]:
        """Convert XML element to dictionary.

        Args:
            element: XML element

        Returns:
            Dictionary representation
        """
        result: Dict[str, Any] = {
            "tag": element.tag,
        }

        # Add attributes
        if element.attrib:
            result["attributes"] = element.attrib

        # Add text content
        if element.text and element.text.strip():
            result["text"] = element.text.strip()

        # Add children
        children = list(element)
        if children:
            result["children"] = [self._element_to_dict(child) for child in children]

        return result

    def _indent(self, elem: ET.Element, level: int = 0) -> None:
        """Add indentation to XML element for pretty printing.

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
                self._indent(child, level + 1)
            if not child.tail or not child.tail.strip():
                child.tail = indent
        else:
            if level and (not elem.tail or not elem.tail.strip()):
                elem.tail = indent
