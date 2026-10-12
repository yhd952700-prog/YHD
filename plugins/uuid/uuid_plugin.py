"""
UUID Generation Plugin for LiuHao AI OS

Provides UUID generation for v1, v3, v4, v5, v7, batch generation,
validation, and namespace UUID generation.

Capabilities:
- v1: Generate UUID v1 (time-based)
- v3: Generate UUID v3 (name-based, MD5)
- v4: Generate UUID v4 (random)
- v5: Generate UUID v5 (name-based, SHA-1)
- v7: Generate UUID v7 (time-ordered, Unix time)
- batch: Generate multiple UUIDs
- validate: Validate UUID string format
- parse: Parse UUID string to components
- namespace: Generate namespace UUIDs
- nil: Get nil UUID (all zeros)
"""

import uuid
import re
import time
import secrets
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class UuidInfo:
    """Detailed UUID information."""
    uuid_str: str
    version: int
    variant: str
    is_valid: bool
    timestamp: Optional[int] = None
    timestamp_iso: Optional[str] = None
    clock_sequence: Optional[int] = None
    node: Optional[str] = None
    components: Dict[str, str] = field(default_factory=dict)


@dataclass
class BatchUuidResult:
    """Result of batch UUID generation."""
    uuids: List[str]
    count: int
    version: int
    duplicates: int


