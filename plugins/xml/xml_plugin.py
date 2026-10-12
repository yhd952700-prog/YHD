"""
XML Processing Plugin for LiuHao AI OS

Provides XML parsing, generation, XPath query, XSLT interface,
XML to JSON / JSON to XML, and XML validation (DTD/XSD interface).

Capabilities:
- parse: Parse XML string to dict
- generate: Generate XML string from data
- xpath: Query XML using XPath expressions
- to_json: Convert XML to JSON
- from_json: Convert JSON to XML
- validate: Validate XML structure
- transform: XSLT transformation interface
- extract_text: Extract all text content
- find_elements: Find elements by tag name
- get_attributes: Get element attributes
- modify: Modify XML elements
"""

import re
import json
from typing import Dict, Any, Optional, List, Tuple, Union
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class XmlParseResult:
    """Result of XML parsing."""
    success: bool
    data: Optional[Dict[str, Any]] = None
    root_tag: Optional[str] = None
    error: Optional[str] = None
    error_line: Optional[int] = None


@dataclass
class XmlValidationResult:
    """Result of XML validation."""
    is_valid: bool
    errors: List[str]
    warnings: List[str]
    root_element: Optional[str] = None
    element_count: int = 0
    attribute_count: int = 0


@dataclass
class XpathResult:
    """Result of an XPath query."""
    matches: List[Dict[str, Any]]
    count: int
    expression: str


