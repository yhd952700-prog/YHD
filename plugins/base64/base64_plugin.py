"""
Base64 Tool Plugin for LiuHao AI OS

Provides Base64 encode/decode, Base64URL variant, file to base64,
base64 to file, and multiple variant support.

Capabilities:
- encode: Base64 encode text
- decode: Base64 decode to text
- encode_bytes: Base64 encode raw bytes
- decode_bytes: Base64 decode to raw bytes
- url_encode: Base64URL encode
- url_decode: Base64URL decode
- file_to_base64: Encode file content to base64
- base64_to_file: Decode base64 and write to file
- detect_variant: Detect base64 variant
- validate: Validate base64 string
- batch_encode: Batch encode multiple values
"""

import base64
import re
import os
from typing import Dict, Any, Optional, List, Tuple, Union
from dataclasses import dataclass, field

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class Base64Result:
    """Result of a base64 operation."""
    success: bool
    data: str = ""
    bytes_data: Optional[bytes] = None
    original_size: int = 0
    encoded_size: int = 0
    error: Optional[str] = None
    variant: str = "standard"


@dataclass
class ValidationResult:
    """Result of base64 validation."""
    is_valid: bool
    variant: str  # standard, url_safe, unknown
    padding: bool
    issues: List[str]
    estimated_decoded_size: int


