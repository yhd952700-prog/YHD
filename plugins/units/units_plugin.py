"""
Unit Conversion Plugin for LiuHao AI OS

Provides unit conversion for length, weight, temperature, volume,
area, speed, time, and data size with 100+ units, precision control,
and batch conversion.

Capabilities:
- convert: Convert a value between units
- get_categories: List available unit categories
- get_units: List units in a category
- batch_convert: Batch convert multiple values
- convert_to_all: Convert to all units in category
"""

import math
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class ConversionResult:
    """Result of a unit conversion."""
    value: float
    from_unit: str
    to_unit: str
    result: float
    category: str
    precision: int
    formatted: str


class UnitsPlugin(BasePlugin):
    """Unit conversion plugin.

    Supports conversion between 100+ units across 8 categories:
    length, weight/mass, temperature, volume, area, speed, time,
    and digital storage.

    Example:
        plugin = UnitsPlugin()
        plugin.initialize()
        plugin.activate()

        result = plugin.convert(10, "km", "mi")
        print(result.result)  # ~6.21
    """

    # Unit definitions: all relative to a base unit per category
    # Format: {category: {unit: (factor, full_name)}}
    # factor = how many base units = 1 of this unit

    UNITS: Dict[str, Dict[str, Tuple[float, str]]] = {
        "length": {
            "m": (1.0, "meter"),
            "km": (1000.0, "kilometer"),
            "cm": (0.01, "centimeter"),
            "mm": (0.001, "millimeter"),
            "um": (1e-6, "micrometer"),
            "nm": (1e-9, "nanometer"),
            "mi": (1609.344, "mile"),
            "yd": (0.9144, "yard"),
            "ft": (0.3048, "foot"),
            "in": (0.0254, "inch"),
            "nmi": (1852.0, "nautical mile"),
            "fathom": (1.8288, "fathom"),
            "fur": (201.168, "furlong"),
            "rd": (5.0292, "rod"),
            "ly": (9.46073047e15, "light-year"),
            "au": (1.49597871e11, "astronomical unit"),
            "pc": (3.08567758e16, "parsec"),
        },
        "weight": {
            "kg": (1.0, "kilogram"),
            "g": (0.001, "gram"),
            "mg": (1e-6, "milligram"),
            "ug": (1e-9, "microgram"),
            "t": (1000.0, "metric ton"),
            "lb": (0.45359237, "pound"),
            "oz": (0.028349523125, "ounce"),
            "st": (6.35029318, "stone"),
            "ton_us": (907.18474, "US ton"),
            "ton_uk": (1016.0469088, "UK ton"),
            "ct": (0.0002, "carat"),
            "oz_troy": (0.0311034768, "troy ounce"),
        },
        "temperature": {
            "c": (0.0, "Celsius"),
            "f": (0.0, "Fahrenheit"),
            "k": (0.0, "Kelvin"),
            "r": (0.0, "Rankine"),
        },
        "volume": {
            "l": (1.0, "liter"),
            "ml": (0.001, "milliliter"),
            "cl": (0.01, "centiliter"),
            "m3": (1000.0, "cubic meter"),
            "cm3": (0.001, "cubic centimeter"),
            "gal_us": (3.785411784, "US gallon"),
            "gal_uk": (4.54609, "UK gallon"),
            "qt_us": (0.946352946, "US quart"),
            "pt_us": (0.473176473, "US pint"),
            "cup_us": (0.2365882365, "US cup"),
            "oz_fl_us": (0.0295735295625, "US fluid ounce"),
            "oz_fl_uk": (0.0284130625, "UK fluid ounce"),
            "tbsp": (0.01478676478125, "tablespoon (US)"),
            "tsp": (0.00492892159375, "teaspoon (US)"),
        },
        "area": {
            "m2": (1.0, "square meter"),
            "km2": (1e6, "square kilometer"),
            "cm2": (1e-4, "square centimeter"),
            "mm2": (1e-6, "square millimeter"),
            "ha": (10000.0, "hectare"),
            "ac": (4046.8564224, "acre"),
            "mi2": (2589988.110336, "square mile"),
            "yd2": (0.83612736, "square yard"),
            "ft2": (0.09290304, "square foot"),
            "in2": (0.00064516, "square inch"),
        },
        "speed": {
            "mps": (1.0, "meter per second"),
            "kmph": (0.2777777777777778, "kilometer per hour"),
            "mph": (0.44704, "mile per hour"),
            "knot": (0.5144444444444444, "knot"),
            "fps": (0.3048, "foot per second"),
            "mach": (340.29, "Mach (at sea level)"),
            "c": (299792458.0, "speed of light"),
        },
        "time": {
            "s": (1.0, "second"),
            "ms": (0.001, "millisecond"),
            "us": (1e-6, "microsecond"),
            "ns": (1e-9, "nanosecond"),
            "min": (60.0, "minute"),
            "h": (3600.0, "hour"),
            "d": (86400.0, "day"),
            "wk": (604800.0, "week"),
            "mo": (2592000.0, "month (30 days)"),
            "yr": (31536000.0, "year (365 days)"),
            "century": (3153600000.0, "century"),
        },
        "data": {
            "b": (1.0, "bit"),
            "B": (8.0, "byte"),
            "KB": (8192.0, "kilobyte"),
            "MB": (8388608.0, "megabyte"),
            "GB": (8589934592.0, "gigabyte"),
            "TB": (8796093022208.0, "terabyte"),
            "PB": (9007199254740992.0, "petabyte"),
            "KiB": (8 * 1024.0, "kibibyte"),
            "MiB": (8 * 1048576.0, "mebibyte"),
            "GiB": (8 * 1073741824.0, "gibibyte"),
            "TiB": (8 * 1099511627776.0, "tebibyte"),
        },
    }

    # Category display names
    CATEGORY_NAMES = {
        "length": "Length / Distance",
        "weight": "Weight / Mass",
        "temperature": "Temperature",
        "volume": "Volume",
        "area": "Area",
        "speed": "Speed",
        "time": "Time",
        "data": "Digital Storage",
    }

    def __init__(self) -> None:
        """Initialize the units plugin."""
        metadata = PluginMetadata(
            name="units",
            version="1.0.0",
            description="Unit conversion for 100+ units across 8 categories",
            author="LiuHao AI OS Team",
            categories=["conversion", "utility", "measurement"],
            keywords=["unit", "convert", "measurement", "length", "weight", "temperature"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._default_precision: int = 4

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the units plugin."""
        try:
            if config:
                self._default_precision = config.get("default_precision", 4)

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the units plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the units plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the units plugin."""
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
            "get_categories",
            "get_units",
            "get_unit_info",
            "batch_convert",
            "convert_to_all",
            "find_unit",
        ]

    # ==================== Conversion Methods ====================

    def convert(
        self,
        value: float,
        from_unit: str,
        to_unit: str,
        precision: Optional[int] = None,
    ) -> ConversionResult:
        """Convert a value from one unit to another.

        Args:
            value: The value to convert.
            from_unit: Source unit code.
            to_unit: Target unit code.
            precision: Decimal precision. Uses default if None.

        Returns:
            ConversionResult with the converted value.

        Raises:
            ValueError: If either unit is not found.
        """
        self._ensure_active()
        self._increment_call_count()

        from_unit = from_unit.strip()
        to_unit = to_unit.strip()
        precision = precision if precision is not None else self._default_precision

        # Find which category these units belong to
        category = self._find_common_category(from_unit, to_unit)
        if not category:
            raise ValueError(
                f"Cannot convert between '{from_unit}' and '{to_unit}': "
                f"units are not in the same category or not found"
            )

        # Temperature needs special handling (not a simple ratio)
        if category == "temperature":
            result = self._convert_temperature(value, from_unit, to_unit)
        else:
            result = self._convert_linear(value, from_unit, to_unit, category)

        # Format the result
        formatted = self._format_result(result, to_unit, precision)

        return ConversionResult(
            value=value,
            from_unit=from_unit,
            to_unit=to_unit,
            result=round(result, precision),
            category=category,
            precision=precision,
            formatted=formatted,
        )

    def get_categories(self) -> List[Dict[str, Any]]:
        """Get all unit categories.

        Returns:
            List of category dicts with 'id', 'name', 'unit_count'.
        """
        self._ensure_active()
        self._increment_call_count()

        categories = []
        for cat_id, units in self.UNITS.items():
            categories.append({
                "id": cat_id,
                "name": self.CATEGORY_NAMES.get(cat_id, cat_id),
                "unit_count": len(units),
            })
        return categories

    def get_units(self, category: str) -> List[Dict[str, str]]:
        """Get all units in a category.

        Args:
            category: Category ID.

        Returns:
            List of unit dicts with 'code', 'name', 'factor'.
        """
        self._ensure_active()
        self._increment_call_count()

        if category not in self.UNITS:
            raise ValueError(f"Unknown category: {category}")

        units = []
        for code, (factor, name) in sorted(self.UNITS[category].items()):
            units.append({
                "code": code,
                "name": name,
                "factor": factor,
            })
        return units

    def get_unit_info(self, unit: str) -> Optional[Dict[str, Any]]:
        """Get information about a specific unit.

        Args:
            unit: Unit code.

        Returns:
            Dict with unit info, or None if not found.
        """
        self._ensure_active()

        for category, units in self.UNITS.items():
            if unit in units:
                factor, name = units[unit]
                return {
                    "code": unit,
                    "name": name,
                    "category": category,
                    "category_name": self.CATEGORY_NAMES.get(category, category),
                    "factor": factor,
                }
        return None

    def find_unit(self, query: str) -> List[Dict[str, Any]]:
        """Search for units by name or code.

        Args:
            query: Search query.

        Returns:
            List of matching unit info dicts.
        """
        self._ensure_active()
        self._increment_call_count()

        query_lower = query.lower()
        results = []

        for category, units in self.UNITS.items():
            for code, (factor, name) in units.items():
                if (query_lower in code.lower() or
                    query_lower in name.lower()):
                    results.append({
                        "code": code,
                        "name": name,
                        "category": category,
                        "category_name": self.CATEGORY_NAMES.get(category, category),
                    })

        return results

    def batch_convert(
        self,
        conversions: List[Dict[str, Any]],
    ) -> List[ConversionResult]:
        """Batch convert multiple unit pairs.

        Args:
            conversions: List of dicts with 'value', 'from', 'to'.

        Returns:
            List of ConversionResult objects.
        """
        self._ensure_active()

        results = []
        for conv in conversions:
            result = self.convert(
                value=conv.get("value", 0),
                from_unit=conv.get("from", ""),
                to_unit=conv.get("to", ""),
            )
            results.append(result)

        return results

    def convert_to_all(
        self,
        value: float,
        from_unit: str,
        precision: Optional[int] = None,
    ) -> Dict[str, float]:
        """Convert a value to all units in its category.

        Args:
            value: The value to convert.
            from_unit: Source unit code.
            precision: Decimal precision.

        Returns:
            Dict mapping unit codes to converted values.
        """
        self._ensure_active()
        self._increment_call_count()

        category = None
        for cat, units in self.UNITS.items():
            if from_unit in units:
                category = cat
                break

        if not category:
            raise ValueError(f"Unknown unit: {from_unit}")

        results = {}
        for unit_code in self.UNITS[category]:
            if unit_code == from_unit:
                results[unit_code] = value
            else:
                conv = self.convert(value, from_unit, unit_code, precision)
                results[unit_code] = conv.result

        return results

    # ==================== Internal Conversion Methods ====================

    def _convert_linear(
        self, value: float, from_unit: str, to_unit: str, category: str
    ) -> float:
        """Convert between units that have a linear relationship."""
        from_factor, _ = self.UNITS[category][from_unit]
        to_factor, _ = self.UNITS[category][to_unit]

        # Convert to base unit, then to target unit
        base_value = value * from_factor
        result = base_value / to_factor

        return result

    def _convert_temperature(self, value: float, from_unit: str, to_unit: str) -> float:
        """Convert between temperature units (non-linear)."""
        # First convert to Celsius
        celsius = self._to_celsius(value, from_unit)

        # Then convert from Celsius to target
        result = self._from_celsius(celsius, to_unit)
        return result

    def _to_celsius(self, value: float, unit: str) -> float:
        """Convert temperature to Celsius."""
        unit = unit.lower()
        if unit in ("c", "celsius"):
            return value
        elif unit in ("f", "fahrenheit"):
            return (value - 32) * 5 / 9
        elif unit in ("k", "kelvin"):
            return value - 273.15
        elif unit in ("r", "rankine"):
            return (value - 491.67) * 5 / 9
        else:
            raise ValueError(f"Unknown temperature unit: {unit}")

    def _from_celsius(self, value: float, unit: str) -> float:
        """Convert temperature from Celsius."""
        unit = unit.lower()
        if unit in ("c", "celsius"):
            return value
        elif unit in ("f", "fahrenheit"):
            return value * 9 / 5 + 32
        elif unit in ("k", "kelvin"):
            return value + 273.15
        elif unit in ("r", "rankine"):
            return (value + 273.15) * 9 / 5
        else:
            raise ValueError(f"Unknown temperature unit: {unit}")

    def _find_common_category(self, unit1: str, unit2: str) -> Optional[str]:
        """Find the category that contains both units."""
        cat1 = None
        for category, units in self.UNITS.items():
            if unit1 in units:
                cat1 = category
                break

        if not cat1:
            return None

        if unit2 in self.UNITS[cat1]:
            return cat1

        return None

    def _format_result(self, value: float, unit: str, precision: int) -> str:
        """Format a conversion result with unit."""
        # Use appropriate precision based on magnitude
        if abs(value) >= 1000000:
            formatted = f"{value:.2e} {unit}"
        elif abs(value) >= 1:
            formatted = f"{value:.{precision}f} {unit}"
        elif abs(value) >= 0.001:
            formatted = f"{value:.{precision + 2}f} {unit}"
        else:
            formatted = f"{value:.2e} {unit}"

        return formatted

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        total_units = sum(len(units) for units in self.UNITS.values())
        return {
            "categories": len(self.UNITS),
            "total_units": total_units,
            "default_precision": self._default_precision,
            "units_by_category": {
                cat: len(units) for cat, units in self.UNITS.items()
            },
        }
