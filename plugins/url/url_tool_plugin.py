"""
URL Toolkit Plugin for LiuHao AI OS

Provides URL parsing, validation, encoding/decoding, normalization,
query parameter manipulation, and URL expansion.

Capabilities:
- parse: Parse URL into components
- validate: Validate URL format
- encode: URL encode a string
- decode: URL decode a string
- normalize: Normalize a URL
- get_params: Get query parameters
- set_params: Set query parameters
- add_params: Add query parameters
- remove_params: Remove query parameters
- build_url: Build URL from components
- get_domain: Extract domain from URL
- get_base_url: Get base URL
"""

import re
import urllib.parse
from typing import Dict, Any, Optional, List, Tuple, Union
from dataclasses import dataclass, field

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class UrlComponents:
    """Parsed URL components."""
    scheme: str
    netloc: str
    path: str
    params: str
    query: str
    fragment: str
    username: Optional[str]
    password: Optional[str]
    hostname: Optional[str]
    port: Optional[int]
    query_params: Dict[str, List[str]]


@dataclass
class ValidationResult:
    """Result of URL validation."""
    is_valid: bool
    errors: List[str]
    warnings: List[str]
    scheme: Optional[str]
    is_absolute: bool
    is_secure: bool


class UrlToolPlugin(BasePlugin):
    """URL toolkit plugin.

    Provides URL parsing, validation, encoding, normalization,
    and query parameter manipulation.

    Example:
        plugin = UrlToolPlugin()
        plugin.initialize()
        plugin.activate()

        parsed = plugin.parse("https://example.com/path?query=value")
        print(parsed.hostname)
    """

    # Common TLDs for validation
    COMMON_TLDS = {
        "com", "org", "net", "edu", "gov", "mil", "int",
        "io", "co", "ai", "app", "dev", "xyz", "info",
        "biz", "online", "store", "tech", "site", "me",
        "us", "uk", "ca", "au", "de", "fr", "jp", "cn",
        "in", "br", "ru", "kr", "nl", "se", "no", "es",
        "it", "ch", "at", "be", "dk", "fi", "pl", "pt",
        "eu", "asia", "pro", "name", "mobi", "tv", "cc",
    }

    # URL regex for basic validation
    URL_PATTERN = re.compile(
        r"^(?:([a-zA-Z][a-zA-Z0-9+.-]*):\/\/)?"  # scheme
        r"(?:([^:@\s]+)(?::([^@\s]*))?@)?"  # user:password
        r"([a-zA-Z0-9.-]+|\[[a-fA-F0-9:]+\])"  # host
        r"(?::(\d+))?"  # port
        r"(\/[^?#]*)?"  # path
        r"(\?[^#]*)?"  # query
        r"(#.*)?$"  # fragment
    )

    def __init__(self) -> None:
        """Initialize the URL toolkit plugin."""
        metadata = PluginMetadata(
            name="url_tool",
            version="1.0.0",
            description="URL parsing, validation, encoding, and manipulation",
            author="LiuHao AI OS Team",
            categories=["web", "utility", "development"],
            keywords=["url", "uri", "parse", "encode", "decode", "query"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._default_scheme: str = "https"
        self._default_encoding: str = "utf-8"

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the URL toolkit plugin."""
        try:
            if config:
                self._default_scheme = config.get("default_scheme", "https")
                self._default_encoding = config.get("default_encoding", "utf-8")

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the URL toolkit plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the URL toolkit plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the URL toolkit plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "parse",
            "validate",
            "encode",
            "decode",
            "normalize",
            "get_params",
            "set_params",
            "add_params",
            "remove_params",
            "build_url",
            "get_domain",
            "get_base_url",
            "join_url",
            "is_external",
            "extract_urls",
        ]

    # ==================== Parsing ====================

    def parse(self, url: str) -> UrlComponents:
        """Parse a URL into its components.

        Args:
            url: The URL to parse.

        Returns:
            UrlComponents with parsed parts.
        """
        self._ensure_active()
        self._increment_call_count()

        # Add default scheme if missing
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
            url = f"{self._default_scheme}://{url}"

        parsed = urllib.parse.urlparse(url)

        # Parse query parameters
        query_params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)

        return UrlComponents(
            scheme=parsed.scheme,
            netloc=parsed.netloc,
            path=parsed.path or "/",
            params=parsed.params,
            query=parsed.query,
            fragment=parsed.fragment,
            username=parsed.username,
            password=parsed.password,
            hostname=parsed.hostname,
            port=parsed.port,
            query_params=query_params,
        )

    # ==================== Validation ====================

    def validate(self, url: str, *, require_scheme: bool = True) -> ValidationResult:
        """Validate a URL format.

        Args:
            url: The URL to validate.
            require_scheme: Whether a scheme (http://) is required.

        Returns:
            ValidationResult with validity and details.
        """
        self._ensure_active()
        self._increment_call_count()

        errors: List[str] = []
        warnings: List[str] = []
        scheme = None
        is_absolute = False
        is_secure = False

        if not url or not url.strip():
            return ValidationResult(
                is_valid=False,
                errors=["URL is empty"],
                warnings=[],
                scheme=None,
                is_absolute=False,
                is_secure=False,
            )

        url = url.strip()

        # Basic format check
        match = self.URL_PATTERN.match(url)
        if not match:
            return ValidationResult(
                is_valid=False,
                errors=["URL format is invalid"],
                warnings=[],
                scheme=None,
                is_absolute=False,
                is_secure=False,
            )

        scheme = match.group(1)
        host = match.group(4)
        port = match.group(5)

        if require_scheme and not scheme:
            errors.append("URL is missing scheme (e.g., http://)")

        if scheme:
            scheme = scheme.lower()
            if scheme not in ("http", "https", "ftp", "ftps", "sftp", "ssh", "ws", "wss", "mailto", "tel", "file"):
                warnings.append(f"Uncommon scheme: {scheme}")
            is_secure = scheme in ("https", "ftps", "sftp", "wss", "ssh")

        # Check host
        if host:
            if host.startswith("[") and host.endswith("]"):
                # IPv6 address
                pass
            elif not self._is_valid_hostname(host):
                errors.append(f"Invalid hostname: {host}")
            else:
                # Check TLD
                parts = host.split(".")
                if len(parts) >= 2:
                    tld = parts[-1].lower()
                    if len(tld) < 2:
                        errors.append(f"Invalid TLD: {tld}")

        # Check port
        if port:
            try:
                port_num = int(port)
                if port_num < 1 or port_num > 65535:
                    errors.append(f"Invalid port: {port_num} (must be 1-65535)")
            except ValueError:
                errors.append(f"Invalid port: {port}")

        is_absolute = scheme is not None and host is not None

        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            scheme=scheme,
            is_absolute=is_absolute,
            is_secure=is_secure,
        )

    def _is_valid_hostname(self, hostname: str) -> bool:
        """Check if a hostname is valid."""
        if len(hostname) > 253:
            return False

        if hostname.startswith(".") or hostname.endswith("."):
            return False

        # Check each label
        labels = hostname.split(".")
        if not labels:
            return False

        for label in labels:
            if not label or len(label) > 63:
                return False
            if not re.match(r"^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?$", label):
                return False

        return True

    # ==================== Encoding / Decoding ====================

    def encode(
        self,
        text: str,
        *,
        safe: str = "",
        encoding: Optional[str] = None,
    ) -> str:
        """URL-encode a string.

        Args:
            text: Text to encode.
            safe: Characters that should not be encoded.
            encoding: Character encoding.

        Returns:
            URL-encoded string.
        """
        self._ensure_active()
        self._increment_call_count()

        enc = encoding or self._default_encoding
        return urllib.parse.quote(text, safe=safe, encoding=enc)

    def decode(
        self,
        text: str,
        *,
        encoding: Optional[str] = None,
    ) -> str:
        """URL-decode a string.

        Args:
            text: URL-encoded text.
            encoding: Character encoding.

        Returns:
            Decoded string.
        """
        self._ensure_active()
        self._increment_call_count()

        enc = encoding or self._default_encoding
        return urllib.parse.unquote(text, encoding=enc)

    def encode_component(
        self,
        text: str,
        *,
        encoding: Optional[str] = None,
    ) -> str:
        """Encode a URL component (more aggressive encoding).

        Args:
            text: Text to encode.
            encoding: Character encoding.

        Returns:
            Encoded component string.
        """
        self._ensure_active()
        self._increment_call_count()

        enc = encoding or self._default_encoding
        return urllib.parse.quote_plus(text, encoding=enc)

    def decode_component(
        self,
        text: str,
        *,
        encoding: Optional[str] = None,
    ) -> str:
        """Decode a URL component (handles + as space).

        Args:
            text: Encoded text.
            encoding: Character encoding.

        Returns:
            Decoded string.
        """
        self._ensure_active()
        self._increment_call_count()

        enc = encoding or self._default_encoding
        return urllib.parse.unquote_plus(text, encoding=enc)

    # ==================== Normalization ====================

    def normalize(self, url: str, *, scheme: Optional[str] = None) -> str:
        """Normalize a URL to standard format.

        Applies standard normalization: lowercasing scheme and host,
        removing default ports, removing duplicate slashes,
        decoding unnecessary encodings, sorting query params, etc.

        Args:
            url: The URL to normalize.
            scheme: Default scheme to add if missing.

        Returns:
            Normalized URL string.
        """
        self._ensure_active()
        self._increment_call_count()

        default_scheme = scheme or self._default_scheme

        # Add scheme if missing
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
            url = f"{default_scheme}://{url}"

        parsed = urllib.parse.urlparse(url)

        # Lowercase scheme and host
        scheme_lower = parsed.scheme.lower()
        netloc = parsed.netloc.lower()

        # Remove default port
        if parsed.port:
            if (scheme_lower == "http" and parsed.port == 80) or \
               (scheme_lower == "https" and parsed.port == 443) or \
               (scheme_lower == "ftp" and parsed.port == 21):
                if parsed.username:
                    auth = f"{parsed.username}"
                    if parsed.password:
                        auth += f":{parsed.password}"
                    netloc = f"{auth}@{parsed.hostname}"
                else:
                    netloc = parsed.hostname or ""

        # Normalize path
        path = parsed.path or "/"
        path = re.sub(r"/+", "/", path)  # Remove duplicate slashes

        # Remove dot segments
        path = self._remove_dot_segments(path)

        # Sort query parameters
        if parsed.query:
            params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            params.sort(key=lambda x: x[0])
            query = urllib.parse.urlencode(params)
        else:
            query = ""

        # Remove fragment? (keep it, but normalize)
        fragment = parsed.fragment

        # Reconstruct URL
        normalized = urllib.parse.urlunparse((
            scheme_lower,
            netloc,
            path,
            parsed.params,
            query,
            fragment,
        ))

        return normalized

    def _remove_dot_segments(self, path: str) -> str:
        """Remove . and .. segments from a path."""
        segments = path.split("/")
        result: List[str] = []

        for segment in segments:
            if segment == ".":
                continue
            elif segment == "..":
                if result and result[-1] != "":
                    result.pop()
            else:
                result.append(segment)

        return "/".join(result) or "/"

    # ==================== Query Parameters ====================

    def get_params(self, url: str) -> Dict[str, List[str]]:
        """Get all query parameters from a URL.

        Args:
            url: The URL.

        Returns:
            Dict of parameter name -> list of values.
        """
        self._ensure_active()
        self._increment_call_count()

        parsed = self.parse(url)
        return parsed.query_params

    def get_param(self, url: str, name: str, default: Optional[str] = None) -> Optional[str]:
        """Get a single query parameter value.

        Args:
            url: The URL.
            name: Parameter name.
            default: Default value if not found.

        Returns:
            Parameter value (first value) or default.
        """
        self._ensure_active()
        self._increment_call_count()

        params = self.get_params(url)
        values = params.get(name, [])
        return values[0] if values else default

    def set_params(
        self,
        url: str,
        params: Dict[str, Union[str, List[str]]],
    ) -> str:
        """Set query parameters on a URL (replaces existing).

        Args:
            url: The base URL.
            params: Parameters to set.

        Returns:
            URL with new query parameters.
        """
        self._ensure_active()
        self._increment_call_count()

        parsed = urllib.parse.urlparse(url)

        # Build flat param list
        flat_params: List[Tuple[str, str]] = []
        for key, value in params.items():
            if isinstance(value, list):
                for v in value:
                    flat_params.append((key, str(v)))
            else:
                flat_params.append((key, str(value)))

        query = urllib.parse.urlencode(flat_params)

        return urllib.parse.urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            query,
            parsed.fragment,
        ))

    def add_params(
        self,
        url: str,
        params: Dict[str, Union[str, List[str]]],
    ) -> str:
        """Add query parameters to a URL (appends to existing).

        Args:
            url: The base URL.
            params: Parameters to add.

        Returns:
            URL with added query parameters.
        """
        self._ensure_active()
        self._increment_call_count()

        # Get existing params
        existing = self.get_params(url)

        # Add new params
        for key, value in params.items():
            if isinstance(value, list):
                existing[key] = existing.get(key, []) + value
            else:
                existing.setdefault(key, []).append(str(value))

        return self.set_params(url, existing)

    def remove_params(
        self,
        url: str,
        param_names: List[str],
    ) -> str:
        """Remove query parameters from a URL.

        Args:
            url: The base URL.
            param_names: Names of parameters to remove.

        Returns:
            URL with specified parameters removed.
        """
        self._ensure_active()
        self._increment_call_count()

        params = self.get_params(url)

        for name in param_names:
            params.pop(name, None)

        return self.set_params(url, params)

    def clear_params(self, url: str) -> str:
        """Remove all query parameters from a URL.

        Args:
            url: The URL.

        Returns:
            URL without query parameters.
        """
        self._ensure_active()
        self._increment_call_count()

        parsed = urllib.parse.urlparse(url)
        return urllib.parse.urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            "",
            parsed.fragment,
        ))

    # ==================== URL Building ====================

    def build_url(
        self,
        *,
        scheme: Optional[str] = None,
        host: str,
        path: str = "/",
        params: Optional[Dict[str, Any]] = None,
        query: Optional[Dict[str, Any]] = None,
        fragment: str = "",
        port: Optional[int] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ) -> str:
        """Build a URL from components.

        Args:
            scheme: URL scheme (http, https, etc.).
            host: Hostname.
            path: URL path.
            params: Path parameters.
            query: Query parameters.
            fragment: Fragment identifier.
            port: Port number.
            username: Auth username.
            password: Auth password.

        Returns:
            Built URL string.
        """
        self._ensure_active()
        self._increment_call_count()

        scheme = scheme or self._default_scheme

        # Build netloc
        netloc = host
        if port:
            netloc = f"{host}:{port}"

        if username:
            auth = username
            if password:
                auth += f":{password}"
            netloc = f"{auth}@{netloc}"

        # Build query string
        query_str = ""
        if query:
            flat_params: List[Tuple[str, str]] = []
            for key, value in query.items():
                if isinstance(value, list):
                    for v in value:
                        flat_params.append((key, str(v)))
                else:
                    flat_params.append((key, str(value)))
            query_str = urllib.parse.urlencode(flat_params)

        path_params = params or {}
        param_str = ""
        if isinstance(params, dict):
            param_str = ";".join(f"{k}={v}" for k, v in params.items())

        return urllib.parse.urlunparse((
            scheme,
            netloc,
            path,
            param_str,
            query_str,
            fragment,
        ))

    # ==================== Domain & Base URL ====================

    def get_domain(self, url: str, *, include_subdomain: bool = True) -> str:
        """Extract the domain from a URL.

        Args:
            url: The URL.
            include_subdomain: Whether to include subdomains.

        Returns:
            Domain string.
        """
        self._ensure_active()
        self._increment_call_count()

        parsed = self.parse(url)
        hostname = parsed.hostname or ""

        if include_subdomain:
            return hostname

        # Try to extract root domain (simple approach)
        parts = hostname.split(".")
        if len(parts) >= 2:
            # Handle country-code TLDs like .co.uk
            if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in ("co", "com", "org", "net", "gov", "edu"):
                return ".".join(parts[-3:])
            else:
                return ".".join(parts[-2:])

        return hostname

    def get_base_url(self, url: str) -> str:
        """Get the base URL (scheme + host + path without query/fragment).

        Args:
            url: The URL.

        Returns:
            Base URL string.
        """
        self._ensure_active()
        self._increment_call_count()

        parsed = urllib.parse.urlparse(url)
        return urllib.parse.urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            "",
            "",
            "",
        ))

    def join_url(self, base_url: str, relative_url: str) -> str:
        """Join a base URL with a relative URL.

        Args:
            base_url: Base URL.
            relative_url: Relative URL to join.

        Returns:
            Joined absolute URL.
        """
        self._ensure_active()
        self._increment_call_count()

        return urllib.parse.urljoin(base_url, relative_url)

    def is_external(self, url: str, base_domain: str) -> bool:
        """Check if a URL points to an external domain.

        Args:
            url: The URL to check.
            base_domain: Base domain to compare against.

        Returns:
            True if the URL is external.
        """
        self._ensure_active()
        self._increment_call_count()

        parsed = self.parse(url)
        hostname = parsed.hostname or ""

        if not hostname:
            return False  # Relative URL is internal

        return hostname.lower() != base_domain.lower() and \
            not hostname.lower().endswith("." + base_domain.lower())

    # ==================== URL Extraction ====================

    def extract_urls(self, text: str) -> List[str]:
        """Extract all URLs from a text.

        Args:
            text: Input text.

        Returns:
            List of URL strings found.
        """
        self._ensure_active()
        self._increment_call_count()

        # URL regex pattern
        url_pattern = re.compile(
            r"https?://"  # scheme
            r"[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?"  # subdomain
            r"(\.[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?)*"  # domain parts
            r"\.[a-zA-Z]{2,}"  # TLD
            r"(:\d{1,5})?"  # port
            r"(/[^\s]*)?"  # path
            r"(\?[^\s#]*)?"  # query
            r"(#[^\s]*)?",  # fragment
            re.IGNORECASE,
        )

        urls = []
        for match in url_pattern.finditer(text):
            url = match.group(0).rstrip(".,;:!?)\"'")
            if len(url) > 10:  # Minimum reasonable URL length
                urls.append(url)

        return list(dict.fromkeys(urls))  # Remove duplicates, preserve order

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            "default_scheme": self._default_scheme,
            "default_encoding": self._default_encoding,
            "common_tlds": len(self.COMMON_TLDS),
            "features": [
                "parse", "validate", "encode", "decode",
                "normalize", "query params manipulation",
                "build url", "domain extraction",
                "url extraction from text",
            ],
        }
