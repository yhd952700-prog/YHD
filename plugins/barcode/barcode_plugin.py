"""
Barcode Generation Plugin for LiuHao AI OS

Provides barcode generation for EAN-13, UPC-A, Code 128,
with validation, encoding, and output format options.

Capabilities:
- generate_ean13: Generate EAN-13 barcode
- generate_upca: Generate UPC-A barcode
- generate_code128: Generate Code 128 barcode
- validate: Validate barcode data
- get_barcode_specs: Get barcode specifications
- calculate_check_digit: Calculate check digits
"""

from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class BarcodeResult:
    """Result of barcode generation."""
    data: str
    barcode_type: str
    encoded_patterns: List[str]  # Bar/space patterns
    check_digit: Optional[int]
    width_modules: int  # Total width in modules
    is_valid: bool
    errors: List[str]


class BarcodePlugin(BasePlugin):
    """Barcode generation and validation plugin.

    Supports EAN-13, UPC-A, and Code 128 barcode types with
    check digit calculation, validation, and pattern encoding.
    Provides barcode data in module pattern format suitable for
    rendering.

    Example:
        plugin = BarcodePlugin()
        plugin.initialize()
        plugin.activate()

        result = plugin.generate_ean13("4006381333931")
        print(result.check_digit)  # 1
    """

    # EAN-13 digit encodings: L-code (left, odd parity), G-code (left, even parity), R-code (right)
    # Each digit is 7 modules. '1' = bar, '0' = space
    EAN_L = {
        0: "0001101", 1: "0011001", 2: "0010011", 3: "0111101",
        4: "0100011", 5: "0110001", 6: "0101111", 7: "0111011",
        8: "0110111", 9: "0001011",
    }
    EAN_G = {
        0: "0100111", 1: "0110011", 2: "0011011", 3: "0100001",
        4: "0011101", 5: "0111001", 6: "0000101", 7: "0010001",
        8: "0001001", 9: "0010111",
    }
    EAN_R = {
        0: "1110010", 1: "1100110", 2: "1101100", 3: "1000010",
        4: "1011100", 5: "1001110", 6: "1010000", 7: "1000100",
        8: "1001000", 9: "1110100",
    }

    # EAN-13 first digit determines parity pattern for left 6 digits
    # 0 = L-code, 1 = G-code
    EAN_PARITY_PATTERNS = {
        0: [0, 0, 0, 0, 0, 0],
        1: [0, 0, 1, 0, 1, 1],
        2: [0, 0, 1, 1, 0, 1],
        3: [0, 0, 1, 1, 1, 0],
        4: [0, 1, 0, 0, 1, 1],
        5: [0, 1, 1, 0, 0, 1],
        6: [0, 1, 1, 1, 0, 0],
        7: [0, 1, 0, 1, 0, 1],
        8: [0, 1, 0, 1, 1, 0],
        9: [0, 1, 1, 0, 1, 0],
    }

    # Code 128 subsets
    CODE128_A = {}  # Will be populated in init
    CODE128_B = {}
    CODE128_C = {}

    # Common barcode dimensions
    EAN13_WIDTH_MODULES = 95  # 3 left guard + 6*7 left + 5 center guard + 6*7 right + 3 right guard
    UPCA_WIDTH_MODULES = 95

    def __init__(self) -> None:
        """Initialize the barcode plugin."""
        metadata = PluginMetadata(
            name="barcode",
            version="1.0.0",
            description="Barcode generation for EAN-13, UPC-A, Code 128 with validation",
            author="LiuHao AI OS Team",
            categories=["encoding", "utility", "graphics"],
            keywords=["barcode", "ean", "upc", "code128", "qr", "generator", "scanner"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._module_width_px = 2
        self._bar_color = "000000"
        self._bg_color = "FFFFFF"

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the barcode plugin."""
        try:
            if config:
                self._module_width_px = config.get("module_width_px", 2)
                self._bar_color = config.get("bar_color", "000000")
                self._bg_color = config.get("bg_color", "FFFFFF")

            self._build_code128_tables()

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the barcode plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the barcode plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the barcode plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "generate_ean13",
            "generate_upca",
            "generate_code128",
            "validate",
            "calculate_check_digit",
            "get_barcode_specs",
            "ascii_pattern",
        ]

    # ==================== EAN-13 ====================

    def generate_ean13(self, data: str) -> BarcodeResult:
        """Generate an EAN-13 barcode.

        Args:
            data: 12 or 13 digit EAN-13 number. If 12 digits,
                  check digit will be calculated.

        Returns:
            BarcodeResult with encoded patterns.
        """
        self._ensure_active()
        self._increment_call_count()

        errors: List[str] = []
        digits = [int(c) for c in data if c.isdigit()]

        if len(digits) not in (12, 13):
            errors.append(f"EAN-13 requires 12 or 13 digits, got {len(digits)}")
            return BarcodeResult(
                data=data,
                barcode_type="EAN-13",
                encoded_patterns=[],
                check_digit=None,
                width_modules=0,
                is_valid=False,
                errors=errors,
            )

        # Calculate or verify check digit
        if len(digits) == 12:
            check_digit = self._calculate_ean_checksum(digits)
            digits.append(check_digit)
        else:
            expected = self._calculate_ean_checksum(digits[:12])
            check_digit = digits[12]
            if digits[12] != expected:
                errors.append(
                    f"Check digit mismatch: got {digits[12]}, expected {expected}"
                )

        # Encode the barcode
        patterns: List[str] = []

        # Left guard bar
        patterns.append("101")

        # Left 6 digits (positions 1-6, first digit determines parity)
        first_digit = digits[0]
        parity = self.EAN_PARITY_PATTERNS[first_digit]

        for i in range(6):
            digit = digits[i + 1]
            if parity[i] == 0:
                patterns.append(self.EAN_L[digit])
            else:
                patterns.append(self.EAN_G[digit])

        # Center guard
        patterns.append("01010")

        # Right 6 digits (positions 7-12 + check digit)
        for i in range(6):
            digit = digits[i + 7]
            patterns.append(self.EAN_R[digit])

        # Right guard bar
        patterns.append("101")

        return BarcodeResult(
            data=''.join(str(d) for d in digits),
            barcode_type="EAN-13",
            encoded_patterns=patterns,
            check_digit=check_digit,
            width_modules=self.EAN13_WIDTH_MODULES,
            is_valid=len(errors) == 0,
            errors=errors,
        )

    # ==================== UPC-A ====================

    def generate_upca(self, data: str) -> BarcodeResult:
        """Generate a UPC-A barcode.

        Args:
            data: 11 or 12 digit UPC-A number. If 11 digits,
                  check digit will be calculated.

        Returns:
            BarcodeResult with encoded patterns.
        """
        self._ensure_active()
        self._increment_call_count()

        errors: List[str] = []
        digits = [int(c) for c in data if c.isdigit()]

        if len(digits) not in (11, 12):
            errors.append(f"UPC-A requires 11 or 12 digits, got {len(digits)}")
            return BarcodeResult(
                data=data,
                barcode_type="UPC-A",
                encoded_patterns=[],
                check_digit=None,
                width_modules=0,
                is_valid=False,
                errors=errors,
            )

        # Calculate or verify check digit
        if len(digits) == 11:
            check_digit = self._calculate_upc_checksum(digits)
            digits.append(check_digit)
        else:
            expected = self._calculate_upc_checksum(digits[:11])
            check_digit = digits[11]
            if digits[11] != expected:
                errors.append(
                    f"Check digit mismatch: got {digits[11]}, expected {expected}"
                )

        # UPC-A uses L-code for left half, R-code for right half
        patterns: List[str] = []
        patterns.append("101")  # Left guard

        for i in range(6):
            patterns.append(self.EAN_L[digits[i]])

        patterns.append("01010")  # Center guard

        for i in range(6):
            patterns.append(self.EAN_R[digits[i + 6]])

        patterns.append("101")  # Right guard

        return BarcodeResult(
            data=''.join(str(d) for d in digits),
            barcode_type="UPC-A",
            encoded_patterns=patterns,
            check_digit=check_digit,
            width_modules=self.UPCA_WIDTH_MODULES,
            is_valid=len(errors) == 0,
            errors=errors,
        )

    # ==================== Code 128 ====================

    def generate_code128(self, data: str, subset: str = "B") -> BarcodeResult:
        """Generate a Code 128 barcode.

        Args:
            data: The data to encode.
            subset: Code 128 subset ('A', 'B', or 'C').

        Returns:
            BarcodeResult with encoded patterns.
        """
        self._ensure_active()
        self._increment_call_count()

        errors: List[str] = []
        subset = subset.upper()

        if subset not in ('A', 'B', 'C'):
            errors.append(f"Invalid Code 128 subset: {subset}")
            return BarcodeResult(
                data=data,
                barcode_type="Code 128",
                encoded_patterns=[],
                check_digit=None,
                width_modules=0,
                is_valid=False,
                errors=errors,
            )

        # Get patterns table for subset
        if subset == 'A':
            table = self.CODE128_A
            start_code = 103  # Start A
        elif subset == 'B':
            table = self.CODE128_B
            start_code = 104  # Start B
        else:
            table = self.CODE128_C
            start_code = 105  # Start C

        # Validate data
        values: List[int] = []
        if subset == 'C':
            # Code C: pairs of digits
            if len(data) % 2 != 0:
                errors.append("Code 128 subset C requires even number of digits")
            for ch in data:
                if not ch.isdigit():
                    errors.append(f"Code 128 subset C only supports digits, found '{ch}'")
            for i in range(0, len(data) - 1, 2):
                pair = data[i:i + 2]
                if pair.isdigit():
                    values.append(int(pair))
        else:
            for ch in data:
                if ch not in table:
                    errors.append(
                        f"Character '{ch}' not supported in Code 128 subset {subset}"
                    )
                    values.append(0)
                else:
                    values.append(table[ch])

        if errors:
            return BarcodeResult(
                data=data,
                barcode_type=f"Code 128 {subset}",
                encoded_patterns=[],
                check_digit=None,
                width_modules=0,
                is_valid=False,
                errors=errors,
            )

        # Calculate check digit
        # Checksum = (start + sum(position * value)) mod 103
        checksum = start_code
        for i, val in enumerate(values):
            checksum += (i + 1) * val
        checksum %= 103

        # Build patterns
        patterns: List[str] = []
        patterns.append(self._code128_pattern(start_code))

        for val in values:
            patterns.append(self._code128_pattern(val))

        patterns.append(self._code128_pattern(checksum))
        patterns.append("1100011101011")  # Stop pattern

        total_width = sum(len(p) for p in patterns)

        return BarcodeResult(
            data=data,
            barcode_type=f"Code 128 {subset}",
            encoded_patterns=patterns,
            check_digit=checksum,
            width_modules=total_width,
            is_valid=True,
            errors=[],
        )

    # ==================== Validation ====================

    def validate(self, data: str, barcode_type: str) -> Tuple[bool, List[str]]:
        """Validate barcode data for a specific type.

        Args:
            data: The data to validate.
            barcode_type: Type of barcode ('ean13', 'upca', 'code128').

        Returns:
            Tuple of (is_valid, list_of_errors).
        """
        self._ensure_active()
        self._increment_call_count()

        errors: List[str] = []
        barcode_type = barcode_type.lower()

        if barcode_type in ('ean13', 'ean-13'):
            digits = [c for c in data if c.isdigit()]
            if len(digits) not in (12, 13):
                errors.append(f"EAN-13 requires 12 or 13 digits, got {len(digits)}")
            elif len(digits) == 13:
                expected = self._calculate_ean_checksum([int(d) for d in digits[:12]])
                if int(digits[12]) != expected:
                    errors.append(f"Invalid check digit (expected {expected})")

        elif barcode_type in ('upca', 'upc-a'):
            digits = [c for c in data if c.isdigit()]
            if len(digits) not in (11, 12):
                errors.append(f"UPC-A requires 11 or 12 digits, got {len(digits)}")
            elif len(digits) == 12:
                expected = self._calculate_upc_checksum([int(d) for d in digits[:11]])
                if int(digits[11]) != expected:
                    errors.append(f"Invalid check digit (expected {expected})")

        elif barcode_type in ('code128', 'code-128'):
            # Code 128 B supports most printable ASCII
            for ch in data:
                if ord(ch) < 32 or ord(ch) > 126:
                    errors.append(f"Character '{ch}' not supported in Code 128")
            if not data:
                errors.append("Data cannot be empty")

        else:
            errors.append(f"Unknown barcode type: {barcode_type}")

        return len(errors) == 0, errors

    # ==================== Check Digit ====================

    def calculate_check_digit(self, data: str, barcode_type: str) -> Optional[int]:
        """Calculate check digit for a barcode.

        Args:
            data: Barcode data without check digit.
            barcode_type: Type of barcode.

        Returns:
            Check digit value, or None if not applicable.
        """
        self._ensure_active()
        self._increment_call_count()

        digits = [int(c) for c in data if c.isdigit()]
        barcode_type = barcode_type.lower()

        if barcode_type in ('ean13', 'ean-13', 'ean8', 'ean-8'):
            return self._calculate_ean_checksum(digits)
        elif barcode_type in ('upca', 'upc-a'):
            return self._calculate_upc_checksum(digits)

        return None

    # ==================== ASCII Pattern ====================

    def ascii_pattern(self, result: BarcodeResult, height: int = 5) -> str:
        """Generate an ASCII art representation of a barcode.

        Args:
            result: BarcodeResult from generation.
            height: Height of barcode in characters.

        Returns:
            ASCII art string.
        """
        self._ensure_active()
        self._increment_call_count()

        if not result.encoded_patterns:
            return "(empty barcode)"

        full_pattern = ''.join(result.encoded_patterns)

        lines: List[str] = []
        bar_char = '#'
        space_char = ' '

        for _ in range(height):
            line = ''.join(
                bar_char if bit == '1' else space_char
                for bit in full_pattern
            )
            lines.append(line)

        # Add human-readable text below
        lines.append("")
        lines.append(result.data.center(len(full_pattern)))

        return '\n'.join(lines)

    # ==================== Specs ====================

    def get_barcode_specs(self, barcode_type: str) -> Dict[str, Any]:
        """Get specifications for a barcode type.

        Args:
            barcode_type: Type of barcode.

        Returns:
            Dict with barcode specifications.
        """
        self._ensure_active()
        self._increment_call_count()

        barcode_type = barcode_type.lower()

        specs = {
            "ean13": {
                "name": "EAN-13",
                "total_digits": 13,
                "data_digits": 12,
                "check_digit": True,
                "width_modules": 95,
                "left_guard": "101",
                "center_guard": "01010",
                "right_guard": "101",
                "modules_per_digit": 7,
                "supported_chars": "digits 0-9",
            },
            "upca": {
                "name": "UPC-A",
                "total_digits": 12,
                "data_digits": 11,
                "check_digit": True,
                "width_modules": 95,
                "left_guard": "101",
                "center_guard": "01010",
                "right_guard": "101",
                "modules_per_digit": 7,
                "supported_chars": "digits 0-9",
            },
            "code128": {
                "name": "Code 128",
                "subsets": ["A", "B", "C"],
                "check_digit": True,
                "modulo": 103,
                "start_codes": {"A": 103, "B": 104, "C": 105},
                "stop_pattern": "1100011101011",
                "modules_per_char": 11,
                "subset_a_chars": "ASCII 0-95 (control + uppercase + symbols)",
                "subset_b_chars": "ASCII 32-127 (printable)",
                "subset_c_chars": "digit pairs (00-99)",
            },
        }

        return specs.get(barcode_type, {"error": f"Unknown type: {barcode_type}"})

    # ==================== Internal Helpers ====================

    def _calculate_ean_checksum(self, digits: List[int]) -> int:
        """Calculate EAN-13 check digit."""
        # Odd positions (from right) * 3, even * 1
        # For EAN-13, we weight alternating digits starting with 1 from right
        total = 0
        for i, d in enumerate(reversed(digits)):
            if i % 2 == 0:
                total += d * 3
            else:
                total += d
        return (10 - (total % 10)) % 10

    def _calculate_upc_checksum(self, digits: List[int]) -> int:
        """Calculate UPC-A check digit."""
        # Odd positions * 3, even positions * 1 (1-indexed from left)
        total = 0
        for i, d in enumerate(digits):
            if i % 2 == 0:
                total += d * 3
            else:
                total += d
        return (10 - (total % 10)) % 10

    def _build_code128_tables(self) -> None:
        """Build Code 128 character tables."""
        # Subset B: printable ASCII (32-127)
        for i in range(95):
            char = chr(32 + i)
            self.CODE128_B[char] = i

        # Subset A: uppercase, digits, control chars, symbols
        for i in range(64):
            char = chr(i)  # 0-63 (NUL to ?)
            self.CODE128_A[char] = i
        for i in range(32):
            char = chr(64 + i)  # @ to _
            self.CODE128_A[char] = 64 + i

        # Subset C: digit pairs 00-99
        for i in range(100):
            pair = f"{i:02d}"
            self.CODE128_C[pair] = i

    def _code128_pattern(self, value: int) -> str:
        """Get the bar/space pattern for a Code 128 value.

        Code 128 uses 6 bars and 6 spaces per character (11 modules total).
        Patterns are defined by standard Code 128 specification.
        """
        # Code 128 patterns (simplified standard patterns)
        # Each is 11 modules: b s b s b s b s b s b (6 bars, 5 spaces)
        patterns = [
            "11011001100", "11001101100", "11001100110", "10010011000",  # 0-3
            "10010001100", "10001001100", "10011001000", "10011000100",  # 4-7
            "10001100100", "11001001000", "11001000100", "11000100100",  # 8-11
            "10110011100", "10011011100", "10011001110", "10111001100",  # 12-15
            "10011101100", "10011100110", "11001110010", "11001011100",  # 16-19
            "11001001110", "11011100100", "11001110100", "11101101110",  # 20-23
            "11101001100", "11100101100", "11100100110", "11101100100",  # 24-27
            "11100110100", "11100110010", "11011011000", "11011000110",  # 28-31
            "11000110110", "10100011000", "10001011000", "10001000110",  # 32-35
            "10110001000", "10001101000", "10001100010", "11010001000",  # 36-39
            "11000101000", "11000100010", "10110111000", "10110001110",  # 40-43
            "10001101110", "10111011000", "10111000110", "10001110110",  # 44-47
            "11101110110", "11010001110", "11000101110", "11011101000",  # 48-51
            "11011100010", "11011101110", "11101011000", "11101000110",  # 52-55
            "11100010110", "11101101000", "11101100010", "11100011010",  # 56-59
            "11101111010", "11001000010", "11110001010", "10100110000",  # 60-63
            "10100001100", "10010110000", "10010000110", "10000101100",  # 64-67
            "10000100110", "10110010000", "10110000100", "10011010000",  # 68-71
            "10011000010", "10000110100", "10000110010", "11000010010",  # 72-75
            "11001010000", "11110111010", "11000010100", "10001111010",  # 76-79
            "10100111100", "10010111100", "10010011110", "10111100100",  # 80-83
            "10011110100", "10011110010", "11110100100", "11110010100",  # 84-87
            "11110010010", "11011011110", "11011110110", "11110110110",  # 88-91
            "10101111000", "10100011110", "10001011110", "10111101000",  # 92-95
            "10111100010", "11110101000", "11110100010", "10111011110",  # 96-99
            "10111101110", "11101011110", "11110101110", "11010000100",  # 100-103
            "11010010000", "11010011100", "11001110100",  # 104-106 (Start B, Start C, Code A)
        ]

        if 0 <= value < len(patterns):
            return patterns[value]

        # Default fallback for special codes
        return "11011001100"

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            "supported_types": ["EAN-13", "UPC-A", "Code 128"],
            "module_width_px": self._module_width_px,
            "bar_color": self._bar_color,
            "bg_color": self._bg_color,
            "ean13_width_modules": self.EAN13_WIDTH_MODULES,
            "code128_charset_b_size": len(self.CODE128_B),
        }