class Base64Plugin(BasePlugin):
    """Base64 encoding/decoding plugin.

    Supports standard Base64, Base64URL (URL-safe), file encoding,
    and batch operations.

    Example:
        plugin = Base64Plugin()
        plugin.initialize()
        plugin.activate()

        encoded = plugin.encode("Hello, World!")
        print(encoded.data)
    """

    # Base64 character sets
    STANDARD_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
    URLSAFE_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_="

    def __init__(self) -> None:
        """Initialize the base64 plugin."""
        metadata = PluginMetadata(
            name="base64",
            version="1.0.0",
            description="Base64 encode/decode with URL-safe variant and file support",
            author="LiuHao AI OS Team",
            categories=["encoding", "utility", "security"],
            keywords=["base64", "encode", "decode", "base64url", "encoding"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._default_variant: str = "standard"
        self._default_encoding: str = "utf-8"
        self._line_length: int = 0  # 0 = no line wrapping

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the base64 plugin."""
        try:
            if config:
                self._default_variant = config.get("default_variant", "standard")
                self._default_encoding = config.get("default_encoding", "utf-8")
                self._line_length = config.get("line_length", 0)

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the base64 plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the base64 plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the base64 plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "encode",
            "decode",
            "encode_bytes",
            "decode_bytes",
            "url_encode",
            "url_decode",
            "file_to_base64",
            "base64_to_file",
            "detect_variant",
            "validate",
            "batch_encode",
            "batch_decode",
            "get_data_uri",
        ]

    # ==================== Encode ====================

    def encode(
        self,
        text: str,
        *,
        variant: Optional[str] = None,
        encoding: Optional[str] = None,
        padding: bool = True,
    ) -> Base64Result:
        """Encode a text string to Base64.

        Args:
            text: The text to encode.
            variant: 'standard' or 'url_safe'.
            encoding: Text encoding (utf-8, etc.).
            padding: Whether to include padding (=).

        Returns:
            Base64Result with encoded string.
        """
        self._ensure_active()
        self._increment_call_count()

        variant = variant or self._default_variant
        enc = encoding or self._default_encoding

        try:
            data_bytes = text.encode(enc)
            encoded = self._encode_bytes(data_bytes, variant, padding)

            if self._line_length > 0:
                encoded = self._wrap_lines(encoded, self._line_length)

            return Base64Result(
                success=True,
                data=encoded,
                original_size=len(data_bytes),
                encoded_size=len(encoded),
                variant=variant,
            )
        except UnicodeEncodeError as e:
            return Base64Result(
                success=False,
                error=f"Encoding error: {e}",
                variant=variant,
            )
        except Exception as e:
            return Base64Result(
                success=False,
                error=str(e),
                variant=variant,
            )

    def encode_bytes(
        self,
        data: bytes,
        *,
        variant: Optional[str] = None,
        padding: bool = True,
    ) -> Base64Result:
        """Encode raw bytes to Base64.

        Args:
            data: The bytes to encode.
            variant: 'standard' or 'url_safe'.
            padding: Whether to include padding.

        Returns:
            Base64Result with encoded string.
        """
        self._ensure_active()
        self._increment_call_count()

        variant = variant or self._default_variant

        try:
            encoded = self._encode_bytes(data, variant, padding)

            if self._line_length > 0:
                encoded = self._wrap_lines(encoded, self._line_length)

            return Base64Result(
                success=True,
                data=encoded,
                original_size=len(data),
                encoded_size=len(encoded),
                variant=variant,
            )
        except Exception as e:
            return Base64Result(
                success=False,
                error=str(e),
                variant=variant,
            )

    def _encode_bytes(self, data: bytes, variant: str, padding: bool) -> str:
        """Encode bytes to base64 string."""
        if variant == "url_safe":
            encoded = base64.urlsafe_b64encode(data).decode("ascii")
        else:
            encoded = base64.b64encode(data).decode("ascii")

        if not padding:
            encoded = encoded.rstrip("=")

        return encoded

    # ==================== Decode ====================

    def decode(
        self,
        encoded: str,
        *,
        variant: Optional[str] = None,
        encoding: Optional[str] = None,
    ) -> Base64Result:
        """Decode a Base64 string to text.

        Args:
            encoded: The base64-encoded string.
            variant: 'standard', 'url_safe', or 'auto' to detect.
            encoding: Output text encoding.

        Returns:
            Base64Result with decoded text.
        """
        self._ensure_active()
        self._increment_call_count()

        enc = encoding or self._default_encoding

        try:
            # Clean up whitespace and newlines
            cleaned = self._clean_encoded(encoded)

            # Detect variant if auto
            if variant == "auto" or variant is None:
                variant = self._detect_variant_str(cleaned)

            decoded_bytes = self._decode_to_bytes(cleaned, variant)
            decoded_text = decoded_bytes.decode(enc)

            return Base64Result(
                success=True,
                data=decoded_text,
                bytes_data=decoded_bytes,
                original_size=len(cleaned),
                encoded_size=len(cleaned),
                variant=variant,
            )
        except UnicodeDecodeError as e:
            return Base64Result(
                success=False,
                error=f"Decoding error: {e}",
                variant=variant or "unknown",
            )
        except Exception as e:
            return Base64Result(
                success=False,
                error=str(e),
                variant=variant or "unknown",
            )

    def decode_bytes(
        self,
        encoded: str,
        *,
        variant: Optional[str] = None,
    ) -> Base64Result:
        """Decode a Base64 string to raw bytes.

        Args:
            encoded: The base64-encoded string.
            variant: 'standard', 'url_safe', or 'auto'.

        Returns:
            Base64Result with decoded bytes.
        """
        self._ensure_active()
        self._increment_call_count()

        try:
            cleaned = self._clean_encoded(encoded)

            if variant == "auto" or variant is None:
                variant = self._detect_variant_str(cleaned)

            decoded_bytes = self._decode_to_bytes(cleaned, variant)

            return Base64Result(
                success=True,
                data="",
                bytes_data=decoded_bytes,
                original_size=len(decoded_bytes),
                encoded_size=len(cleaned),
                variant=variant,
            )
        except Exception as e:
            return Base64Result(
                success=False,
                error=str(e),
                variant=variant or "unknown",
            )

    def _decode_to_bytes(self, encoded: str, variant: str) -> bytes:
        """Decode base64 string to bytes."""
        # Add padding if needed
        padding_needed = (4 - len(encoded) % 4) % 4
        if padding_needed:
            if variant == "url_safe":
                encoded += "=" * padding_needed
            else:
                encoded += "=" * padding_needed

        if variant == "url_safe":
            # First convert URL-safe to standard if needed
            standard = encoded.replace("-", "+").replace("_", "/")
            return base64.b64decode(standard)
        else:
            return base64.b64decode(encoded)

    def _clean_encoded(self, encoded: str) -> str:
        """Remove whitespace and newlines from encoded string."""
        return re.sub(r"[\s\r\n]+", "", encoded)

    # ==================== URL-Safe Shortcuts ====================

    def url_encode(self, text: str, *, padding: bool = False) -> Base64Result:
        """Encode text to Base64URL (URL-safe variant).

        Args:
            text: The text to encode.
            padding: Whether to include padding (default False for URL).

        Returns:
            Base64Result with URL-safe encoded string.
        """
        return self.encode(text, variant="url_safe", padding=padding)

    def url_decode(self, encoded: str) -> Base64Result:
        """Decode a Base64URL string to text.

        Args:
            encoded: The URL-safe base64 string.

        Returns:
            Base64Result with decoded text.
        """
        return self.decode(encoded, variant="url_safe")

    # ==================== File Operations ====================

    def file_to_base64(
        self,
        filepath: str,
        *,
        variant: Optional[str] = None,
        padding: bool = True,
    ) -> Base64Result:
        """Encode a file's content to Base64.

        Args:
            filepath: Path to the file.
            variant: Base64 variant.
            padding: Whether to include padding.

        Returns:
            Base64Result with encoded file content.
        """
        self._ensure_active()
        self._increment_call_count()

        variant = variant or self._default_variant

        try:
            if not os.path.exists(filepath):
                return Base64Result(
                    success=False,
                    error=f"File not found: {filepath}",
                    variant=variant,
                )

            with open(filepath, "rb") as f:
                file_data = f.read()

            encoded = self._encode_bytes(file_data, variant, padding)

            return Base64Result(
                success=True,
                data=encoded,
                original_size=len(file_data),
                encoded_size=len(encoded),
                variant=variant,
            )
        except PermissionError as e:
            return Base64Result(
                success=False,
                error=f"Permission denied: {e}",
                variant=variant,
            )
        except Exception as e:
            return Base64Result(
                success=False,
                error=str(e),
                variant=variant,
            )

    def base64_to_file(
        self,
        encoded: str,
        filepath: str,
        *,
        variant: Optional[str] = None,
        overwrite: bool = False,
    ) -> Base64Result:
        """Decode Base64 and write to a file.

        Args:
            encoded: The base64-encoded string.
            filepath: Output file path.
            variant: Base64 variant.
            overwrite: Whether to overwrite existing file.

        Returns:
            Base64Result with operation status.
        """
        self._ensure_active()
        self._increment_call_count()

        variant = variant or self._default_variant

        try:
            if os.path.exists(filepath) and not overwrite:
                return Base64Result(
                    success=False,
                    error=f"File already exists: {filepath}",
                    variant=variant,
                )

            cleaned = self._clean_encoded(encoded)

            if variant == "auto":
                variant = self._detect_variant_str(cleaned)

            decoded_bytes = self._decode_to_bytes(cleaned, variant)

            # Create directory if needed
            dirpath = os.path.dirname(filepath)
            if dirpath and not os.path.exists(dirpath):
                os.makedirs(dirpath, exist_ok=True)

            with open(filepath, "wb") as f:
                f.write(decoded_bytes)

            return Base64Result(
                success=True,
                data=filepath,
                bytes_data=decoded_bytes,
                original_size=len(decoded_bytes),
                encoded_size=len(cleaned),
                variant=variant,
            )
        except Exception as e:
            return Base64Result(
                success=False,
                error=str(e),
                variant=variant,
            )

    # ==================== Data URI ====================

    def get_data_uri(
        self,
        data: Union[str, bytes],
        mime_type: str = "application/octet-stream",
        *,
        is_file: bool = False,
    ) -> str:
        """Generate a data URI string.

        Args:
            data: Text, bytes, or file path (if is_file=True).
            mime_type: MIME type for the data URI.
            is_file: Whether data is a file path.

        Returns:
            Data URI string (data:<mime_type>;base64,<data>).
        """
        self._ensure_active()
        self._increment_call_count()

        if is_file:
            result = self.file_to_base64(data)
            if not result.success:
                raise PluginExecutionError(result.error or "Failed to encode file")
            encoded = result.data
        elif isinstance(data, bytes):
            result = self.encode_bytes(data)
            encoded = result.data
        else:
            result = self.encode(data)
            encoded = result.data

        return f"data:{mime_type};base64,{encoded}"

    def parse_data_uri(self, data_uri: str) -> Dict[str, Any]:
        """Parse a data URI string.

        Args:
            data_uri: The data URI string.

        Returns:
            Dict with mime_type, base64_data, is_base64, decoded_text.
        """
        self._ensure_active()
        self._increment_call_count()

        if not data_uri.startswith("data:"):
            raise ValueError("Not a valid data URI")

        content = data_uri[5:]  # Remove "data:"
        parts = content.split(",", 1)

        if len(parts) != 2:
            raise ValueError("Invalid data URI format")

        header, encoded_data = parts

        # Parse header
        mime_type = "text/plain"
        is_base64 = False
        params = {}

        header_parts = header.split(";")
        if header_parts and header_parts[0]:
            mime_type = header_parts[0]

        for part in header_parts[1:]:
            if part == "base64":
                is_base64 = True
            elif "=" in part:
                key, value = part.split("=", 1)
                params[key] = value

        result: Dict[str, Any] = {
            "mime_type": mime_type,
            "is_base64": is_base64,
            "params": params,
            "raw_data": encoded_data,
        }

        if is_base64:
            decode_result = self.decode(encoded_data)
            result["decoded_text"] = decode_result.data if decode_result.success else ""
            result["decoded_bytes"] = decode_result.bytes_data

        return result

    # ==================== Validation & Detection ====================

    def validate(self, encoded: str) -> ValidationResult:
        """Validate a Base64 string.

        Args:
            encoded: The string to validate.

        Returns:
            ValidationResult with validity and details.
        """
        self._ensure_active()
        self._increment_call_count()

        cleaned = self._clean_encoded(encoded)
        issues: List[str] = []

        # Check for empty
        if not cleaned:
            return ValidationResult(
                is_valid=False,
                variant="unknown",
                padding=False,
                issues=["Empty string"],
                estimated_decoded_size=0,
            )

        # Detect variant
        variant = self._detect_variant_str(cleaned)

        # Check valid characters
        if variant == "standard":
            valid_chars = set(self.STANDARD_CHARS)
        elif variant == "url_safe":
            valid_chars = set(self.URLSAFE_CHARS)
        else:
            valid_chars = set(self.STANDARD_CHARS)

        invalid_chars = set()
        for c in cleaned:
            if c not in valid_chars:
                invalid_chars.add(c)

        if invalid_chars:
            issues.append(f"Invalid characters: {''.join(sorted(invalid_chars))}")

        # Check padding
        has_padding = cleaned.endswith("=")
        padding_count = len(cleaned) - len(cleaned.rstrip("="))

        if has_padding:
            if padding_count > 2:
                issues.append(f"Too many padding characters: {padding_count}")
            # Check length is multiple of 4
            if len(cleaned) % 4 != 0:
                issues.append("Length is not a multiple of 4")
        else:
            # Without padding, check if length mod 4 is 0, 2, or 3
            remainder = len(cleaned) % 4
            if remainder == 1:
                issues.append("Invalid length for unpadded base64")

        # Estimate decoded size
        if has_padding:
            estimated = len(cleaned) // 4 * 3 - padding_count
        else:
            estimated = len(cleaned) * 3 // 4

        return ValidationResult(
            is_valid=len(issues) == 0,
            variant=variant,
            padding=has_padding,
            issues=issues,
            estimated_decoded_size=max(0, estimated),
        )

    def detect_variant(self, encoded: str) -> str:
        """Detect which Base64 variant a string uses.

        Args:
            encoded: The base64 string.

        Returns:
            'standard', 'url_safe', or 'unknown'.
        """
        self._ensure_active()
        self._increment_call_count()

        cleaned = self._clean_encoded(encoded)
        return self._detect_variant_str(cleaned)

    def _detect_variant_str(self, encoded: str) -> str:
        """Detect base64 variant from cleaned string."""
        if not encoded:
            return "unknown"

        has_plus = "+" in encoded
        has_slash = "/" in encoded
        has_minus = "-" in encoded
        has_underscore = "_" in encoded

        if has_plus or has_slash:
            return "standard"
        elif has_minus or has_underscore:
            return "url_safe"
        else:
            # Could be either (alphanumeric only)
            return "standard"  # Default to standard

    # ==================== Batch Operations ====================

    def batch_encode(
        self,
        items: List[str],
        *,
        variant: Optional[str] = None,
    ) -> List[Base64Result]:
        """Batch encode multiple strings.

        Args:
            items: List of strings to encode.
            variant: Base64 variant.

        Returns:
            List of Base64Result objects.
        """
        self._ensure_active()
        self._increment_call_count()

        return [self.encode(item, variant=variant) for item in items]

    def batch_decode(
        self,
        items: List[str],
        *,
        variant: Optional[str] = None,
    ) -> List[Base64Result]:
        """Batch decode multiple base64 strings.

        Args:
            items: List of base64 strings to decode.
            variant: Base64 variant.

        Returns:
            List of Base64Result objects.
        """
        self._ensure_active()
        self._increment_call_count()

        return [self.decode(item, variant=variant) for item in items]

    # ==================== Utility ====================

    def _wrap_lines(self, text: str, length: int) -> str:
        """Wrap text to specified line length."""
        return "\n".join(text[i:i + length] for i in range(0, len(text), length))

    def convert_variant(
        self,
        encoded: str,
        target_variant: str,
        *,
        padding: Optional[bool] = None,
    ) -> str:
        """Convert between Base64 variants.

        Args:
            encoded: The base64 string.
            target_variant: Target variant ('standard' or 'url_safe').
            padding: Target padding (keep current if None).

        Returns:
            Converted base64 string.
        """
        self._ensure_active()
        self._increment_call_count()

        cleaned = self._clean_encoded(encoded)
        has_padding = cleaned.endswith("=")

        if target_variant == "url_safe":
            result = cleaned.replace("+", "-").replace("/", "_")
        else:
            result = cleaned.replace("-", "+").replace("_", "/")

        if padding is True:
            padding_needed = (4 - len(result) % 4) % 4
            result += "=" * padding_needed
        elif padding is False:
            result = result.rstrip("=")
        # If None, keep current padding

        return result

    def get_ratio(self, original_size: int) -> Dict[str, float]:
        """Get Base64 size ratio information.

        Args:
            original_size: Original size in bytes.

        Returns:
            Dict with encoded_size, ratio, overhead.
        """
        encoded_size = ((original_size + 2) // 3) * 4
        ratio = encoded_size / original_size if original_size > 0 else 0
        overhead = ratio - 1

        return {
            "original_size": original_size,
            "encoded_size": encoded_size,
            "expansion_ratio": round(ratio, 4),
            "overhead_percent": round(overhead * 100, 2),
        }

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            "default_variant": self._default_variant,
            "default_encoding": self._default_encoding,
            "line_length": self._line_length,
            "variants": ["standard", "url_safe"],
            "features": [
                "text encode/decode",
                "bytes encode/decode",
                "file encode/decode",
                "data URI",
                "variant detection",
                "validation",
                "batch operations",
            ],
        }
