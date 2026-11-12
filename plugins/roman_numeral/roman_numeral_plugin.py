"""
Roman Numeral Plugin for LiuHao AI OS

Provides integer to Roman numeral conversion, Roman to integer,
validation, arithmetic, extended numerals, and year conversion.

Capabilities:
- to_roman: Convert integer to Roman numeral
- from_roman: Convert Roman numeral to integer
- validate: Validate Roman numeral
- roman_add: Add Roman numerals
- roman_sub: Subtract Roman numerals
- year_to_roman: Convert year to Roman numeral
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
class RomanNumeralResult:
    """Result of a Roman numeral conversion."""
    input: str
    value: int
    roman: str
    is_valid: bool
    error: Optional[str] = None


class RomanNumeralPlugin(BasePlugin):
    """Roman numeral conversion and utilities plugin.

    Supports standard Roman numerals (1-3999), validation,
    arithmetic, extended numerals (with vinculum for larger values),
    and year conversion.

    Example:
        plugin = RomanNumeralPlugin()
        plugin.initialize()
        plugin.activate()

        print(plugin.to_roman(2024))  # "MMXXIV"
        print(plugin.from_roman("MCMLXXXIV"))  # 1984
    """

    # Standard Roman numeral values (descending order)
    ROMAN_VALUES = [
        (1000, 'M'),
        (900, 'CM'),
        (500, 'D'),
        (400, 'CD'),
        (100, 'C'),
        (90, 'XC'),
        (50, 'L'),
        (40, 'XL'),
        (10, 'X'),
        (9, 'IX'),
        (5, 'V'),
        (4, 'IV'),
        (1, 'I'),
    ]

    # Extended Roman numerals (with vinculum - overline means *1000)
    # Represented with lowercase letters for vinculum
    EXTENDED_VALUES = [
        (1000000, 'm'),  # M with vinculum
        (900000, 'cm'),  # CM with vinculum
        (500000, 'd'),   # D with vinculum
        (400000, 'cd'),  # CD with vinculum
        (100000, 'c'),   # C with vinculum
        (90000, 'xc'),   # XC with vinculum
        (50000, 'l'),    # L with vinculum
        (40000, 'xl'),   # XL with vinculum
        (10000, 'x'),    # X with vinculum
        (9000, 'ix'),    # IX with vinculum
        (5000, 'v'),     # V with vinculum
        (4000, 'iv'),    # IV with vinculum
        (1000, 'M'),
        (900, 'CM'),
        (500, 'D'),
        (400, 'CD'),
        (100, 'C'),
        (90, 'XC'),
        (50, 'L'),
        (40, 'XL'),
        (10, 'X'),
        (9, 'IX'),
        (5, 'V'),
        (4, 'IV'),
        (1, 'I'),
    ]

    # Individual symbol values
    SYMBOL_VALUES = {
        'I': 1, 'V': 5, 'X': 10, 'L': 50,
        'C': 100, 'D': 500, 'M': 1000,
    }

    def __init__(self) -> None:
        """Initialize the Roman numeral plugin."""
        metadata = PluginMetadata(
            name="roman-numeral",
            version="1.0.0",
            description="Roman numeral conversion, validation, arithmetic, and year conversion",
            author="LiuHao AI OS Team",
            categories=["math", "conversion", "historical"],
            keywords=["roman", "numeral", "conversion", "number", "ancient", "year"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the Roman numeral plugin."""
        try:
            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the Roman numeral plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the Roman numeral plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the Roman numeral plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "to_roman",
            "from_roman",
            "validate",
            "roman_add",
            "roman_sub",
            "year_to_roman",
            "extended_to_roman",
            "get_numeral_info",
        ]

    # ==================== Integer to Roman ====================

    def to_roman(self, number: int) -> RomanNumeralResult:
        """Convert an integer to a Roman numeral.

        Standard Roman numerals support values 1-3999.

        Args:
            number: Integer to convert.

        Returns:
            RomanNumeralResult with Roman numeral string.
        """
        self._ensure_active()
        self._increment_call_count()

        if number < 1:
            return RomanNumeralResult(
                input=str(number),
                value=number,
                roman="",
                is_valid=False,
                error="Roman numerals must be positive (>= 1)",
            )

        if number > 3999:
            return RomanNumeralResult(
                input=str(number),
                value=number,
                roman="",
                is_valid=False,
                error="Standard Roman numerals only support up to 3999. Use extended_to_roman for larger values.",
            )

        result = ''
        remaining = number

        for value, symbol in self.ROMAN_VALUES:
            while remaining >= value:
                result += symbol
                remaining -= value

        return RomanNumeralResult(
            input=str(number),
            value=number,
            roman=result,
            is_valid=True,
        )

    # ==================== Roman to Integer ====================

    def from_roman(self, roman: str) -> RomanNumeralResult:
        """Convert a Roman numeral string to an integer.

        Args:
            roman: Roman numeral string.

        Returns:
            RomanNumeralResult with integer value.
        """
        self._ensure_active()
        self._increment_call_count()

        roman_upper = roman.upper().strip()

        # Validate first
        valid, error = self.validate(roman_upper)
        if not valid:
            return RomanNumeralResult(
                input=roman,
                value=0,
                roman=roman_upper,
                is_valid=False,
                error=error,
            )

        result = 0
        prev_value = 0

        # Process from right to left
        for ch in reversed(roman_upper):
            value = self.SYMBOL_VALUES[ch]
            if value < prev_value:
                result -= value
            else:
                result += value
            prev_value = value

        return RomanNumeralResult(
            input=roman,
            value=result,
            roman=roman_upper,
            is_valid=True,
        )

    # ==================== Validation ====================

    def validate(self, roman: str) -> Tuple[bool, str]:
        """Validate a Roman numeral string.

        Checks for valid characters, proper subtractive notation,
        and other rules.

        Args:
            roman: Roman numeral string to validate.

        Returns:
            Tuple of (is_valid, error_message).
        """
        self._ensure_active()
        self._increment_call_count()

        if not roman:
            return False, "Empty string"

        roman_upper = roman.upper()

        # Check all characters are valid
        valid_chars = set('IVXLCDM')
        for ch in roman_upper:
            if ch not in valid_chars:
                return False, f"Invalid character: '{ch}'"

        # Check for too many consecutive identical symbols
        # I, X, C, M can be repeated up to 3 times
        # V, L, D can never be repeated
        for symbol in 'VLD':
            if roman_upper.count(symbol) > 1:
                return False, f"Symbol '{symbol}' cannot be repeated"

        for symbol in 'IXCM':
            count = 0
            max_count = 0
            for ch in roman_upper:
                if ch == symbol:
                    count += 1
                    max_count = max(max_count, count)
                else:
                    count = 0
            if max_count > 3:
                return False, f"Symbol '{symbol}' cannot appear more than 3 times consecutively"

        # Check subtractive notation rules
        # Only I, X, C can be subtracted
        # I can subtract from V and X only
        # X can subtract from L and C only
        # C can subtract from D and M only
        subtractive_pairs = {
            'IV': 4, 'IX': 9,
            'XL': 40, 'XC': 90,
            'CD': 400, 'CM': 900,
        }

        i = 0
        while i < len(roman_upper):
            if i + 1 < len(roman_upper):
                pair = roman_upper[i:i + 2]
                if pair in subtractive_pairs:
                    # Valid subtractive pair
                    i += 2
                    continue

                current_val = self.SYMBOL_VALUES[roman_upper[i]]
                next_val = self.SYMBOL_VALUES[roman_upper[i + 1]]

                if next_val > current_val:
                    return False, f"Invalid subtractive pair: '{pair}'"

            i += 1

        return True, "Valid Roman numeral"

    # ==================== Arithmetic ====================

    def roman_add(self, a: str, b: str) -> Dict[str, Any]:
        """Add two Roman numerals.

        Args:
            a: First Roman numeral.
            b: Second Roman numeral.

        Returns:
            Dict with sum as both Roman and integer.
        """
        self._ensure_active()
        self._increment_call_count()

        val_a = self.from_roman(a)
        val_b = self.from_roman(b)

        if not val_a.is_valid:
            return {"error": f"Invalid first numeral: {val_a.error}"}
        if not val_b.is_valid:
            return {"error": f"Invalid second numeral: {val_b.error}"}

        total = val_a.value + val_b.value

        if total > 3999:
            sum_roman = None
            overflow = True
        else:
            sum_result = self.to_roman(total)
            sum_roman = sum_result.roman
            overflow = False

        return {
            "a": a.upper(),
            "b": b.upper(),
            "value_a": val_a.value,
            "value_b": val_b.value,
            "sum": sum_roman,
            "sum_value": total,
            "overflow": overflow,
        }

    def roman_sub(self, a: str, b: str) -> Dict[str, Any]:
        """Subtract two Roman numerals.

        Args:
            a: First Roman numeral (minuend).
            b: Second Roman numeral (subtrahend).

        Returns:
            Dict with difference as both Roman and integer.
        """
        self._ensure_active()
        self._increment_call_count()

        val_a = self.from_roman(a)
        val_b = self.from_roman(b)

        if not val_a.is_valid:
            return {"error": f"Invalid first numeral: {val_a.error}"}
        if not val_b.is_valid:
            return {"error": f"Invalid second numeral: {val_b.error}"}

        diff = val_a.value - val_b.value

        if diff <= 0:
            return {
                "a": a.upper(),
                "b": b.upper(),
                "value_a": val_a.value,
                "value_b": val_b.value,
                "difference": None,
                "difference_value": diff,
                "negative": diff < 0,
                "error": "Result is not positive (Roman numerals are positive)",
            }

        diff_result = self.to_roman(diff)

        return {
            "a": a.upper(),
            "b": b.upper(),
            "value_a": val_a.value,
            "value_b": val_b.value,
            "difference": diff_result.roman,
            "difference_value": diff,
            "negative": False,
        }

    # ==================== Year Conversion ====================

    def year_to_roman(self, year: int) -> Dict[str, Any]:
        """Convert a year to Roman numerals (with century info).

        Args:
            year: Year number (positive for CE, negative for BCE).

        Returns:
            Dict with Roman numeral and era info.
        """
        self._ensure_active()
        self._increment_call_count()

        is_bce = year < 0
        abs_year = abs(year)

        if abs_year > 3999:
            return {
                "year": year,
                "roman": None,
                "era": "BCE" if is_bce else "CE",
                "error": "Year out of range for standard Roman numerals",
            }

        result = self.to_roman(abs_year)

        # Century
        century = (abs_year - 1) // 100 + 1
        century_roman = self.to_roman(century).roman if century <= 3999 else str(century)

        # Millennium
        millennium = (abs_year - 1) // 1000 + 1

        return {
            "year": year,
            "roman": result.roman,
            "era": "BCE" if is_bce else "CE",
            "century": century,
            "century_roman": century_roman,
            "millennium": millennium,
            "decade": (abs_year // 10) * 10,
        }

    # ==================== Extended Roman Numerals ====================

    def extended_to_roman(self, number: int) -> Dict[str, Any]:
        """Convert larger numbers using extended Roman numerals.

        Uses lowercase letters to represent values with vinculum
        (overline, meaning multiply by 1000).

        Args:
            number: Integer to convert (up to ~4 million).

        Returns:
            Dict with extended Roman numeral representation.
        """
        self._ensure_active()
        self._increment_call_count()

        if number < 1:
            return {"error": "Number must be positive"}

        if number > 4000000:
            return {"error": "Number too large for extended Roman numerals"}

        result = ''
        remaining = number

        for value, symbol in self.EXTENDED_VALUES:
            while remaining >= value:
                result += symbol
                remaining -= value

        # Separate vinculum (lowercase) and standard (uppercase) parts
        vinculum_part = ''.join(c for c in result if c.islower())
        standard_part = ''.join(c for c in result if c.isupper())

        return {
            "value": number,
            "roman": result,
            "vinculum_part": vinculum_part.upper(),
            "standard_part": standard_part,
            "vinculum_value": number // 1000 * 1000 if number >= 4000 else 0,
            "standard_value": number % 1000,
            "note": "Lowercase letters represent values with vinculum (x1000)",
        }

    # ==================== Numeral Info ====================

    def get_numeral_info(self) -> Dict[str, Any]:
        """Get information about Roman numeral symbols.

        Returns:
            Dict with symbol information.
        """
        self._ensure_active()
        self._increment_call_count()

        return {
            "symbols": [
                {"symbol": "I", "value": 1, "name": "unus"},
                {"symbol": "V", "value": 5, "name": "quinque"},
                {"symbol": "X", "value": 10, "name": "decem"},
                {"symbol": "L", "value": 50, "name": "quinquaginta"},
                {"symbol": "C", "value": 100, "name": "centum"},
                {"symbol": "D", "value": 500, "name": "quingenti"},
                {"symbol": "M", "value": 1000, "name": "mille"},
            ],
            "subtractive_pairs": [
                {"pair": "IV", "value": 4},
                {"pair": "IX", "value": 9},
                {"pair": "XL", "value": 40},
                {"pair": "XC", "value": 90},
                {"pair": "CD", "value": 400},
                {"pair": "CM", "value": 900},
            ],
            "max_value_standard": 3999,
            "max_value_extended": 3999999,
            "rules": [
                "Symbols I, X, C, M can be repeated up to 3 times",
                "Symbols V, L, D can never be repeated",
                "Only I, X, C can be used subtractively",
                "I subtracts from V and X only",
                "X subtracts from L and C only",
                "C subtracts from D and M only",
            ],
        }

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            "standard_symbols": len(self.SYMBOL_VALUES),
            "subtractive_pairs": 6,
            "max_standard_value": 3999,
            "max_extended_value": 3999999,
            "supports_arithmetic": True,
            "supports_validation": True,
            "supports_extended": True,
        }
