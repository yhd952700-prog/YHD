"""
Cron Expression Parser Plugin for LiuHao AI OS

Provides cron expression parsing, next run time calculation,
human-readable description, validation, schedule listing,
and cron expression generation.

Capabilities:
- parse: Parse and validate cron expression
- next_run: Calculate next run time
- describe: Human-readable description
- validate: Validate cron expression
- next_runs: List next N run times
- generate: Generate cron expression from parameters
"""

from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import calendar

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class CronField:
    """A parsed cron field."""
    field: str  # minute, hour, day_of_month, month, day_of_week
    raw: str
    values: List[int]
    is_wildcard: bool


@dataclass
class CronParseResult:
    """Result of cron expression parsing."""
    expression: str
    fields: Dict[str, CronField]
    is_valid: bool
    error: Optional[str] = None


class CronPlugin(BasePlugin):
    """Cron expression parser and utilities plugin.

    Supports standard 5-field cron syntax:
    minute hour day-of-month month day-of-week

    Provides parsing, validation, next run calculation,
    human-readable descriptions, and schedule listing.

    Example:
        plugin = CronPlugin()
        plugin.initialize()
        plugin.activate()

        next = plugin.next_run("0 9 * * 1-5")
        print(next)  # Next weekday at 9:00
    """

    # Field names in order
    FIELDS = ["minute", "hour", "day_of_month", "month", "day_of_week"]

    # Field ranges
    FIELD_RANGES = {
        "minute": (0, 59),
        "hour": (0, 23),
        "day_of_month": (1, 31),
        "month": (1, 12),
        "day_of_week": (0, 7),  # 0 and 7 both mean Sunday
    }

    # Month names
    MONTH_NAMES = {
        1: "January", 2: "February", 3: "March", 4: "April",
        5: "May", 6: "June", 7: "July", 8: "August",
        9: "September", 10: "October", 11: "November", 12: "December",
    }

    # Day of week names
    DAY_NAMES = {
        0: "Sunday", 1: "Monday", 2: "Tuesday", 3: "Wednesday",
        4: "Thursday", 5: "Friday", 6: "Saturday", 7: "Sunday",
    }

    # Common cron presets
    PRESETS = {
        "@yearly": "0 0 1 1 *",
        "@annually": "0 0 1 1 *",
        "@monthly": "0 0 1 * *",
        "@weekly": "0 0 * * 0",
        "@daily": "0 0 * * *",
        "@midnight": "0 0 * * *",
        "@hourly": "0 * * * *",
        "@reboot": "N/A (run at startup)",
    }

    def __init__(self) -> None:
        """Initialize the cron plugin."""
        metadata = PluginMetadata(
            name="cron",
            version="1.0.0",
            description="Cron expression parser, next run calculator, and schedule generator",
            author="LiuHao AI OS Team",
            categories=["scheduling", "utility", "time"],
            keywords=["cron", "schedule", "cronjob", "expression", "timer", "automation"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the cron plugin."""
        try:
            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the cron plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the cron plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the cron plugin."""
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
            "next_run",
            "describe",
            "validate",
            "next_runs",
            "generate",
            "get_presets",
        ]

    # ==================== Cron Parsing ====================

    def parse(self, expression: str) -> CronParseResult:
        """Parse and validate a cron expression.

        Args:
            expression: Cron expression string (5 fields).

        Returns:
            CronParseResult with parsed fields.
        """
        self._ensure_active()
        self._increment_call_count()

        # Check for presets
        expr_lower = expression.strip().lower()
        if expr_lower in self.PRESETS:
            preset_value = self.PRESETS[expr_lower]
            if preset_value == "N/A (run at startup)":
                return CronParseResult(
                    expression=expression,
                    fields={},
                    is_valid=False,
                    error="@reboot cannot be parsed as a time expression",
                )
            expression = preset_value

        parts = expression.strip().split()
        if len(parts) != 5:
            return CronParseResult(
                expression=expression,
                fields={},
                is_valid=False,
                error=f"Expected 5 fields, got {len(parts)}",
            )

        fields: Dict[str, CronField] = {}

        for field_name, raw in zip(self.FIELDS, parts):
            min_val, max_val = self.FIELD_RANGES[field_name]

            try:
                values, is_wildcard = self._parse_field(raw, min_val, max_val, field_name)
            except ValueError as e:
                return CronParseResult(
                    expression=expression,
                    fields=fields,
                    is_valid=False,
                    error=f"Invalid {field_name} field '{raw}': {e}",
                )

            fields[field_name] = CronField(
                field=field_name,
                raw=raw,
                values=values,
                is_wildcard=is_wildcard,
            )

        # Validate day of week: normalize 7 to 0
        if "day_of_week" in fields:
            dow = fields["day_of_week"]
            normalized = list(set(v % 7 for v in dow.values))
            fields["day_of_week"] = CronField(
                field=dow.field,
                raw=dow.raw,
                values=sorted(normalized),
                is_wildcard=dow.is_wildcard,
            )

        return CronParseResult(
            expression=expression,
            fields=fields,
            is_valid=True,
        )

    def _parse_field(
        self, raw: str, min_val: int, max_val: int, field_name: str
    ) -> Tuple[List[int], bool]:
        """Parse a single cron field.

        Returns:
            Tuple of (values_list, is_wildcard).
        """
        if raw == '*':
            return list(range(min_val, max_val + 1)), True

        values = set()

        # Split by commas
        for part in raw.split(','):
            part = part.strip()
            if not part:
                raise ValueError(f"Empty value in list")

            # Check for step value
            step = 1
            if '/' in part:
                range_part, step_str = part.split('/', 1)
                try:
                    step = int(step_str)
                except ValueError:
                    raise ValueError(f"Invalid step value: {step_str}")
                if step <= 0:
                    raise ValueError(f"Step must be positive")
            else:
                range_part = part

            # Parse range
            if '-' in range_part:
                start_str, end_str = range_part.split('-', 1)
                try:
                    start = int(start_str)
                    end = int(end_str)
                except ValueError:
                    raise ValueError(f"Invalid range: {range_part}")
                if start < min_val or end > max_val:
                    raise ValueError(
                        f"Range {start}-{end} out of bounds ({min_val}-{max_val})"
                    )
                if start > end:
                    raise ValueError(f"Start greater than end: {range_part}")

                for v in range(start, end + 1, step):
                    values.add(v)
            elif range_part == '*':
                for v in range(min_val, max_val + 1, step):
                    values.add(v)
            else:
                try:
                    val = int(range_part)
                except ValueError:
                    raise ValueError(f"Invalid value: {range_part}")
                if val < min_val or val > max_val:
                    raise ValueError(
                        f"Value {val} out of bounds ({min_val}-{max_val})"
                    )
                values.add(val)

        return sorted(values), False

    # ==================== Validation ====================

    def validate(self, expression: str) -> Tuple[bool, str]:
        """Validate a cron expression.

        Args:
            expression: Cron expression to validate.

        Returns:
            Tuple of (is_valid, error_message).
        """
        self._ensure_active()
        self._increment_call_count()

        result = self.parse(expression)
        if result.is_valid:
            return True, "Valid cron expression"
        else:
            return False, result.error or "Invalid cron expression"

    # ==================== Next Run Calculation ====================

    def next_run(
        self,
        expression: str,
        from_time: Optional[datetime] = None,
    ) -> Optional[datetime]:
        """Calculate the next run time for a cron expression.

        Args:
            expression: Cron expression.
            from_time: Start time (defaults to now).

        Returns:
            Next run datetime, or None if expression is invalid.
        """
        self._ensure_active()
        self._increment_call_count()

        parsed = self.parse(expression)
        if not parsed.is_valid:
            return None

        if from_time is None:
            from_time = datetime.now()

        # Start from the next minute
        current = from_time.replace(second=0, microsecond=0) + timedelta(minutes=1)

        # Limit search to prevent infinite loop (5 years)
        max_iterations = 365 * 24 * 60 * 5
        iterations = 0

        while iterations < max_iterations:
            iterations += 1

            # Check month
            if current.month not in parsed.fields["month"].values:
                # Jump to first day of next valid month
                if current.month >= 12:
                    current = current.replace(year=current.year + 1, month=1, day=1,
                                              hour=0, minute=0)
                else:
                    current = current.replace(month=current.month + 1, day=1,
                                              hour=0, minute=0)
                continue

            # Check day of month
            days_in_month = calendar.monthrange(current.year, current.month)[1]
            if current.day > days_in_month:
                current = current.replace(month=current.month + 1, day=1,
                                          hour=0, minute=0)
                continue

            if current.day not in parsed.fields["day_of_month"].values:
                current += timedelta(days=1)
                current = current.replace(hour=0, minute=0)
                continue

            # Check day of week (Monday=0 in Python weekday, but cron uses Sunday=0)
            dow = (current.weekday() + 1) % 7  # Convert to 0=Sunday
            if dow not in parsed.fields["day_of_week"].values:
                current += timedelta(days=1)
                current = current.replace(hour=0, minute=0)
                continue

            # Check hour
            if current.hour not in parsed.fields["hour"].values:
                if current.hour >= 23:
                    current += timedelta(days=1)
                    current = current.replace(hour=0, minute=0)
                else:
                    current = current.replace(hour=current.hour + 1, minute=0)
                continue

            # Check minute
            if current.minute not in parsed.fields["minute"].values:
                if current.minute >= 59:
                    if current.hour >= 23:
                        current += timedelta(days=1)
                        current = current.replace(hour=0, minute=0)
                    else:
                        current = current.replace(hour=current.hour + 1, minute=0)
                else:
                    current = current.replace(minute=current.minute + 1)
                continue

            # All fields match!
            return current

        return None

    def next_runs(
        self,
        expression: str,
        count: int = 5,
        from_time: Optional[datetime] = None,
    ) -> List[datetime]:
        """Get the next N run times.

        Args:
            expression: Cron expression.
            count: Number of next runs to return.
            from_time: Start time (defaults to now).

        Returns:
            List of next run datetimes.
        """
        self._ensure_active()
        self._increment_call_count()

        runs: List[datetime] = []
        current = from_time

        for _ in range(count):
            next_time = self.next_run(expression, current)
            if next_time is None:
                break
            runs.append(next_time)
            current = next_time

        return runs

    # ==================== Description ====================

    def describe(self, expression: str) -> Dict[str, Any]:
        """Generate a human-readable description of a cron expression.

        Args:
            expression: Cron expression.

        Returns:
            Dict with description text and components.
        """
        self._ensure_active()
        self._increment_call_count()

        parsed = self.parse(expression)
        if not parsed.is_valid:
            return {"error": parsed.error}

        parts: List[str] = []

        # Minute and hour (time)
        minute_field = parsed.fields["minute"]
        hour_field = parsed.fields["hour"]

        if minute_field.is_wildcard and hour_field.is_wildcard:
            time_desc = "every minute"
        elif minute_field.is_wildcard:
            hours = ', '.join(str(h) for h in hour_field.values)
            time_desc = f"every minute during hour(s) {hours}"
        elif hour_field.is_wildcard:
            minutes = ', '.join(str(m) for m in minute_field.values)
            time_desc = f"at minute(s) {minutes} of every hour"
        else:
            if len(minute_field.values) == 1 and len(hour_field.values) == 1:
                time_desc = f"at {hour_field.values[0]:02d}:{minute_field.values[0]:02d}"
            else:
                times = []
                for h in hour_field.values:
                    for m in minute_field.values:
                        times.append(f"{h:02d}:{m:02d}")
                time_desc = f"at {', '.join(times[:5])}"
                if len(times) > 5:
                    time_desc += f" (+{len(times) - 5} more)"

        parts.append(time_desc)

        # Day of month
        dom_field = parsed.fields["day_of_month"]
        if not dom_field.is_wildcard:
            if len(dom_field.values) == 1:
                parts.append(f"on day {dom_field.values[0]} of the month")
            else:
                days = ', '.join(str(d) for d in dom_field.values[:5])
                parts.append(f"on days {days} of the month")

        # Month
        month_field = parsed.fields["month"]
        if not month_field.is_wildcard:
            if len(month_field.values) == 1:
                parts.append(f"in {self.MONTH_NAMES[month_field.values[0]]}")
            else:
                months = ', '.join(
                    self.MONTH_NAMES[m] for m in month_field.values[:5]
                )
                parts.append(f"in {months}")

        # Day of week
        dow_field = parsed.fields["day_of_week"]
        if not dow_field.is_wildcard:
            if len(dow_field.values) == 1:
                parts.append(f"on {self.DAY_NAMES[dow_field.values[0]]}s")
            elif len(dow_field.values) == 2 and 1 in dow_field.values and 5 in dow_field.values:
                parts.append("on weekdays")
            elif len(dow_field.values) == 2 and 0 in dow_field.values and 6 in dow_field.values:
                parts.append("on weekends")
            else:
                days = ', '.join(
                    self.DAY_NAMES[d] for d in dow_field.values[:5]
                )
                parts.append(f"on {days}")

        description = ", ".join(parts)

        return {
            "expression": expression,
            "description": description.capitalize(),
            "components": {
                "minute": minute_field.values,
                "hour": hour_field.values,
                "day_of_month": dom_field.values,
                "month": month_field.values,
                "day_of_week": dow_field.values,
            },
        }

    # ==================== Cron Generation ====================

    def generate(
        self,
        minute: str = "*",
        hour: str = "*",
        day_of_month: str = "*",
        month: str = "*",
        day_of_week: str = "*",
    ) -> Dict[str, Any]:
        """Generate a cron expression from individual fields.

        Args:
            minute: Minute field value.
            hour: Hour field value.
            day_of_month: Day of month field value.
            month: Month field value.
            day_of_week: Day of week field value.

        Returns:
            Dict with generated cron expression and validation.
        """
        self._ensure_active()
        self._increment_call_count()

        expression = f"{minute} {hour} {day_of_month} {month} {day_of_week}"
        parsed = self.parse(expression)

        return {
            "expression": expression,
            "is_valid": parsed.is_valid,
            "error": parsed.error,
            "description": self.describe(expression).get("description", "")
            if parsed.is_valid else None,
        }

    # ==================== Presets ====================

    def get_presets(self) -> Dict[str, str]:
        """Get common cron presets.

        Returns:
            Dict of preset name to cron expression.
        """
        self._ensure_active()
        self._increment_call_count()
        return dict(self.PRESETS)

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            "fields": 5,
            "field_names": self.FIELDS,
            "presets": len(self.PRESETS),
            "preset_names": list(self.PRESETS.keys()),
            "minute_range": f"{self.FIELD_RANGES['minute'][0]}-{self.FIELD_RANGES['minute'][1]}",
            "hour_range": f"{self.FIELD_RANGES['hour'][0]}-{self.FIELD_RANGES['hour'][1]}",
            "day_range": f"{self.FIELD_RANGES['day_of_month'][0]}-{self.FIELD_RANGES['day_of_month'][1]}",
            "month_range": f"{self.FIELD_RANGES['month'][0]}-{self.FIELD_RANGES['month'][1]}",
            "dow_range": f"{self.FIELD_RANGES['day_of_week'][0]}-{self.FIELD_RANGES['day_of_week'][1]}",
        }
