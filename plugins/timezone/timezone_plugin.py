"""
Timezone Handling Plugin for LiuHao AI OS

Provides timezone conversion, timezone list, current time in timezone,
UTC offset info, DST info, and timezone abbreviation lookup.

Capabilities:
- convert: Convert time between timezones
- get_current_time: Get current time in a timezone
- list_timezones: List available timezones
- get_utc_offset: Get UTC offset for a timezone
- get_dst_info: Get DST information
- lookup_abbreviation: Look up timezone abbreviation
- parse_with_timezone: Parse datetime with timezone
- format_time: Format time in a timezone
- time_diff: Calculate time difference between zones
- common_timezones: Get commonly used timezones
"""

import re
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class TimezoneInfo:
    """Information about a timezone."""
    name: str
    offset_hours: float
    offset_minutes: int
    abbreviation: str
    is_dst: bool
    utc_offset_str: str


@dataclass
class ConversionResult:
    """Result of a timezone conversion."""
    source_timezone: str
    target_timezone: str
    source_time: str
    target_time: str
    time_difference_hours: float
    source_offset: float
    target_offset: float


class TimezonePlugin(BasePlugin):
    """Timezone handling plugin.

    Provides timezone conversion, information lookup, and
    DST awareness using built-in Python timezone support.

    Example:
        plugin = TimezonePlugin()
        plugin.initialize()
        plugin.activate()

        result = plugin.convert("2024-01-15 12:00:00", "UTC", "America/New_York")
        print(result.target_time)
    """

    # Common timezone data: name -> (offset_hours, abbreviation_std, abbreviation_dst)
    # Uses fixed offsets (not accounting for DST in all cases)
    TIMEZONES: Dict[str, Dict[str, Any]] = {
        # UTC
        "UTC": {"offset": 0, "std_abbr": "UTC", "dst_abbr": "UTC", "has_dst": False},
        "GMT": {"offset": 0, "std_abbr": "GMT", "dst_abbr": "GMT", "has_dst": False},
        # Americas
        "America/New_York": {"offset": -5, "std_abbr": "EST", "dst_abbr": "EDT", "has_dst": True},
        "America/Chicago": {"offset": -6, "std_abbr": "CST", "dst_abbr": "CDT", "has_dst": True},
        "America/Denver": {"offset": -7, "std_abbr": "MST", "dst_abbr": "MDT", "has_dst": True},
        "America/Los_Angeles": {"offset": -8, "std_abbr": "PST", "dst_abbr": "PDT", "has_dst": True},
        "America/Anchorage": {"offset": -9, "std_abbr": "AKST", "dst_abbr": "AKDT", "has_dst": True},
        "America/Honolulu": {"offset": -10, "std_abbr": "HST", "dst_abbr": "HST", "has_dst": False},
        "America/Toronto": {"offset": -5, "std_abbr": "EST", "dst_abbr": "EDT", "has_dst": True},
        "America/Vancouver": {"offset": -8, "std_abbr": "PST", "dst_abbr": "PDT", "has_dst": True},
        "America/Mexico_City": {"offset": -6, "std_abbr": "CST", "dst_abbr": "CDT", "has_dst": True},
        "America/Sao_Paulo": {"offset": -3, "std_abbr": "BRT", "dst_abbr": "BRST", "has_dst": False},
        "America/Argentina/Buenos_Aires": {"offset": -3, "std_abbr": "ART", "dst_abbr": "ART", "has_dst": False},
        "America/Caracas": {"offset": -4, "std_abbr": "VET", "dst_abbr": "VET", "has_dst": False},
        "America/Santiago": {"offset": -4, "std_abbr": "CLT", "dst_abbr": "CLST", "has_dst": True},
        # Europe
        "Europe/London": {"offset": 0, "std_abbr": "GMT", "dst_abbr": "BST", "has_dst": True},
        "Europe/Dublin": {"offset": 0, "std_abbr": "GMT", "dst_abbr": "IST", "has_dst": True},
        "Europe/Paris": {"offset": 1, "std_abbr": "CET", "dst_abbr": "CEST", "has_dst": True},
        "Europe/Berlin": {"offset": 1, "std_abbr": "CET", "dst_abbr": "CEST", "has_dst": True},
        "Europe/Madrid": {"offset": 1, "std_abbr": "CET", "dst_abbr": "CEST", "has_dst": True},
        "Europe/Rome": {"offset": 1, "std_abbr": "CET", "dst_abbr": "CEST", "has_dst": True},
        "Europe/Amsterdam": {"offset": 1, "std_abbr": "CET", "dst_abbr": "CEST", "has_dst": True},
        "Europe/Brussels": {"offset": 1, "std_abbr": "CET", "dst_abbr": "CEST", "has_dst": True},
        "Europe/Vienna": {"offset": 1, "std_abbr": "CET", "dst_abbr": "CEST", "has_dst": True},
        "Europe/Zurich": {"offset": 1, "std_abbr": "CET", "dst_abbr": "CEST", "has_dst": True},
        "Europe/Stockholm": {"offset": 1, "std_abbr": "CET", "dst_abbr": "CEST", "has_dst": True},
        "Europe/Oslo": {"offset": 1, "std_abbr": "CET", "dst_abbr": "CEST", "has_dst": True},
        "Europe/Copenhagen": {"offset": 1, "std_abbr": "CET", "dst_abbr": "CEST", "has_dst": True},
        "Europe/Helsinki": {"offset": 2, "std_abbr": "EET", "dst_abbr": "EEST", "has_dst": True},
        "Europe/Athens": {"offset": 2, "std_abbr": "EET", "dst_abbr": "EEST", "has_dst": True},
        "Europe/Moscow": {"offset": 3, "std_abbr": "MSK", "dst_abbr": "MSK", "has_dst": False},
        "Europe/Istanbul": {"offset": 3, "std_abbr": "TRT", "dst_abbr": "TRT", "has_dst": False},
        "Europe/Kyiv": {"offset": 2, "std_abbr": "EET", "dst_abbr": "EEST", "has_dst": True},
        "Europe/Warsaw": {"offset": 1, "std_abbr": "CET", "dst_abbr": "CEST", "has_dst": True},
        "Europe/Prague": {"offset": 1, "std_abbr": "CET", "dst_abbr": "CEST", "has_dst": True},
        "Europe/Budapest": {"offset": 1, "std_abbr": "CET", "dst_abbr": "CEST", "has_dst": True},
        # Asia
        "Asia/Tokyo": {"offset": 9, "std_abbr": "JST", "dst_abbr": "JST", "has_dst": False},
        "Asia/Seoul": {"offset": 9, "std_abbr": "KST", "dst_abbr": "KST", "has_dst": False},
        "Asia/Shanghai": {"offset": 8, "std_abbr": "CST", "dst_abbr": "CST", "has_dst": False},
        "Asia/Hong_Kong": {"offset": 8, "std_abbr": "HKT", "dst_abbr": "HKT", "has_dst": False},
        "Asia/Singapore": {"offset": 8, "std_abbr": "SGT", "dst_abbr": "SGT", "has_dst": False},
        "Asia/Taipei": {"offset": 8, "std_abbr": "CST", "dst_abbr": "CST", "has_dst": False},
        "Asia/Bangkok": {"offset": 7, "std_abbr": "ICT", "dst_abbr": "ICT", "has_dst": False},
        "Asia/Jakarta": {"offset": 7, "std_abbr": "WIB", "dst_abbr": "WIB", "has_dst": False},
        "Asia/Kuala_Lumpur": {"offset": 8, "std_abbr": "MYT", "dst_abbr": "MYT", "has_dst": False},
        "Asia/Manila": {"offset": 8, "std_abbr": "PHT", "dst_abbr": "PHT", "has_dst": False},
        "Asia/Ho_Chi_Minh": {"offset": 7, "std_abbr": "ICT", "dst_abbr": "ICT", "has_dst": False},
        "Asia/Calcutta": {"offset": 5.5, "std_abbr": "IST", "dst_abbr": "IST", "has_dst": False},
        "Asia/Kolkata": {"offset": 5.5, "std_abbr": "IST", "dst_abbr": "IST", "has_dst": False},
        "Asia/Karachi": {"offset": 5, "std_abbr": "PKT", "dst_abbr": "PKT", "has_dst": False},
        "Asia/Dubai": {"offset": 4, "std_abbr": "GST", "dst_abbr": "GST", "has_dst": False},
        "Asia/Riyadh": {"offset": 3, "std_abbr": "AST", "dst_abbr": "AST", "has_dst": False},
        "Asia/Tehran": {"offset": 3.5, "std_abbr": "IRST", "dst_abbr": "IRDT", "has_dst": True},
        "Asia/Jerusalem": {"offset": 2, "std_abbr": "IST", "dst_abbr": "IDT", "has_dst": True},
        "Asia/Beirut": {"offset": 2, "std_abbr": "EET", "dst_abbr": "EEST", "has_dst": True},
        # Oceania
        "Australia/Sydney": {"offset": 10, "std_abbr": "AEST", "dst_abbr": "AEDT", "has_dst": True},
        "Australia/Melbourne": {"offset": 10, "std_abbr": "AEST", "dst_abbr": "AEDT", "has_dst": True},
        "Australia/Brisbane": {"offset": 10, "std_abbr": "AEST", "dst_abbr": "AEST", "has_dst": False},
        "Australia/Perth": {"offset": 8, "std_abbr": "AWST", "dst_abbr": "AWST", "has_dst": False},
        "Australia/Adelaide": {"offset": 9.5, "std_abbr": "ACST", "dst_abbr": "ACDT", "has_dst": True},
        "Pacific/Auckland": {"offset": 12, "std_abbr": "NZST", "dst_abbr": "NZDT", "has_dst": True},
        "Pacific/Fiji": {"offset": 12, "std_abbr": "FJT", "dst_abbr": "FJST", "has_dst": True},
        "Pacific/Honolulu": {"offset": -10, "std_abbr": "HST", "dst_abbr": "HST", "has_dst": False},
        # Africa
        "Africa/Cairo": {"offset": 2, "std_abbr": "EET", "dst_abbr": "EET", "has_dst": False},
        "Africa/Johannesburg": {"offset": 2, "std_abbr": "SAST", "dst_abbr": "SAST", "has_dst": False},
        "Africa/Lagos": {"offset": 1, "std_abbr": "WAT", "dst_abbr": "WAT", "has_dst": False},
        "Africa/Nairobi": {"offset": 3, "std_abbr": "EAT", "dst_abbr": "EAT", "has_dst": False},
        "Africa/Casablanca": {"offset": 0, "std_abbr": "WET", "dst_abbr": "WEST", "has_dst": True},
        # Atlantic
        "Atlantic/Reykjavik": {"offset": 0, "std_abbr": "GMT", "dst_abbr": "GMT", "has_dst": False},
        "Atlantic/Azores": {"offset": -1, "std_abbr": "AZOT", "dst_abbr": "AZOST", "has_dst": True},
    }

    # Abbreviation to timezone name mapping (many-to-one)
    ABBREVIATION_MAP: Dict[str, List[str]] = {
        "UTC": ["UTC"],
        "GMT": ["UTC", "Europe/London"],
        "EST": ["America/New_York", "America/Toronto"],
        "EDT": ["America/New_York", "America/Toronto"],
        "CST": ["America/Chicago", "Asia/Shanghai", "Asia/Taipei"],
        "CDT": ["America/Chicago"],
        "MST": ["America/Denver"],
        "MDT": ["America/Denver"],
        "PST": ["America/Los_Angeles", "America/Vancouver"],
        "PDT": ["America/Los_Angeles", "America/Vancouver"],
        "CET": ["Europe/Paris", "Europe/Berlin", "Europe/Rome", "Europe/Madrid"],
        "CEST": ["Europe/Paris", "Europe/Berlin", "Europe/Rome", "Europe/Madrid"],
        "EET": ["Europe/Helsinki", "Europe/Athens", "Africa/Cairo"],
        "EEST": ["Europe/Helsinki", "Europe/Athens"],
        "JST": ["Asia/Tokyo"],
        "KST": ["Asia/Seoul"],
        "HKT": ["Asia/Hong_Kong"],
        "SGT": ["Asia/Singapore"],
        "IST": ["Asia/Kolkata"],
        "AEST": ["Australia/Sydney", "Australia/Melbourne"],
        "AEDT": ["Australia/Sydney", "Australia/Melbourne"],
        "NZST": ["Pacific/Auckland"],
        "NZDT": ["Pacific/Auckland"],
        "BST": ["Europe/London"],
    }

    # Common/popular timezones
    COMMON_TIMEZONES = [
        "UTC",
        "America/New_York",
        "America/Chicago",
        "America/Denver",
        "America/Los_Angeles",
        "Europe/London",
        "Europe/Paris",
        "Europe/Berlin",
        "Europe/Moscow",
        "Asia/Tokyo",
        "Asia/Shanghai",
        "Asia/Singapore",
        "Asia/Dubai",
        "Asia/Kolkata",
        "Australia/Sydney",
        "Pacific/Auckland",
    ]

    def __init__(self) -> None:
        """Initialize the timezone plugin."""
        metadata = PluginMetadata(
            name="timezone",
            version="1.0.0",
            description="Timezone conversion, DST info, and world clock",
            author="LiuHao AI OS Team",
            categories=["time", "utility", "world"],
            keywords=["timezone", "time", "dst", "utc", "conversion", "clock"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._default_timezone: str = "UTC"
        self._default_format: str = "%Y-%m-%d %H:%M:%S"

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the timezone plugin."""
        try:
            if config:
                self._default_timezone = config.get("default_timezone", "UTC")
                self._default_format = config.get("default_format", "%Y-%m-%d %H:%M:%S")

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the timezone plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the timezone plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the timezone plugin."""
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
            "get_current_time",
            "list_timezones",
            "get_utc_offset",
            "get_dst_info",
            "lookup_abbreviation",
            "parse_with_timezone",
            "format_time",
            "time_diff",
            "common_timezones",
            "world_clock",
        ]

    # ==================== Timezone Conversion ====================

    def convert(
        self,
        time_str: str,
        from_tz: str,
        to_tz: str,
        *,
        time_format: Optional[str] = None,
    ) -> ConversionResult:
        """Convert time from one timezone to another.

        Args:
            time_str: Time string to convert.
            from_tz: Source timezone name.
            to_tz: Target timezone name.
            time_format: Format of the input time string.

        Returns:
            ConversionResult with converted time.
        """
        self._ensure_active()
        self._increment_call_count()

        fmt = time_format or self._default_format

        # Parse the input time
        dt = datetime.strptime(time_str, fmt)

        # Get offsets
        from_info = self._get_timezone_info(from_tz, dt)
        to_info = self._get_timezone_info(to_tz, dt)

        # Calculate difference
        offset_diff = to_info.offset_hours - from_info.offset_hours

        # Convert
        target_dt = dt + timedelta(hours=offset_diff)

        return ConversionResult(
            source_timezone=from_tz,
            target_timezone=to_tz,
            source_time=dt.strftime(fmt),
            target_time=target_dt.strftime(fmt),
            time_difference_hours=round(offset_diff, 2),
            source_offset=from_info.offset_hours,
            target_offset=to_info.offset_hours,
        )

    def get_current_time(
        self,
        timezone_name: Optional[str] = None,
        *,
        time_format: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get the current time in a specific timezone.

        Args:
            timezone_name: Timezone name. Uses default if None.
            time_format: Output time format.

        Returns:
            Dict with time info.
        """
        self._ensure_active()
        self._increment_call_count()

        tz = timezone_name or self._default_timezone
        fmt = time_format or self._default_format

        # Get current UTC time
        now_utc = datetime.utcnow()

        # Get timezone info
        info = self._get_timezone_info(tz, now_utc)

        # Apply offset
        local_time = now_utc + timedelta(hours=info.offset_hours)

        return {
            "timezone": tz,
            "time": local_time.strftime(fmt),
            "date": local_time.strftime("%Y-%m-%d"),
            "time_only": local_time.strftime("%H:%M:%S"),
            "offset_hours": info.offset_hours,
            "offset_str": info.utc_offset_str,
            "abbreviation": info.abbreviation,
            "is_dst": info.is_dst,
            "utc_time": now_utc.strftime(fmt),
            "unix_timestamp": int(local_time.timestamp()),
        }

    def world_clock(
        self,
        timezone_names: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Get current time for multiple timezones.

        Args:
            timezone_names: List of timezone names. Uses common ones if None.

        Returns:
            List of time info dicts.
        """
        self._ensure_active()
        self._increment_call_count()

        tz_list = timezone_names or self.COMMON_TIMEZONES
        results = []

        for tz in tz_list:
            try:
                info = self.get_current_time(tz)
                results.append(info)
            except Exception:
                continue

        return results

    # ==================== Timezone Info ====================

    def get_utc_offset(
        self,
        timezone_name: str,
        date_str: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get UTC offset for a timezone.

        Args:
            timezone_name: Timezone name.
            date_str: Date for DST calculation. Current date if None.

        Returns:
            Dict with offset info.
        """
        self._ensure_active()
        self._increment_call_count()

        if date_str:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        else:
            dt = datetime.utcnow()

        info = self._get_timezone_info(timezone_name, dt)

        return {
            "timezone": timezone_name,
            "offset_hours": info.offset_hours,
            "offset_minutes": info.offset_minutes,
            "offset_str": info.utc_offset_str,
            "abbreviation": info.abbreviation,
            "is_dst": info.is_dst,
        }

    def get_dst_info(self, timezone_name: str, year: Optional[int] = None) -> Dict[str, Any]:
        """Get DST information for a timezone.

        Args:
            timezone_name: Timezone name.
            year: Year to check. Current year if None.

        Returns:
            Dict with DST info.
        """
        self._ensure_active()
        self._increment_call_count()

        if year is None:
            year = datetime.utcnow().year

        tz_data = self.TIMEZONES.get(timezone_name)
        if not tz_data:
            raise ValueError(f"Unknown timezone: {timezone_name}")

        has_dst = tz_data.get("has_dst", False)
        std_abbr = tz_data.get("std_abbr", "")
        dst_abbr = tz_data.get("dst_abbr", "")

        # Simplified DST dates (US/EU pattern - approximate)
        dst_start = None
        dst_end = None

        if has_dst:
            # Northern hemisphere: DST roughly March-November
            # Southern hemisphere: DST roughly September-April
            offset = tz_data.get("offset", 0)
            if offset > -2:  # Northern hemisphere (rough approximation)
                dst_start = f"{year}-03-10"  # Second Sunday in March (approx)
                dst_end = f"{year}-11-03"  # First Sunday in November (approx)
            else:  # Southern hemisphere
                dst_start = f"{year}-10-01"
                dst_end = f"{year + 1}-04-01"

        return {
            "timezone": timezone_name,
            "has_dst": has_dst,
            "standard_abbreviation": std_abbr,
            "dst_abbreviation": dst_abbr,
            "dst_start": dst_start,
            "dst_end": dst_end,
            "standard_offset": tz_data.get("offset", 0),
            "dst_offset": tz_data.get("offset", 0) + (1 if has_dst else 0),
        }

    # ==================== Lookup & Search ====================

    def list_timezones(
        self,
        *,
        region: Optional[str] = None,
        search: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List available timezones.

        Args:
            region: Filter by region (America, Europe, Asia, etc.).
            search: Search string to filter by name.

        Returns:
            List of timezone info dicts.
        """
        self._ensure_active()
        self._increment_call_count()

        results = []
        now = datetime.utcnow()

        for name, data in self.TIMEZONES.items():
            # Region filter
            if region and "/" in name:
                tz_region = name.split("/")[0]
                if tz_region.lower() != region.lower():
                    continue

            # Search filter
            if search:
                if search.lower() not in name.lower():
                    continue

            info = self._get_timezone_info(name, now)
            results.append({
                "name": name,
                "offset_hours": info.offset_hours,
                "offset_str": info.utc_offset_str,
                "abbreviation": info.abbreviation,
                "has_dst": data.get("has_dst", False),
                "current_time": self.get_current_time(name)["time"],
            })

        # Sort by offset
        results.sort(key=lambda x: x["offset_hours"])
        return results

    def lookup_abbreviation(self, abbreviation: str) -> List[Dict[str, Any]]:
        """Look up timezones by abbreviation.

        Args:
            abbreviation: Timezone abbreviation (e.g., EST, CET).

        Returns:
            List of matching timezone info dicts.
        """
        self._ensure_active()
        self._increment_call_count()

        abbr = abbreviation.upper()
        results = []

        if abbr in self.ABBREVIATION_MAP:
            for tz_name in self.ABBREVIATION_MAP[abbr]:
                info = self.get_utc_offset(tz_name)
                results.append(info)
        else:
            # Search all timezones for matching abbreviation
            for name, data in self.TIMEZONES.items():
                if data.get("std_abbr", "").upper() == abbr or \
                   data.get("dst_abbr", "").upper() == abbr:
                    info = self.get_utc_offset(name)
                    results.append(info)

        return results

    # ==================== Parsing & Formatting ====================

    def parse_with_timezone(
        self,
        time_str: str,
        timezone_name: Optional[str] = None,
        *,
        time_format: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Parse a datetime string with timezone context.

        Args:
            time_str: Time string to parse.
            timezone_name: Timezone of the input time.
            time_format: Format string.

        Returns:
            Dict with parsed time info.
        """
        self._ensure_active()
        self._increment_call_count()

        tz = timezone_name or self._default_timezone
        fmt = time_format or self._default_format

        dt = datetime.strptime(time_str, fmt)
        info = self._get_timezone_info(tz, dt)

        # Calculate UTC time
        utc_dt = dt - timedelta(hours=info.offset_hours)

        return {
            "local_time": dt.strftime(fmt),
            "timezone": tz,
            "offset_hours": info.offset_hours,
            "utc_time": utc_dt.strftime(fmt),
            "unix_timestamp": int(utc_dt.timestamp()),
            "year": dt.year,
            "month": dt.month,
            "day": dt.day,
            "hour": dt.hour,
            "minute": dt.minute,
            "second": dt.second,
            "weekday": dt.strftime("%A"),
            "day_of_year": dt.timetuple().tm_yday,
            "is_dst": info.is_dst,
        }

    def format_time(
        self,
        time_str: str,
        timezone_name: Optional[str] = None,
        *,
        output_format: str = "%Y-%m-%d %H:%M:%S",
        input_format: Optional[str] = None,
    ) -> str:
        """Format a time string in a specific timezone.

        Args:
            time_str: Input time string.
            timezone_name: Target timezone.
            output_format: Desired output format.
            input_format: Input format.

        Returns:
            Formatted time string.
        """
        self._ensure_active()
        self._increment_call_count()

        fmt = input_format or self._default_format
        dt = datetime.strptime(time_str, fmt)
        return dt.strftime(output_format)

    # ==================== Time Difference ====================

    def time_diff(
        self,
        timezone1: str,
        timezone2: str,
        date_str: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Calculate time difference between two timezones.

        Args:
            timezone1: First timezone.
            timezone2: Second timezone.
            date_str: Date for DST calculation.

        Returns:
            Dict with time difference info.
        """
        self._ensure_active()
        self._increment_call_count()

        if date_str:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        else:
            dt = datetime.utcnow()

        info1 = self._get_timezone_info(timezone1, dt)
        info2 = self._get_timezone_info(timezone2, dt)

        diff_hours = info2.offset_hours - info1.offset_hours
        diff_minutes = int(abs(diff_hours * 60) % 60)
        hours = int(abs(diff_hours))

        return {
            "timezone1": timezone1,
            "timezone2": timezone2,
            "difference_hours": round(diff_hours, 2),
            "difference_text": f"{hours}h {diff_minutes}m " + ("ahead" if diff_hours > 0 else "behind"),
            "offset1": info1.offset_hours,
            "offset2": info2.offset_hours,
            "ahead": diff_hours > 0,
            "same_time": diff_hours == 0,
        }

    # ==================== Common Timezones ====================

    def common_timezones(self) -> List[Dict[str, Any]]:
        """Get a list of commonly used timezones with current time.

        Returns:
            List of common timezone info dicts.
        """
        self._ensure_active()
        self._increment_call_count()
        return self.world_clock(self.COMMON_TIMEZONES)

    # ==================== Internal Helpers ====================

    def _get_timezone_info(self, timezone_name: str, dt: datetime) -> TimezoneInfo:
        """Get timezone info for a specific datetime."""
        if timezone_name not in self.TIMEZONES:
            raise ValueError(f"Unknown timezone: {timezone_name}")

        tz_data = self.TIMEZONES[timezone_name]
        base_offset = tz_data.get("offset", 0)
        has_dst = tz_data.get("has_dst", False)
        std_abbr = tz_data.get("std_abbr", "")
        dst_abbr = tz_data.get("dst_abbr", "")

        # Simplified DST check (northern hemisphere: March-November, southern: September-April)
        is_dst = False
        if has_dst:
            month = dt.month
            if base_offset > -2:  # Northern hemisphere
                is_dst = 3 <= month <= 10
            else:  # Southern hemisphere
                is_dst = month >= 10 or month <= 3

        offset_hours = base_offset + (1 if is_dst else 0)
        offset_minutes_total = int(offset_hours * 60)
        abs_minutes = abs(offset_minutes_total)
        hours = abs_minutes // 60
        minutes = abs_minutes % 60
        sign = "+" if offset_hours >= 0 else "-"
        offset_str = f"UTC{sign}{hours:02d}:{minutes:02d}"

        abbr = dst_abbr if is_dst else std_abbr

        return TimezoneInfo(
            name=timezone_name,
            offset_hours=offset_hours,
            offset_minutes=offset_minutes_total,
            abbreviation=abbr,
            is_dst=is_dst,
            utc_offset_str=offset_str,
        )

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            "default_timezone": self._default_timezone,
            "total_timezones": len(self.TIMEZONES),
            "common_timezones": len(self.COMMON_TIMEZONES),
            "abbreviations": len(self.ABBREVIATION_MAP),
            "regions": sorted(set(name.split("/")[0] for name in self.TIMEZONES if "/" in name)),
        }