class UuidPlugin(BasePlugin):
    """UUID generation and validation plugin.

    Supports UUID versions 1, 3, 4, 5, and 7 with batch generation,
    validation, and detailed parsing.

    Example:
        plugin = UuidPlugin()
        plugin.initialize()
        plugin.activate()

        uid = plugin.v4()
        print(uid)
    """

    # Standard namespace UUIDs
    NAMESPACES = {
        "dns": uuid.NAMESPACE_DNS,
        "url": uuid.NAMESPACE_URL,
        "oid": uuid.NAMESPACE_OID,
        "x500": uuid.NAMESPACE_X500,
    }

    # UUID regex pattern
    UUID_PATTERN = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        re.IGNORECASE,
    )

    def __init__(self) -> None:
        """Initialize the UUID plugin."""
        metadata = PluginMetadata(
            name="uuid",
            version="1.0.0",
            description="UUID generation (v1, v3, v4, v5, v7) and validation",
            author="LiuHao AI OS Team",
            categories=["utility", "identifier", "development"],
            keywords=["uuid", "guid", "identifier", "random", "unique"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._default_version: int = 4
        self._default_case: str = "lower"

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the UUID plugin."""
        try:
            if config:
                self._default_version = config.get("default_version", 4)
                self._default_case = config.get("default_case", "lower")

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the UUID plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the UUID plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the UUID plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "generate",
            "v1",
            "v3",
            "v4",
            "v5",
            "v7",
            "batch",
            "validate",
            "parse",
            "namespace",
            "nil",
            "to_string",
            "from_string",
            "compare",
        ]

    # ==================== UUID Generation ====================

    def generate(self, version: Optional[int] = None, **kwargs: Any) -> str:
        """Generate a UUID of specified version.

        Args:
            version: UUID version (1, 3, 4, 5, 7). Default from config.
            **kwargs: Additional arguments for specific versions.

        Returns:
            UUID string.
        """
        self._ensure_active()
        self._increment_call_count()

        version = version or self._default_version

        if version == 1:
            return self.v1(**kwargs)
        elif version == 3:
            return self.v3(**kwargs)
        elif version == 4:
            return self.v4()
        elif version == 5:
            return self.v5(**kwargs)
        elif version == 7:
            return self.v7(**kwargs)
        else:
            raise ValueError(f"Unsupported UUID version: {version}. Use 1, 3, 4, 5, or 7.")

    def v1(self, *, node: Optional[int] = None, clock_seq: Optional[int] = None) -> str:
        """Generate a UUID v1 (time-based).

        Uses the MAC address and current time.

        Args:
            node: Custom node (48-bit integer). Auto-detected if None.
            clock_seq: Custom clock sequence. Random if None.

        Returns:
            UUID v1 string.
        """
        self._ensure_active()
        self._increment_call_count()

        uid = uuid.uuid1(node=node, clock_seq=clock_seq)
        return self._format_uuid(uid)

    def v3(self, namespace: str = "dns", name: str = "") -> str:
        """Generate a UUID v3 (name-based, MD5 hash).

        Args:
            namespace: Namespace UUID or name (dns, url, oid, x500).
            name: Name string to hash.

        Returns:
            UUID v3 string.
        """
        self._ensure_active()
        self._increment_call_count()

        ns_uuid = self._resolve_namespace(namespace)
        uid = uuid.uuid3(ns_uuid, name)
        return self._format_uuid(uid)

    def v4(self) -> str:
        """Generate a UUID v4 (random).

        Uses cryptographically secure random numbers.

        Returns:
            UUID v4 string.
        """
        self._ensure_active()
        self._increment_call_count()

        uid = uuid.uuid4()
        return self._format_uuid(uid)

    def v5(self, namespace: str = "dns", name: str = "") -> str:
        """Generate a UUID v5 (name-based, SHA-1 hash).

        Args:
            namespace: Namespace UUID or name (dns, url, oid, x500).
            name: Name string to hash.

        Returns:
            UUID v5 string.
        """
        self._ensure_active()
        self._increment_call_count()

        ns_uuid = self._resolve_namespace(namespace)
        uid = uuid.uuid5(ns_uuid, name)
        return self._format_uuid(uid)

    def v7(self, *, timestamp: Optional[float] = None) -> str:
        """Generate a UUID v7 (time-ordered with Unix timestamp).

        UUID v7 is a time-ordered UUID with 48 bits of Unix timestamp
        in milliseconds, plus 74 bits of randomness.

        Format: TTTTTTTT-TTTT-7VVV-VVVV-RRRRRRRRRRRR
        where T = timestamp, V = random version, R = random

        Args:
            timestamp: Unix timestamp in seconds. Current time if None.

        Returns:
            UUID v7 string.
        """
        self._ensure_active()
        self._increment_call_count()

        # Get timestamp in milliseconds
        if timestamp is None:
            ts_ms = int(time.time() * 1000)
        else:
            ts_ms = int(timestamp * 1000)

        # UUID v7 format:
        # 48 bits: timestamp (ms)
        # 4 bits: version (0111 = 7)
        # 12 bits: random_a
        # 2 bits: variant (10)
        # 62 bits: random_b

        # Generate 74 bits of randomness (12 + 62)
        random_bytes = secrets.token_bytes(10)  # 80 bits, we'll use 74
        random_int = int.from_bytes(random_bytes, "big")

        # random_a: 12 bits
        random_a = (random_int >> 62) & 0xFFF

        # random_b: 62 bits (first 2 bits of variant set to 10)
        random_b = random_int & 0x3FFFFFFFFFFFFFFF  # 62 bits
        # Set variant bits (bits 6 and 7 from the top of the clock_seq_hi_and_reserved)
        random_b |= 0x8000000000000000  # Set the 63rd bit (variant = 10xx)

        # Build UUID components
        time_low = (ts_ms >> 16) & 0xFFFFFFFF
        time_mid = (ts_ms >> 4) & 0xFFFF
        time_hi_and_version = ((ts_ms & 0xF) << 12) | (7 << 12)  # version 7
        # Actually, version goes in the high nibble of time_hi_and_version
        time_hi_and_version = ((ts_ms & 0x0FFF) << 4) | 0x7000  # 4 bits of ts + version nibble

        # Wait, let me recalculate. UUID v7 has:
        # - time_low (32 bits): top 32 bits of 48-bit timestamp
        # - time_mid (16 bits): next 16 bits of timestamp
        # - time_hi_and_version (16 bits): low 16 bits, with version in high nibble
        # Actually: 48 bits total timestamp
        # time_low = bits 47-16 (32 bits)
        # time_mid = bits 15-0? No...
        # Standard UUID layout:
        # time_low: 32 bits
        # time_mid: 16 bits
        # time_hi_and_version: 16 bits (high 4 bits = version)
        # So: timestamp 48 bits -> time_low (32) + time_mid (12) + time_hi_low (4)
        # time_hi_and_version: 4 bits version + 12 bits remaining timestamp

        ts_48 = ts_ms & 0xFFFFFFFFFFFF  # 48 bits

        time_low = (ts_48 >> 16) & 0xFFFFFFFF  # bits 47-16: 32 bits
        time_mid = ts_48 & 0xFFFF  # bits 15-0: 16 bits... wait that's only 48 total = 32 + 16
        # But we need version in time_hi. Let me re-examine.

        # Actually, let's just build the 128-bit value directly
        # 48 bits timestamp | 4 bits version | 12 bits rand_a | 2 bits variant | 62 bits rand_b
        high_64 = (ts_ms << 16) | (7 << 12) | random_a

        # Low 64 bits: variant (2 bits) + random_b (62 bits)
        low_64 = (2 << 62) | random_b  # variant = 10 (RFC 4122)

        # Format as UUID string
        uid_str = (
            f"{high_64 >> 32:08x}-"
            f"{(high_64 >> 16) & 0xFFFF:04x}-"
            f"{high_64 & 0xFFFF:04x}-"
            f"{(low_64 >> 48) & 0xFFFF:04x}-"
            f"{low_64 & 0xFFFFFFFFFFFF:012x}"
        )

        return self._format_uuid_str(uid_str)

    def _resolve_namespace(self, namespace: str) -> uuid.UUID:
        """Resolve a namespace name or UUID string to UUID object."""
        if namespace.lower() in self.NAMESPACES:
            return self.NAMESPACES[namespace.lower()]

        # Try parsing as UUID string
        try:
            return uuid.UUID(namespace)
        except ValueError:
            raise ValueError(
                f"Unknown namespace: {namespace}. "
                f"Use one of: {list(self.NAMESPACES.keys())} or a valid UUID string."
            )

    def _format_uuid(self, uid: uuid.UUID) -> str:
        """Format a UUID object to string with configured case."""
        s = str(uid)
        if self._default_case == "upper":
            return s.upper()
        return s

    def _format_uuid_str(self, uid_str: str) -> str:
        """Format a UUID string with configured case."""
        if self._default_case == "upper":
            return uid_str.upper()
        return uid_str

    # ==================== Batch Generation ====================

    def batch(
        self,
        count: int,
        version: Optional[int] = None,
        **kwargs: Any,
    ) -> BatchUuidResult:
        """Generate multiple UUIDs at once.

        Args:
            count: Number of UUIDs to generate.
            version: UUID version.
            **kwargs: Additional arguments for the version.

        Returns:
            BatchUuidResult with UUIDs and stats.
        """
        self._ensure_active()
        self._increment_call_count()

        if count < 1:
            raise ValueError("Count must be at least 1")
        if count > 100000:
            raise ValueError("Maximum batch size is 100000")

        version = version or self._default_version
        uuids: List[str] = []
        seen: set = set()
        duplicates = 0

        for _ in range(count):
            uid = self.generate(version, **kwargs)
            if uid in seen:
                duplicates += 1
            seen.add(uid)
            uuids.append(uid)

        return BatchUuidResult(
            uuids=uuids,
            count=len(uuids),
            version=version,
            duplicates=duplicates,
        )

    # ==================== Validation ====================

    def validate(self, uid_str: str) -> bool:
        """Validate a UUID string format.

        Args:
            uid_str: UUID string to validate.

        Returns:
            True if valid UUID format.
        """
        self._ensure_active()
        self._increment_call_count()

        if not isinstance(uid_str, str):
            return False

        # Check basic format
        if not self.UUID_PATTERN.match(uid_str.strip()):
            return False

        # Try parsing with uuid module
        try:
            uuid.UUID(uid_str.strip())
            return True
        except ValueError:
            return False

    def get_version(self, uid_str: str) -> Optional[int]:
        """Get the version of a UUID string.

        Args:
            uid_str: UUID string.

        Returns:
            Version number (1-5, 7) or None if invalid.
        """
        self._ensure_active()
        self._increment_call_count()

        try:
            uid = uuid.UUID(uid_str.strip())
            return uid.version
        except ValueError:
            return None

    def get_variant(self, uid_str: str) -> Optional[str]:
        """Get the variant of a UUID string.

        Args:
            uid_str: UUID string.

        Returns:
            Variant name or None if invalid.
        """
        self._ensure_active()
        self._increment_call_count()

        try:
            uid = uuid.UUID(uid_str.strip())
            variant_map = {
                uuid.RESERVED_NCS: "reserved_ncs",
                uuid.RFC_4122: "rfc_4122",
                uuid.RESERVED_MICROSOFT: "reserved_microsoft",
                uuid.RESERVED_FUTURE: "reserved_future",
            }
            return variant_map.get(uid.variant, "unknown")
        except ValueError:
            return None

    # ==================== Parsing ====================

    def parse(self, uid_str: str) -> UuidInfo:
        """Parse a UUID string and extract detailed information.

        Args:
            uid_str: UUID string to parse.

        Returns:
            UuidInfo with detailed UUID information.
        """
        self._ensure_active()
        self._increment_call_count()

        try:
            uid = uuid.UUID(uid_str.strip())
        except ValueError:
            return UuidInfo(
                uuid_str=uid_str,
                version=0,
                variant="invalid",
                is_valid=False,
            )

        variant_map = {
            uuid.RESERVED_NCS: "reserved_ncs",
            uuid.RFC_4122: "rfc_4122",
            uuid.RESERVED_MICROSOFT: "reserved_microsoft",
            uuid.RESERVED_FUTURE: "reserved_future",
        }

        info = UuidInfo(
            uuid_str=str(uid),
            version=uid.version or 0,
            variant=variant_map.get(uid.variant, "unknown"),
            is_valid=True,
            components={
                "time_low": f"{uid.time_low:08x}",
                "time_mid": f"{uid.time_mid:04x}",
                "time_hi_version": f"{uid.time_hi_version:04x}",
                "clock_seq_hi_variant": f"{uid.clock_seq_hi_variant:02x}",
                "clock_seq_low": f"{uid.clock_seq_low:02x}",
                "node": f"{uid.node:012x}",
            },
        )

        # Version-specific info
        if uid.version == 1:
            info.timestamp = uid.time
            try:
                import datetime
                # UUID epoch is 1582-10-15, convert to Unix
                unix_ts = (uid.time - 0x01B21DD213814000) / 10_000_000
                info.timestamp_iso = datetime.datetime.fromtimestamp(unix_ts).isoformat()
            except Exception:
                pass
            info.clock_sequence = uid.clock_seq
            info.node = f"{uid.node:012x}"

        return info

    # ==================== Namespace ====================

    def namespace(self, name: str) -> str:
        """Get a standard namespace UUID by name.

        Args:
            name: Namespace name (dns, url, oid, x500).

        Returns:
            Namespace UUID string.
        """
        self._ensure_active()
        self._increment_call_count()

        ns_uuid = self._resolve_namespace(name)
        return self._format_uuid(ns_uuid)

    def get_namespaces(self) -> Dict[str, str]:
        """Get all standard namespace UUIDs.

        Returns:
            Dict mapping namespace name to UUID string.
        """
        return {name: str(uid) for name, uid in self.NAMESPACES.items()}

    # ==================== Nil UUID ====================

    def nil(self) -> str:
        """Get the nil UUID (all zeros).

        Returns:
            "00000000-0000-0000-0000-000000000000"
        """
        self._ensure_active()
        self._increment_call_count()

        return self._format_uuid_str("00000000-0000-0000-0000-000000000000")

    # ==================== Conversion ====================

    def to_string(self, uid: uuid.UUID) -> str:
        """Convert a UUID object to string.

        Args:
            uid: UUID object.

        Returns:
            UUID string.
        """
        self._ensure_active()
        self._increment_call_count()
        return self._format_uuid(uid)

    def from_string(self, uid_str: str) -> uuid.UUID:
        """Parse a UUID string to UUID object.

        Args:
            uid_str: UUID string.

        Returns:
            UUID object.
        """
        self._ensure_active()
        self._increment_call_count()
        return uuid.UUID(uid_str.strip())

    def to_hex(self, uid_str: str) -> str:
        """Convert UUID string to hex (without dashes).

        Args:
            uid_str: UUID string.

        Returns:
            Hex string (32 characters).
        """
        self._ensure_active()
        self._increment_call_count()
        uid = uuid.UUID(uid_str.strip())
        return uid.hex

    def from_hex(self, hex_str: str) -> str:
        """Convert hex string to UUID string.

        Args:
            hex_str: 32-character hex string.

        Returns:
            UUID string.
        """
        self._ensure_active()
        self._increment_call_count()
        uid = uuid.UUID(hex=hex_str.strip())
        return self._format_uuid(uid)

    def to_int(self, uid_str: str) -> int:
        """Convert UUID string to integer.

        Args:
            uid_str: UUID string.

        Returns:
            128-bit integer.
        """
        self._ensure_active()
        self._increment_call_count()
        uid = uuid.UUID(uid_str.strip())
        return uid.int

    def from_int(self, value: int) -> str:
        """Convert integer to UUID string.

        Args:
            value: 128-bit integer.

        Returns:
            UUID string.
        """
        self._ensure_active()
        self._increment_call_count()
        uid = uuid.UUID(int=value)
        return self._format_uuid(uid)

    # ==================== Comparison ====================

    def compare(self, uid1: str, uid2: str) -> Dict[str, Any]:
        """Compare two UUIDs.

        Args:
            uid1: First UUID string.
            uid2: Second UUID string.

        Returns:
            Dict with comparison info.
        """
        self._ensure_active()
        self._increment_call_count()

        try:
            u1 = uuid.UUID(uid1.strip())
            u2 = uuid.UUID(uid2.strip())
        except ValueError as e:
            raise ValueError(f"Invalid UUID: {e}")

        return {
            "equal": u1 == u2,
            "uid1_less": u1 < u2,
            "uid1_greater": u1 > u2,
            "version_same": u1.version == u2.version,
            "variant_same": u1.variant == u2.variant,
            "version1": u1.version,
            "version2": u2.version,
        }

    # ==================== Short UUID ====================

    def short(self, uid_str: str) -> str:
        """Generate a shorter representation of UUID (base62-like).

        Uses base57 encoding for URL-safe short UUIDs.

        Args:
            uid_str: UUID string.

        Returns:
            Short UUID string (~22 chars).
        """
        self._ensure_active()
        self._increment_call_count()

        alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
        base = len(alphabet)

        uid = uuid.UUID(uid_str.strip())
        num = uid.int

        if num == 0:
            return alphabet[0] * 22

        chars: List[str] = []
        while num > 0:
            num, rem = divmod(num, base)
            chars.append(alphabet[rem])

        result = "".join(reversed(chars))
        # Pad to 22 characters
        return result.rjust(22, alphabet[0])

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            "default_version": self._default_version,
            "default_case": self._default_case,
            "supported_versions": [1, 3, 4, 5, 7],
            "standard_namespaces": list(self.NAMESPACES.keys()),
        }
