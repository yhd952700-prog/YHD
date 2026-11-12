"""
IP Address Tools Plugin for LiuHao AI OS

Provides IP address validation (IPv4, IPv6), CIDR parsing,
subnet calculation, IP range checking, IP to integer conversion,
and network/broadcast address calculation.

Capabilities:
- validate_ip: Validate IP address (v4/v6)
- parse_cidr: Parse CIDR notation
- subnet_info: Get subnet information
- ip_in_range: Check if IP is in a range
- ip_to_int: Convert IP to integer
- int_to_ip: Convert integer to IP
- network_broadcast: Get network and broadcast addresses
"""

from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
import struct
import socket

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class SubnetInfo:
    """Information about an IP subnet."""
    network: str
    broadcast: str
    netmask: str
    wildcard: str
    prefix_length: int
    num_addresses: int
    usable_hosts: int
    first_host: str
    last_host: str
    ip_class: str
    is_private: bool


class IPAddressPlugin(BasePlugin):
    """IP address tools plugin.

    Provides IPv4 and IPv6 validation, CIDR parsing,
    subnet calculation, range checking, and integer conversion.

    Example:
        plugin = IPAddressPlugin()
        plugin.initialize()
        plugin.activate()

        info = plugin.subnet_info("192.168.1.0/24")
        print(info.network)  # "192.168.1.0"
        print(info.broadcast)  # "192.168.1.255"
    """

    # Private IP ranges (IPv4)
    PRIVATE_RANGES_IPV4 = [
        ("10.0.0.0", "10.255.255.255"),      # Class A private
        ("172.16.0.0", "172.31.255.255"),    # Class B private
        ("192.168.0.0", "192.168.255.255"),  # Class C private
        ("127.0.0.0", "127.255.255.255"),    # Loopback
        ("169.254.0.0", "169.254.255.255"),  # Link-local
    ]

    # Special-use IPv4 ranges
    SPECIAL_RANGES_IPV4 = [
        ("0.0.0.0", "0.255.255.255", "Current network"),
        ("100.64.0.0", "100.127.255.255", "Carrier-grade NAT"),
        ("192.0.0.0", "192.0.0.255", "IETF Protocol Assignments"),
        ("192.0.2.0", "192.0.2.255", "TEST-NET-1 (documentation)"),
        ("198.51.100.0", "198.51.100.255", "TEST-NET-2 (documentation)"),
        ("203.0.113.0", "203.0.113.255", "TEST-NET-3 (documentation)"),
        ("224.0.0.0", "239.255.255.255", "Multicast"),
        ("240.0.0.0", "255.255.255.254", "Reserved"),
        ("255.255.255.255", "255.255.255.255", "Limited broadcast"),
    ]

    def __init__(self) -> None:
        """Initialize the IP address plugin."""
        metadata = PluginMetadata(
            name="ip-address",
            version="1.0.0",
            description="IP address tools: validation, CIDR, subnets, range checking, conversion",
            author="LiuHao AI OS Team",
            categories=["network", "utility", "validation"],
            keywords=["ip", "network", "cidr", "subnet", "ipv4", "ipv6", "address"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the IP address plugin."""
        try:
            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the IP address plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the IP address plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the IP address plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "validate_ip",
            "parse_cidr",
            "subnet_info",
            "ip_in_range",
            "ip_to_int",
            "int_to_ip",
            "network_broadcast",
            "is_private",
            "detect_ip_type",
        ]

    # ==================== IP Validation ====================

    def validate_ip(self, ip: str, version: Optional[int] = None) -> Dict[str, Any]:
        """Validate an IP address.

        Args:
            ip: IP address string.
            version: IP version (4, 6, or None for auto-detect).

        Returns:
            Dict with validation result and details.
        """
        self._ensure_active()
        self._increment_call_count()

        result = {
            "ip": ip,
            "is_valid": False,
            "version": None,
            "error": None,
        }

        # Try IPv4
        if version in (None, 4):
            is_v4, v4_info = self._validate_ipv4(ip)
            if is_v4:
                result.update(v4_info)
                result["is_valid"] = True
                result["version"] = 4
                return result

        # Try IPv6
        if version in (None, 6):
            is_v6, v6_info = self._validate_ipv6(ip)
            if is_v6:
                result.update(v6_info)
                result["is_valid"] = True
                result["version"] = 6
                return result

        if version == 4:
            result["error"] = "Invalid IPv4 address"
        elif version == 6:
            result["error"] = "Invalid IPv6 address"
        else:
            result["error"] = "Invalid IP address"

        return result

    def _validate_ipv4(self, ip: str) -> Tuple[bool, Dict[str, Any]]:
        """Validate IPv4 address."""
        parts = ip.split('.')
        if len(parts) != 4:
            return False, {}

        octets = []
        for part in parts:
            if not part.isdigit():
                return False, {}
            val = int(part)
            if val < 0 or val > 255:
                return False, {}
            # Check for leading zeros (except "0")
            if len(part) > 1 and part.startswith('0'):
                return False, {}
            octets.append(val)

        # Determine IP class
        first_octet = octets[0]
        if first_octet < 128:
            ip_class = "A"
        elif first_octet < 192:
            ip_class = "B"
        elif first_octet < 224:
            ip_class = "C"
        elif first_octet < 240:
            ip_class = "D"
        else:
            ip_class = "E"

        info = {
            "octets": octets,
            "ip_class": ip_class,
            "is_private": self._is_private_ipv4(ip),
            "is_loopback": octets[0] == 127,
            "is_link_local": octets[0] == 169 and octets[1] == 254,
            "is_multicast": first_octet >= 224 and first_octet <= 239,
            "integer_value": self._ipv4_to_int(ip),
        }

        return True, info

    def _validate_ipv6(self, ip: str) -> Tuple[bool, Dict[str, Any]]:
        """Validate IPv6 address."""
        try:
            socket.inet_pton(socket.AF_INET6, ip)
        except (socket.error, OSError):
            return False, {}

        # Normalize/compress the address
        packed = socket.inet_pton(socket.AF_INET6, ip)
        normalized = socket.inet_ntop(socket.AF_INET6, packed)

        # Check for special types
        is_loopback = (ip == '::1')
        is_unspecified = (ip == '::')
        is_link_local = packed[0] == 0xfe and (packed[1] & 0xc0) == 0x80
        is_unique_local = packed[0] == 0xfc or packed[0] == 0xfd

        info = {
            "normalized": normalized,
            "is_loopback": is_loopback,
            is_unspecified: is_unspecified,
            "is_link_local": is_link_local,
            "is_unique_local": is_unique_local,
            "is_private": is_loopback or is_link_local or is_unique_local,
        }

        return True, info

    # ==================== CIDR Parsing ====================

    def parse_cidr(self, cidr: str) -> Dict[str, Any]:
        """Parse CIDR notation.

        Args:
            cidr: CIDR string (e.g., "192.168.1.0/24").

        Returns:
            Dict with parsed CIDR information.
        """
        self._ensure_active()
        self._increment_call_count()

        if '/' not in cidr:
            return {"error": "Invalid CIDR: missing '/'"}

        ip_part, prefix_part = cidr.split('/', 1)

        # Validate prefix
        try:
            prefix = int(prefix_part)
        except ValueError:
            return {"error": "Invalid prefix length"}

        # Validate IP
        ip_info = self.validate_ip(ip_part)
        if not ip_info["is_valid"]:
            return {"error": f"Invalid IP address: {ip_part}"}

        version = ip_info["version"]

        if version == 4:
            if prefix < 0 or prefix > 32:
                return {"error": "IPv4 prefix must be 0-32"}
        else:
            if prefix < 0 or prefix > 128:
                return {"error": "IPv6 prefix must be 0-128"}

        return {
            "cidr": cidr,
            "ip": ip_part,
            "prefix_length": prefix,
            "version": version,
            "is_valid": True,
        }

    # ==================== Subnet Info ====================

    def subnet_info(self, cidr: str) -> Optional[SubnetInfo]:
        """Get detailed subnet information.

        Args:
            cidr: CIDR notation string.

        Returns:
            SubnetInfo object, or None if invalid.
        """
        self._ensure_active()
        self._increment_call_count()

        parsed = self.parse_cidr(cidr)
        if "error" in parsed:
            return None

        ip = parsed["ip"]
        prefix = parsed["prefix_length"]

        if parsed["version"] == 4:
            return self._subnet_info_ipv4(ip, prefix)
        else:
            return self._subnet_info_ipv6(ip, prefix)

    def _subnet_info_ipv4(self, ip: str, prefix: int) -> SubnetInfo:
        """Calculate IPv4 subnet information."""
        ip_int = self._ipv4_to_int(ip)

        # Netmask
        if prefix == 0:
            mask_int = 0
        else:
            mask_int = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF

        # Wildcard (inverse mask)
        wildcard_int = mask_int ^ 0xFFFFFFFF

        # Network address
        network_int = ip_int & mask_int

        # Broadcast address
        broadcast_int = network_int | wildcard_int

        # Number of addresses
        num_addresses = 2 ** (32 - prefix)

        # Usable hosts
        if prefix == 32:
            usable = 1
            first_host = network_int
            last_host = network_int
        elif prefix == 31:
            usable = 2
            first_host = network_int
            last_host = broadcast_int
        else:
            usable = num_addresses - 2
            first_host = network_int + 1
            last_host = broadcast_int - 1

        # IP class
        first_octet = (network_int >> 24) & 0xFF
        if first_octet < 128:
            ip_class = "A"
        elif first_octet < 192:
            ip_class = "B"
        elif first_octet < 224:
            ip_class = "C"
        else:
            ip_class = "Other"

        network_str = self._int_to_ipv4(network_int)

        return SubnetInfo(
            network=network_str,
            broadcast=self._int_to_ipv4(broadcast_int),
            netmask=self._int_to_ipv4(mask_int),
            wildcard=self._int_to_ipv4(wildcard_int),
            prefix_length=prefix,
            num_addresses=num_addresses,
            usable_hosts=usable,
            first_host=self._int_to_ipv4(first_host),
            last_host=self._int_to_ipv4(last_host),
            ip_class=ip_class,
            is_private=self._is_private_ipv4(network_str),
        )

    def _subnet_info_ipv6(self, ip: str, prefix: int) -> SubnetInfo:
        """Calculate IPv6 subnet information (simplified)."""
        num_addresses = 2 ** (128 - prefix)

        return SubnetInfo(
            network=ip,
            broadcast="N/A (IPv6 uses multicast)",
            netmask=f"/{prefix}",
            wildcard="N/A",
            prefix_length=prefix,
            num_addresses=num_addresses,
            usable_hosts=num_addresses,
            first_host=ip,
            last_host="N/A",
            ip_class="IPv6",
            is_private=False,
        )

    # ==================== IP Range Checking ====================

    def ip_in_range(
        self,
        ip: str,
        network: str,
        prefix: Optional[int] = None,
    ) -> bool:
        """Check if an IP address is in a network range.

        Args:
            ip: IP address to check.
            network: Network address or CIDR.
            prefix: Prefix length (if not in CIDR format).

        Returns:
            True if IP is in the range.
        """
        self._ensure_active()
        self._increment_call_count()

        # Parse CIDR if needed
        if prefix is None and '/' in network:
            cidr = self.parse_cidr(network)
            if "error" in cidr:
                return False
            network = cidr["ip"]
            prefix = cidr["prefix_length"]

        if prefix is None:
            prefix = 32  # Default to single IP

        ip_info = self.validate_ip(ip)
        net_info = self.validate_ip(network)

        if not ip_info["is_valid"] or not net_info["is_valid"]:
            return False

        if ip_info["version"] != net_info["version"]:
            return False

        if ip_info["version"] == 4:
            ip_int = self._ipv4_to_int(ip)
            net_int = self._ipv4_to_int(network)
            mask = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF
            return (ip_int & mask) == (net_int & mask)
        else:
            # IPv6: compare first prefix bits
            ip_packed = socket.inet_pton(socket.AF_INET6, ip)
            net_packed = socket.inet_pton(socket.AF_INET6, network)

            full_bytes = prefix // 8
            remaining_bits = prefix % 8

            # Check full bytes
            for i in range(full_bytes):
                if ip_packed[i] != net_packed[i]:
                    return False

            # Check remaining bits
            if remaining_bits > 0:
                mask = (0xFF << (8 - remaining_bits)) & 0xFF
                if (ip_packed[full_bytes] & mask) != (net_packed[full_bytes] & mask):
                    return False

            return True

    # ==================== IP <-> Integer Conversion ====================

    def ip_to_int(self, ip: str) -> Dict[str, Any]:
        """Convert IP address to integer.

        Args:
            ip: IP address string.

        Returns:
            Dict with integer value and hex representation.
        """
        self._ensure_active()
        self._increment_call_count()

        info = self.validate_ip(ip)
        if not info["is_valid"]:
            return {"error": info.get("error", "Invalid IP")}

        if info["version"] == 4:
            int_val = self._ipv4_to_int(ip)
            return {
                "ip": ip,
                "version": 4,
                "integer": int_val,
                "hex": f"0x{int_val:08X}",
                "binary": f"{int_val:032b}",
            }
        else:
            packed = socket.inet_pton(socket.AF_INET6, ip)
            int_val = int.from_bytes(packed, 'big')
            return {
                "ip": ip,
                "version": 6,
                "integer": int_val,
                "hex": f"0x{int_val:032X}",
            }

    def int_to_ip(self, value: int, version: int = 4) -> Dict[str, Any]:
        """Convert integer to IP address.

        Args:
            value: Integer value.
            version: IP version (4 or 6).

        Returns:
            Dict with IP address string.
        """
        self._ensure_active()
        self._increment_call_count()

        if version == 4:
            if value < 0 or value > 0xFFFFFFFF:
                return {"error": "Value out of range for IPv4"}
            ip = self._int_to_ipv4(value)
            return {
                "integer": value,
                "version": 4,
                "ip": ip,
                "hex": f"0x{value:08X}",
            }
        else:
            if value < 0 or value > (2 ** 128 - 1):
                return {"error": "Value out of range for IPv6"}
            packed = value.to_bytes(16, 'big')
            ip = socket.inet_ntop(socket.AF_INET6, packed)
            return {
                "integer": value,
                "version": 6,
                "ip": ip,
                "hex": f"0x{value:032X}",
            }

    def _ipv4_to_int(self, ip: str) -> int:
        """Convert IPv4 string to integer."""
        octets = [int(o) for o in ip.split('.')]
        return (octets[0] << 24) | (octets[1] << 16) | (octets[2] << 8) | octets[3]

    def _int_to_ipv4(self, value: int) -> str:
        """Convert integer to IPv4 string."""
        return '.'.join(str((value >> (24 - i * 8)) & 0xFF) for i in range(4))

    # ==================== Network / Broadcast ====================

    def network_broadcast(self, cidr: str) -> Dict[str, Any]:
        """Get network and broadcast addresses.

        Args:
            cidr: CIDR notation.

        Returns:
            Dict with network, broadcast, and other info.
        """
        self._ensure_active()
        self._increment_call_count()

        info = self.subnet_info(cidr)
        if info is None:
            return {"error": "Invalid CIDR"}

        return {
            "network": info.network,
            "broadcast": info.broadcast,
            "netmask": info.netmask,
            "wildcard": info.wildcard,
            "prefix_length": info.prefix_length,
            "first_host": info.first_host,
            "last_host": info.last_host,
            "total_addresses": info.num_addresses,
            "usable_hosts": info.usable_hosts,
        }

    # ==================== Private IP Detection ====================

    def is_private(self, ip: str) -> Dict[str, Any]:
        """Check if an IP address is private.

        Args:
            ip: IP address to check.

        Returns:
            Dict with private status and type.
        """
        self._ensure_active()
        self._increment_call_count()

        info = self.validate_ip(ip)
        if not info["is_valid"]:
            return {"error": info.get("error", "Invalid IP")}

        if info["version"] == 4:
            return {
                "ip": ip,
                "version": 4,
                "is_private": self._is_private_ipv4(ip),
                "is_loopback": info.get("is_loopback", False),
                "is_link_local": info.get("is_link_local", False),
                "is_multicast": info.get("is_multicast", False),
                "ip_class": info.get("ip_class", ""),
            }
        else:
            return {
                "ip": ip,
                "version": 6,
                "is_private": info.get("is_private", False),
                "is_loopback": info.get("is_loopback", False),
                "is_link_local": info.get("is_link_local", False),
                "is_unique_local": info.get("is_unique_local", False),
            }

    def _is_private_ipv4(self, ip: str) -> bool:
        """Check if IPv4 address is in a private range."""
        ip_int = self._ipv4_to_int(ip)
        for start, end in self.PRIVATE_RANGES_IPV4:
            start_int = self._ipv4_to_int(start)
            end_int = self._ipv4_to_int(end)
            if start_int <= ip_int <= end_int:
                return True
        return False

    # ==================== IP Type Detection ====================

    def detect_ip_type(self, ip: str) -> Dict[str, Any]:
        """Detect the type/classification of an IP address.

        Args:
            ip: IP address.

        Returns:
            Dict with type classification.
        """
        self._ensure_active()
        self._increment_call_count()

        info = self.validate_ip(ip)
        if not info["is_valid"]:
            return {"error": info.get("error", "Invalid IP")}

        types = []

        if info["version"] == 4:
            if info.get("is_private"):
                types.append("private")
            if info.get("is_loopback"):
                types.append("loopback")
            if info.get("is_link_local"):
                types.append("link-local")
            if info.get("is_multicast"):
                types.append("multicast")
            if not types:
                types.append("public")

            return {
                "ip": ip,
                "version": 4,
                "types": types,
                "class": info.get("ip_class", ""),
                "integer": info.get("integer_value", 0),
            }
        else:
            if info.get("is_loopback"):
                types.append("loopback")
            if info.get("is_link_local"):
                types.append("link-local")
            if info.get("is_unique_local"):
                types.append("unique-local")
            if info.get("is_private"):
                types.append("private")
            if not types:
                types.append("public")

            return {
                "ip": ip,
                "version": 6,
                "types": types,
                "normalized": info.get("normalized", ip),
            }

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            "supports_ipv4": True,
            "supports_ipv6": True,
            "ipv4_max_prefix": 32,
            "ipv6_max_prefix": 128,
            "private_ranges_ipv4": len(self.PRIVATE_RANGES_IPV4),
            "special_ranges_ipv4": len(self.SPECIAL_RANGES_IPV4),
        }