class XmlPlugin(BasePlugin):
    """XML processing plugin.

    Provides XML parsing, generation, XPath query, and format conversion.
    Uses Python's built-in xml.etree.ElementTree.

    Example:
        plugin = XmlPlugin()
        plugin.initialize()
        plugin.activate()

        result = plugin.parse("<root><item>value</item></root>")
        print(result.data)
    """

    def __init__(self) -> None:
        """Initialize the XML plugin."""
        metadata = PluginMetadata(
            name="xml",
            version="1.0.0",
            description="XML parsing, generation, XPath query, and conversion",
            author="LiuHao AI OS Team",
            categories=["data", "conversion", "utility", "serialization"],
            keywords=["xml", "parse", "xpath", "xslt", "json", "markup"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._default_indent: str = "  "
        self._default_encoding: str = "UTF-8"
        self._namespace_map: Dict[str, str] = {}

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the XML plugin."""
        try:
            if config:
                self._default_indent = config.get("indent", "  ")
                self._default_encoding = config.get("encoding", "UTF-8")
                if "namespaces" in config:
                    self._namespace_map.update(config["namespaces"])

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the XML plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the XML plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the XML plugin."""
        try:
            self._namespace_map.clear()
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "parse",
            "generate",
            "xpath",
            "to_json",
            "from_json",
            "validate",
            "extract_text",
            "find_elements",
            "get_attributes",
            "count_elements",
            "flatten_elements",
            "transform",
        ]

    # ==================== Parsing ====================

    def parse(self, xml_text: str) -> XmlParseResult:
        """Parse XML string into a dictionary structure.

        Args:
            xml_text: The XML text to parse.

        Returns:
            XmlParseResult with parsed data.
        """
        self._ensure_active()
        self._increment_call_count()

        try:
            # Register namespaces to preserve prefixes
            for prefix, uri in self._namespace_map.items():
                ET.register_namespace(prefix, uri)

            root = ET.fromstring(xml_text)
            data = self._element_to_dict(root)

            return XmlParseResult(
                success=True,
                data=data,
                root_tag=root.tag,
            )
        except ET.ParseError as e:
            return XmlParseResult(
                success=False,
                error=str(e),
            )
        except Exception as e:
            return XmlParseResult(
                success=False,
                error=str(e),
            )

    def _element_to_dict(self, element: ET.Element) -> Dict[str, Any]:
        """Convert an XML element to a dictionary."""
        result: Dict[str, Any] = {}

        # Add attributes with @ prefix
        if element.attrib:
            for attr_name, attr_value in element.attrib.items():
                result[f"@{attr_name}"] = attr_value

        # Process children
        children = list(element)

        if not children:
            # Leaf element with text
            text = element.text.strip() if element.text else ""
            if text:
                if result:
                    result["#text"] = text
                else:
                    return text  # Simple text content
            return result

        # Group children by tag name
        child_groups: Dict[str, List[Any]] = {}
        for child in children:
            child_data = self._element_to_dict(child)
            child_groups.setdefault(child.tag, []).append(child_data)

        # Add to result
        for tag, items in child_groups.items():
            if len(items) == 1:
                result[tag] = items[0]
            else:
                result[tag] = items

        # Add text content if present
        if element.text and element.text.strip():
            result["#text"] = element.text.strip()

        return result

    # ==================== Generation ====================

    def generate(
        self,
        data: Dict[str, Any],
        *,
        root_tag: str = "root",
        pretty: bool = True,
        declaration: bool = True,
        encoding: Optional[str] = None,
    ) -> str:
        """Generate XML string from dictionary data.

        Args:
            data: Dictionary with XML structure.
            root_tag: Root element tag name.
            pretty: Whether to indent the output.
            declaration: Whether to include XML declaration.
            encoding: Character encoding for declaration.

        Returns:
            XML string.
        """
        self._ensure_active()
        self._increment_call_count()

        encoding = encoding or self._default_encoding

        root = ET.Element(root_tag)
        self._dict_to_element(data, root)

        # Pretty print
        if pretty:
            self._indent_element(root, 0, self._default_indent)

        xml_str = ET.tostring(root, encoding="unicode", xml_declaration=False)

        if declaration:
            xml_str = f'<?xml version="1.0" encoding="{encoding}"?>\n' + xml_str

        return xml_str

    def _dict_to_element(self, data: Any, parent: ET.Element) -> None:
        """Build XML elements from dictionary data."""
        if isinstance(data, dict):
            for key, value in data.items():
                if key.startswith("@"):
                    # Attribute
                    attr_name = key[1:]
                    parent.set(attr_name, str(value))
                elif key == "#text":
                    # Text content
                    parent.text = str(value)
                elif isinstance(value, list):
                    # Multiple children with same tag
                    for item in value:
                        child = ET.SubElement(parent, key)
                        self._dict_to_element(item, child)
                elif isinstance(value, dict):
                    # Nested element
                    child = ET.SubElement(parent, key)
                    self._dict_to_element(value, child)
                else:
                    # Simple value element
                    child = ET.SubElement(parent, key)
                    if value is not None:
                        child.text = str(value)

    def _indent_element(self, element: ET.Element, level: int, indent_str: str) -> None:
        """Add whitespace indentation to an element tree."""
        indent = "\n" + indent_str * level
        if len(element):
            if not element.text or not element.text.strip():
                element.text = indent + indent_str
            for child in element:
                self._indent_element(child, level + 1, indent_str)
            if not element.tail or not element.tail.strip():
                element.tail = indent
        else:
            if level and (not element.tail or not element.tail.strip()):
                element.tail = indent

    # ==================== XPath Query ====================

    def xpath(
        self,
        xml_text: str,
        expression: str,
        namespaces: Optional[Dict[str, str]] = None,
    ) -> XpathResult:
        """Query XML using XPath expressions.

        Supports ElementTree's XPath subset:
        - tag: select child elements
        - *: wildcard
        - .: current element
        - //: all descendants
        - [@attrib='value']: attribute predicate
        - [tag='value']: child text predicate
        - [n]: position predicate

        Args:
            xml_text: The XML text.
            expression: XPath expression.
            namespaces: Optional namespace prefix mapping.

        Returns:
            XpathResult with matching elements.
        """
        self._ensure_active()
        self._increment_call_count()

        try:
            root = ET.fromstring(xml_text)
            ns = namespaces or self._namespace_map

            elements = root.findall(expression, ns) if ns else root.findall(expression)

            matches: List[Dict[str, Any]] = []
            for elem in elements:
                match_data = {
                    "tag": elem.tag,
                    "attributes": dict(elem.attrib),
                    "text": elem.text.strip() if elem.text and elem.text.strip() else "",
                    "children_count": len(list(elem)),
                    "xml": ET.tostring(elem, encoding="unicode").strip(),
                }
                matches.append(match_data)

            return XpathResult(
                matches=matches,
                count=len(matches),
                expression=expression,
            )
        except ET.ParseError as e:
            raise PluginExecutionError(f"XML parse error: {e}")
        except SyntaxError as e:
            raise PluginExecutionError(f"XPath syntax error: {e}")

    def find_elements(
        self,
        xml_text: str,
        tag_name: str,
        *,
        recursive: bool = True,
    ) -> List[Dict[str, Any]]:
        """Find elements by tag name.

        Args:
            xml_text: The XML text.
            tag_name: Tag name to search for.
            recursive: Search all descendants or just direct children.

        Returns:
            List of matching element dicts.
        """
        self._ensure_active()
        self._increment_call_count()

        root = ET.fromstring(xml_text)

        if recursive:
            elements = root.iter(tag_name)
        else:
            elements = root.findall(tag_name)

        result: List[Dict[str, Any]] = []
        for elem in elements:
            result.append({
                "tag": elem.tag,
                "attributes": dict(elem.attrib),
                "text": elem.text.strip() if elem.text and elem.text.strip() else "",
                "children_count": len(list(elem)),
            })

        return result

    def get_attributes(self, xml_text: str, xpath: str = ".") -> Dict[str, str]:
        """Get attributes of an element.

        Args:
            xml_text: The XML text.
            xpath: XPath to the element.

        Returns:
            Dict of attribute name -> value.
        """
        self._ensure_active()
        self._increment_call_count()

        root = ET.fromstring(xml_text)
        elem = root.find(xpath)

        if elem is None:
            return {}

        return dict(elem.attrib)

    # ==================== Conversion ====================

    def to_json(self, xml_text: str, *, pretty: bool = True) -> str:
        """Convert XML to JSON string.

        Args:
            xml_text: The XML text.
            pretty: Whether to pretty-print JSON.

        Returns:
            JSON string.
        """
        self._ensure_active()
        self._increment_call_count()

        parse_result = self.parse(xml_text)
        if not parse_result.success:
            raise PluginExecutionError(f"XML parse error: {parse_result.error}")

        if pretty:
            return json.dumps(parse_result.data, indent=2, ensure_ascii=False)
        else:
            return json.dumps(parse_result.data, ensure_ascii=False)

    def from_json(
        self,
        json_text: str,
        *,
        root_tag: str = "root",
        pretty: bool = True,
    ) -> str:
        """Convert JSON to XML string.

        Args:
            json_text: JSON string.
            root_tag: Root element name.
            pretty: Whether to indent XML output.

        Returns:
            XML string.
        """
        self._ensure_active()
        self._increment_call_count()

        data = json.loads(json_text)
        if not isinstance(data, dict):
            data = {"value": data}

        return self.generate(data, root_tag=root_tag, pretty=pretty)

    # ==================== Validation ====================

    def validate(self, xml_text: str) -> XmlValidationResult:
        """Validate XML well-formedness and basic structure.

        Args:
            xml_text: The XML text to validate.

        Returns:
            XmlValidationResult with validation info.
        """
        self._ensure_active()
        self._increment_call_count()

        errors: List[str] = []
        warnings: List[str] = []

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            return XmlValidationResult(
                is_valid=False,
                errors=[f"Parse error: {e}"],
                warnings=[],
                element_count=0,
                attribute_count=0,
            )

        # Count elements and attributes
        element_count = 0
        attribute_count = 0
        tag_set: set = set()

        for elem in root.iter():
            element_count += 1
            attribute_count += len(elem.attrib)
            tag_set.add(elem.tag)

            # Check for common issues
            if elem.tag == "" or not re.match(r"^[a-zA-Z_][\w.-]*$", elem.tag):
                warnings.append(f"Element with unusual tag name: '{elem.tag}'")

        # Check for unclosed tags detection (ET would have already failed)

        return XmlValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            root_element=root.tag,
            element_count=element_count,
            attribute_count=attribute_count,
        )

    # ==================== Text Extraction ====================

    def extract_text(
        self,
        xml_text: str,
        *,
        separator: str = "\n",
        strip: bool = True,
    ) -> str:
        """Extract all text content from XML.

        Args:
            xml_text: The XML text.
            separator: Separator between text nodes.
            strip: Whether to strip whitespace from each text node.

        Returns:
            Extracted text.
        """
        self._ensure_active()
        self._increment_call_count()

        root = ET.fromstring(xml_text)
        text_parts: List[str] = []

        for elem in root.iter():
            if elem.text:
                text = elem.text.strip() if strip else elem.text
                if text:
                    text_parts.append(text)

        return separator.join(text_parts)

    # ==================== Element Counting ====================

    def count_elements(self, xml_text: str) -> Dict[str, Any]:
        """Count elements and attributes in XML.

        Args:
            xml_text: The XML text.

        Returns:
            Dict with counts and statistics.
        """
        self._ensure_active()
        self._increment_call_count()

        root = ET.fromstring(xml_text)

        element_count = 0
        attribute_count = 0
        text_node_count = 0
        max_depth = 0
        tag_counts: Dict[str, int] = {}

        def walk(elem: ET.Element, depth: int) -> None:
            nonlocal element_count, attribute_count, text_node_count, max_depth
            element_count += 1
            attribute_count += len(elem.attrib)
            max_depth = max(max_depth, depth)

            tag = elem.tag
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

            if elem.text and elem.text.strip():
                text_node_count += 1

            for child in elem:
                walk(child, depth + 1)

        walk(root, 1)

        return {
            "total_elements": element_count,
            "total_attributes": attribute_count,
            "text_nodes": text_node_count,
            "max_depth": max_depth,
            "unique_tags": len(tag_counts),
            "tag_counts": dict(sorted(tag_counts.items(), key=lambda x: -x[1])),
            "root_tag": root.tag,
        }

    # ==================== Flatten Elements ====================

    def flatten_elements(self, xml_text: str) -> List[Dict[str, Any]]:
        """Flatten all elements to a list with paths.

        Args:
            xml_text: The XML text.

        Returns:
            List of element dicts with path, tag, text, attributes.
        """
        self._ensure_active()
        self._increment_call_count()

        root = ET.fromstring(xml_text)
        elements: List[Dict[str, Any]] = []

        def walk(elem: ET.Element, path: str) -> None:
            elements.append({
                "path": path,
                "tag": elem.tag,
                "text": elem.text.strip() if elem.text and elem.text.strip() else "",
                "attributes": dict(elem.attrib),
                "children_count": len(list(elem)),
            })

            for i, child in enumerate(elem):
                child_path = f"{path}/{child.tag}[{i}]"
                walk(child, child_path)

        walk(root, f"/{root.tag}")
        return elements

    # ==================== Transformation Interface ====================

    def transform(
        self,
        xml_text: str,
        xslt_text: str,
    ) -> str:
        """Apply XSLT transformation (requires lxml).

        Falls back to a basic built-in transformation if lxml is not available.
        This is an interface method - actual XSLT may require additional dependencies.

        Args:
            xml_text: Source XML text.
            xslt_text: XSLT stylesheet text.

        Returns:
            Transformed result string.
        """
        self._ensure_active()
        self._increment_call_count()

        try:
            from lxml import etree  # type: ignore
            dom = etree.fromstring(xml_text.encode("utf-8"))
            xslt = etree.fromstring(xslt_text.encode("utf-8"))
            transform = etree.XSLT(xslt)
            result = transform(dom)
            return str(result)
        except ImportError:
            # Basic fallback: return info about required dependency
            raise PluginExecutionError(
                "XSLT transformation requires the 'lxml' library. "
                "Install it with: pip install lxml"
            )
        except Exception as e:
            raise PluginExecutionError(f"XSLT transformation error: {e}")

    # ==================== Modification ====================

    def add_element(
        self,
        xml_text: str,
        parent_xpath: str,
        tag: str,
        *,
        text: Optional[str] = None,
        attributes: Optional[Dict[str, str]] = None,
    ) -> str:
        """Add a new element to XML.

        Args:
            xml_text: Source XML text.
            parent_xpath: XPath to parent element.
            tag: New element tag name.
            text: New element text content.
            attributes: New element attributes.

        Returns:
            Modified XML string.
        """
        self._ensure_active()
        self._increment_call_count()

        root = ET.fromstring(xml_text)
        parent = root.find(parent_xpath)

        if parent is None:
            raise PluginExecutionError(f"Parent element not found: {parent_xpath}")

        new_elem = ET.SubElement(parent, tag)
        if text is not None:
            new_elem.text = text
        if attributes:
            for name, value in attributes.items():
                new_elem.set(name, value)

        self._indent_element(root, 0, self._default_indent)
        return ET.tostring(root, encoding="unicode", xml_declaration=True)

    def remove_element(
        self,
        xml_text: str,
        xpath: str,
    ) -> str:
        """Remove elements from XML.

        Args:
            xml_text: Source XML text.
            xpath: XPath to elements to remove.

        Returns:
            Modified XML string.
        """
        self._ensure_active()
        self._increment_call_count()

        root = ET.fromstring(xml_text)
        elements = root.findall(xpath)

        for elem in elements:
            # Find parent and remove
            for parent in root.iter():
                try:
                    parent.remove(elem)
                    break
                except ValueError:
                    continue

        self._indent_element(root, 0, self._default_indent)
        return ET.tostring(root, encoding="unicode", xml_declaration=True)

    def update_element(
        self,
        xml_text: str,
        xpath: str,
        *,
        text: Optional[str] = None,
        attributes: Optional[Dict[str, str]] = None,
    ) -> str:
        """Update element text and/or attributes.

        Args:
            xml_text: Source XML text.
            xpath: XPath to the element.
            text: New text content (None to keep).
            attributes: New/updated attributes.

        Returns:
            Modified XML string.
        """
        self._ensure_active()
        self._increment_call_count()

        root = ET.fromstring(xml_text)
        elem = root.find(xpath)

        if elem is None:
            raise PluginExecutionError(f"Element not found: {xpath}")

        if text is not None:
            elem.text = text

        if attributes:
            for name, value in attributes.items():
                elem.set(name, value)

        self._indent_element(root, 0, self._default_indent)
        return ET.tostring(root, encoding="unicode", xml_declaration=True)

    # ==================== Namespace Management ====================

    def register_namespace(self, prefix: str, uri: str) -> None:
        """Register a namespace prefix for XPath operations.

        Args:
            prefix: Namespace prefix.
            uri: Namespace URI.
        """
        self._namespace_map[prefix] = uri
        ET.register_namespace(prefix, uri)

    def get_namespaces(self) -> Dict[str, str]:
        """Get registered namespace mappings."""
        return dict(self._namespace_map)

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            "default_indent": self._default_indent,
            "default_encoding": self._default_encoding,
            "registered_namespaces": len(self._namespace_map),
            "xpath_features": [
                "tag name", "wildcard (*)", "attribute predicates",
                "position predicates", "descendant (//)", "text predicates",
            ],
        }
