"""
Number Base Conversion Plugin for LiuHao AI OS

Provides conversion between bases (2-36), binary/octal/decimal/hex,
arbitrary base conversion, fractional numbers, base arithmetic,
and representation options.

Capabilities:
- convert: Convert number between bases
- to_binary / to_octal / to_decimal / to_hex: Quick conversions
- fractional_convert: Convert fractional numbers
- base_add / base_sub: Base arithmetic
- represent: Format with representation options
- validate: Validate number in a given base
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
class BaseConversionResult:
    """Result of a base conversion."""
    value: str
    from_base: int
    to_base: int
    decimal_value: int
    result: str
    is_valid: bool
    error: Optional[str] = None


class NumberBasePlugin(BasePlugin):
    """Number base conversion plugin.

    Supports conversion between any bases 2-36, fractional numbers,
    base arithmetic, and various representation formats.

    Example:
        plugin = NumberBasePlugin()
        plugin.initialize()
        plugin.activate()

        result = plugin.convert("FF", 16, 2)
        print(result.result)  # "11111111"
    """

    DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

    # Common base names
    BASE_NAMES = {
        2: "binary",
        3: "ternary",
        8: "octal",
        10: "decimal",
        12: "duodecimal",
        16: "hexadecimal",
        20: "vigesimal",
        36: "base36",
    }

    def __init__(self) -> None:
        """Initialize the number base plugin."""
        metadata = PluginMetadata(
            name="number-base",
            version="1.0.0",
            description="Number base conversion (2-36), fractions, arithmetic, and formatting",
            author="LiuHao AI OS Team",
            categories=["math", "conversion", "utility"],
            keywords=["base", "binary", "hex", "octal", "decimal", "conversion", "number"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the number base plugin."""
        try:
            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the number base plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the number base plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the number base plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "convert",
            "to_binary",
            "to_octal",
            "to_decimal",
            "to_hex",
            "fractional_convert",
            "base_add",
            "base_sub",
            "validate",
            "represent",
            "get_base_info",
        ]

    # ==================== Core Conversion ====================

    def convert(
        self,
        value: str,
        from_base: int,
        to_base: int,
    ) -> BaseConversionResult:
        """Convert a number from one base to another.

        Args:
            value: Number string to convert.
            from_base: Source base (2-36).
            to_base: Target base (2-36).

        Returns:
            BaseConversionResult with converted value.
        """
        self._ensure_active()
        self._increment_call_count()

        # Validate bases
        if not (2 <= from_base <= 36):
            return BaseConversionResult(
                value=value,
                from_base=from_base,
                to_base=to_base,
                decimal_value=0,
                result="",
                is_valid=False,
                error=f"Invalid from_base: {from_base} (must be 2-36)",
            )

        if not (2 <= to_base <= 36):
            return BaseConversionResult(
                value=value,
                from_base=from_base,
                to_base=to_base,
                decimal_value=0,
                result="",
                is_valid=False,
                error=f"Invalid to_base: {to_base} (must be 2-36)",
            )

        # Handle negative numbers
        is_negative = value.startswith('-')
        if is_negative:
            value = value[1:]

        # Validate value
        valid, error = self.validate(value, from_base)
        if not valid:
            return BaseConversionResult(
                value=value,
                from_base=from_base,
                to_base=to_base,
                decimal_value=0,
                result="",
                is_valid=False,
                error=error,
            )

        # Convert to decimal first
        decimal_val = self._to_decimal(value.upper(), from_base)

        # Convert from decimal to target base
        result = self._from_decimal(decimal_val, to_base)

        if is_negative:
            result = '-' + result

        return BaseConversionResult(
            value=value,
            from_base=from_base,
            to_base=to_base,
            decimal_value=decimal_val if not is_negative else -decimal_val,
            result=result,
            is_valid=True,
        )

    def _to_decimal(self, value: str, base: int) -> int:
        """Convert a number string from given base to decimal."""
        result = 0
        for ch in value.upper():
            digit = self.DIGITS.index(ch)
            result = result * base + digit
        return result

    def _from_decimal(self, value: int, base: int) -> str:
        """Convert a decimal integer to given base."""
        if value == 0:
            return "0"

        result: List[str] = []
        while value > 0:
            result.append(self.DIGITS[value % base])
            value //= base

        return ''.join(reversed(result))

    # ==================== Quick Conversions ====================

    def to_binary(self, value: str, from_base: int = 10) -> str:
        """Convert number to binary (base 2).

        Args:
            value: Number to convert.
            from_base: Source base.

        Returns:
            Binary string.
        """
        result = self.convert(value, from_base, 2)
        return result.result

    def to_octal(self, value: str, from_base: int = 10) -> str:
        """Convert number to octal (base 8)."""
        result = self.convert(value, from_base, 8)
        return result.result

    def to_decimal(self, value: str, from_base: int) -> int:
        """Convert number to decimal (base 10)."""
        result = self.convert(value, from_base, 10)
        return int(result.result) if result.is_valid else 0

    def to_hex(self, value: str, from_base: int = 10) -> str:
        """Convert number to hexadecimal (base 16)."""
        result = self.convert(value, from_base, 16)
        return result.result

    # ==================== Fractional Conversion ====================

    def fractional_convert(
        self,
        value: str,
        from_base: int,
        to_base: int,
        precision: int = 8,
    ) -> Dict[str, Any]:
        """Convert a fractional number between bases.

        Args:
            value: Number string (may contain decimal point).
            from_base: Source base.
            to_base: Target base.
            precision: Fractional digits precision.

        Returns:
            Dict with integer part, fractional part, and combined result.
        """
        self._ensure_active()
        self._increment_call_count()

        # Split integer and fractional parts
        if '.' in value:
            int_part_str, frac_part_str = value.split('.', 1)
        else:
            int_part_str = value
            frac_part_str = ''

        is_negative = int_part_str.startswith('-')
        if is_negative:
            int_part_str = int_part_str[1:]

        # Convert integer part
        int_val = self._to_decimal(int_part_str.upper() or '0', from_base)

        # Convert fractional part
        frac_val = 0.0
        if frac_part_str:
            for i, ch in enumerate(frac_part_str.upper()):
                digit = self.DIGITS.index(ch)
                frac_val += digit / (from_base ** (i + 1))

        decimal_total = int_val + frac_val

        # Convert integer part to target base
        int_result = self._from_decimal(int_val, to_base)

        # Convert fractional part to target base
        frac_result_digits: List[str] = []
        frac_working = frac_val
        for _ in range(precision):
            frac_working *= to_base
            digit_int = int(frac_working)
            frac_result_digits.append(self.DIGITS[digit_int])
            frac_working -= digit_int

        frac_result = ''.join(frac_result_digits)

        # Combine
        if frac_result:
            result = f"{int_result}.{frac_result}"
        else:
            result = int_result

        if is_negative:
            result = '-' + result

        return {
            "value": value,
            "from_base": from_base,
            "to_base": to_base,
            "integer_part": int_result,
            "fractional_part": frac_result,
            "result": result,
            "decimal_value": decimal_total if not is_negative else -decimal_total,
            "precision": precision,
        }

    # ==================== Base Arithmetic ====================

    def base_add(
        self,
        a: str,
        b: str,
        base: int,
    ) -> Dict[str, Any]:
        """Add two numbers in a given base.

        Args:
            a: First number.
            b: Second number.
            base: Base of both numbers.

        Returns:
            Dict with sum in the given base.
        """
        self._ensure_active()
        self._increment_call_count()

        dec_a = self._to_decimal(a.upper(), base)
        dec_b = self._to_decimal(b.upper(), base)
        dec_sum = dec_a + dec_b

        result = self._from_decimal(dec_sum, base)

        return {
            "a": a,
            "b": b,
            "base": base,
            "sum": result,
            "decimal_sum": dec_sum,
            "decimal_a": dec_a,
            "decimal_b": dec_b,
        }

    def base_sub(
        self,
        a: str,
        b: str,
        base: int,
    ) -> Dict[str, Any]:
        """Subtract two numbers in a given base.

        Args:
            a: First number (minuend).
            b: Second number (subtrahend).
            base: Base of both numbers.

        Returns:
            Dict with difference in the given base.
        """
        self._ensure_active()
        self._increment_call_count()

        dec_a = self._to_decimal(a.upper(), base)
        dec_b = self._to_decimal(b.upper(), base)
        dec_diff = dec_a - dec_b

        if dec_diff < 0:
            result = '-' + self._from_decimal(-dec_diff, base)
        else:
            result = self._from_decimal(dec_diff, base)

        return {
            "a": a,
            "b": b,
            "base": base,
            "difference": result,
            "decimal_difference": dec_diff,
            "decimal_a": dec_a,
            "decimal_b": dec_b,
        }

    # ==================== Validation ====================

    def validate(self, value: str, base: int) -> Tuple[bool, str]:
        """Validate that a number string is valid for a given base.

        Args:
            value: Number string to validate.
            base: Base to validate against.

        Returns:
            Tuple of (is_valid, error_message).
        """
        self._ensure_active()
        self._increment_call_count()

        if not value:
            return False, "Empty value"

        # Handle negative sign
        val = value
        if val.startswith('-'):
            val = val[1:]

        # Handle decimal point
        if '.' in val:
            parts = val.split('.')
            if len(parts) != 2:
                return False, "Multiple decimal points"
            val = parts[0] + parts[1]

        if not val:
            return False, "No digits found"

        valid_digits = set(self.DIGITS[:base])

        for i, ch in enumerate(val.upper()):
            if ch not in valid_digits:
                digit_val = self.DIGITS.index(ch) if ch in self.DIGITS else None
                if digit_val is not None:
                    return False, (
                        f"Invalid digit '{ch}' at position {i}: "
                        f"value {digit_val} >= base {base}"
                    )
                else:
                    return False, f"Invalid character '{ch}' at position {i}"

        return True, "Valid"

    # ==================== Representation ====================

    def represent(
        self,
        value: str,
        from_base: int = 10,
        prefix: bool = False,
        group_digits: bool = False,
        group_size: int = 4,
        uppercase: bool = True,
    ) -> Dict[str, Any]:
        """Generate representations in multiple bases.

        Args:
            value: Number to represent.
            from_base: Source base.
            prefix: Add base prefixes (0b, 0o, 0x).
            group_digits: Group digits for readability.
            group_size: Number of digits per group.
            uppercase: Use uppercase letters.

        Returns:
            Dict with representations in common bases.
        """
        self._ensure_active()
        self._increment_call_count()

        # Convert to decimal first
        is_negative = value.startswith('-')
        val = value[1:] if is_negative else value

        decimal = self._to_decimal(val.upper(), from_base)
        if is_negative:
            decimal = -decimal

        bases = [2, 8, 10, 16, 36]
        representations: Dict[str, str] = {}

        for base in bases:
            name = self.BASE_NAMES.get(base, f"base{base}")
            converted = self._from_decimal(abs(decimal), base)

            if not uppercase:
                converted = converted.lower()

            if group_digits and base != 10:
                # Group from right
                groups = []
                reversed_str = converted[::-1]
                for i in range(0, len(reversed_str), group_size):
                    groups.append(reversed_str[i:i + group_size])
                converted = ' '.join(g[::-1] for g in reversed(groups))

            if prefix:
                if base == 2:
                    converted = '0b' + converted
                elif base == 8:
                    converted = '0o' + converted
                elif base == 16:
                    converted = '0x' + converted

            if is_negative:
                converted = '-' + converted

            representations[name] = converted

        return {
            "value": value,
            "decimal_value": decimal,
            "representations": representations,
            "bit_length": decimal.bit_length() if decimal > 0 else 0,
            "byte_length": (decimal.bit_length() + 7) // 8 if decimal > 0 else 0,
        }

    # ==================== Base Info ====================

    def get_base_info(self, base: int) -> Dict[str, Any]:
        """Get information about a number base.

        Args:
            base: The base number (2-36).

        Returns:
            Dict with base information.
        """
        self._ensure_active()
        self._increment_call_count()

        if not (2 <= base <= 36):
            return {"error": f"Invalid base: {base} (must be 2-36)"}

        digits = self.DIGITS[:base]

        return {
            "base": base,
            "name": self.BASE_NAMES.get(base, f"Base {base}"),
            "digits": digits,
            "digit_count": base,
            "highest_digit": digits[-1],
            "highest_value": base - 1,
            "is_power_of_two": (base & (base - 1)) == 0,
        }

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            "min_base": 2,
            "max_base": 36,
            "available_bases": list(range(2, 37)),
            "named_bases": self.BASE_NAMES,
            "digits": self.DIGITS,
            "default_precision": 8,
        }
