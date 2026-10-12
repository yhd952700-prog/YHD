"""
Calendar Management Plugin for LiuHao AI OS

Provides event creation and management, recurring events,
free/busy checking, conflict detection, and reminder management.

Capabilities:
- create_event: Create a new calendar event
- update_event: Update an existing event
- delete_event: Delete an event
- get_events: List events in a date range
- check_conflicts: Check for scheduling conflicts
- check_free_busy: Check free/busy status
- add_reminder: Add a reminder to an event
- list_reminders: List reminders for an event
"""

import uuid
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta, date

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class CalendarEvent:
    """A calendar event."""
    id: str
    title: str
    start_time: datetime
    end_time: datetime
    description: str = ""
    location: str = ""
    organizer: str = ""
    attendees: List[str] = field(default_factory=list)
    is_recurring: bool = False
    recurrence_rule: str = ""  # RRULE format
    categories: List[str] = field(default_factory=list)
    status: str = "confirmed"  # confirmed, tentative, cancelled
    all_day: bool = False
    reminders: List[Dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass
class Reminder:
    """A reminder for an event."""
    id: str
    event_id: str
    minutes_before: int
    method: str = "popup"  # popup, email, sound
    message: str = ""
    is_triggered: bool = False


class CalendarPlugin(BasePlugin):
    """Calendar management plugin.

    Supports event CRUD, recurring events, free/busy checking,
    conflict detection, and reminder management.

    Example:
        plugin = CalendarPlugin()
        plugin.initialize()
        plugin.activate()

        event = plugin.create_event(
            title="Meeting",
            start=datetime(2024, 1, 15, 10, 0),
            end=datetime(2024, 1, 15, 11, 0),
        )
    """

    # Recurrence frequencies
    RECURRENCE_DAILY = "DAILY"
    RECURRENCE_WEEKLY = "WEEKLY"
    RECURRENCE_MONTHLY = "MONTHLY"
    RECURRENCE_YEARLY = "YEARLY"

    def __init__(self) -> None:
        """Initialize the calendar plugin."""
        metadata = PluginMetadata(
            name="calendar",
            version="1.0.0",
            description="Calendar management with events and reminders",
            author="LiuHao AI OS Team",
            categories=["productivity", "calendar", "scheduling"],
            keywords=["calendar", "event", "schedule", "reminder", "meeting"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._events: Dict[str, CalendarEvent] = {}
        self._reminders: Dict[str, Reminder] = {}

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the calendar plugin."""
        try:
            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the calendar plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the calendar plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the calendar plugin."""
        try:
            self._events.clear()
            self._reminders.clear()
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "create_event",
            "update_event",
            "delete_event",
            "get_event",
            "get_events",
            "check_conflicts",
            "check_free_busy",
            "add_reminder",
            "list_reminders",
            "remove_reminder",
            "get_upcoming_events",
        ]

    # ==================== Event Methods ====================

    def create_event(
        self,
        title: str,
        start_time: datetime,
        end_time: datetime,
        description: str = "",
        location: str = "",
        organizer: str = "",
        attendees: Optional[List[str]] = None,
        all_day: bool = False,
        recurrence: Optional[Dict[str, Any]] = None,
        categories: Optional[List[str]] = None,
    ) -> CalendarEvent:
        """Create a new calendar event.

        Args:
            title: Event title.
            start_time: Event start time.
            end_time: Event end time.
            description: Event description.
            location: Event location.
            organizer: Event organizer.
            attendees: List of attendee names/emails.
            all_day: Whether it's an all-day event.
            recurrence: Recurrence rule dict.
            categories: List of category tags.

        Returns:
            The created CalendarEvent.
        """
        self._ensure_active()
        self._increment_call_count()

        if start_time >= end_time:
            raise ValueError("End time must be after start time")

        event_id = str(uuid.uuid4())[:8]

        recurrence_rule = ""
        is_recurring = False
        if recurrence:
            is_recurring = True
            recurrence_rule = self._build_rrule(recurrence)

        event = CalendarEvent(
            id=event_id,
            title=title,
            start_time=start_time,
            end_time=end_time,
            description=description,
            location=location,
            organizer=organizer,
            attendees=attendees or [],
            is_recurring=is_recurring,
            recurrence_rule=recurrence_rule,
            categories=categories or [],
            all_day=all_day,
        )

        self._events[event_id] = event
        return event

    def update_event(
        self,
        event_id: str,
        **kwargs: Any,
    ) -> Optional[CalendarEvent]:
        """Update an existing event.

        Args:
            event_id: ID of the event to update.
            **kwargs: Fields to update (title, start_time, etc.).

        Returns:
            Updated CalendarEvent, or None if not found.
        """
        self._ensure_active()
        self._increment_call_count()

        event = self._events.get(event_id)
        if not event:
            return None

        for key, value in kwargs.items():
            if hasattr(event, key):
                setattr(event, key, value)

        event.updated_at = datetime.now()
        return event

    def delete_event(self, event_id: str) -> bool:
        """Delete an event.

        Args:
            event_id: ID of the event to delete.

        Returns:
            True if the event was found and deleted.
        """
        self._ensure_active()
        self._increment_call_count()

        if event_id in self._events:
            del self._events[event_id]
            # Also delete associated reminders
            to_delete = [rid for rid, r in self._reminders.items()
                        if r.event_id == event_id]
            for rid in to_delete:
                del self._reminders[rid]
            return True
        return False

    def get_event(self, event_id: str) -> Optional[CalendarEvent]:
        """Get an event by ID.

        Args:
            event_id: Event ID.

        Returns:
            CalendarEvent or None.
        """
        self._ensure_active()
        return self._events.get(event_id)

    def get_events(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        category: Optional[str] = None,
    ) -> List[CalendarEvent]:
        """Get events within a date range.

        Args:
            start_date: Start of date range.
            end_date: End of date range.
            category: Filter by category.

        Returns:
            List of CalendarEvent objects, sorted by start time.
        """
        self._ensure_active()
        self._increment_call_count()

        events = list(self._events.values())

        if start_date:
            events = [e for e in events if e.end_time >= start_date]

        if end_date:
            events = [e for e in events if e.start_time <= end_date]

        if category:
            events = [e for e in events if category in e.categories]

        events.sort(key=lambda e: e.start_time)
        return events

    def get_upcoming_events(self, limit: int = 10) -> List[CalendarEvent]:
        """Get upcoming events from now.

        Args:
            limit: Maximum number of events to return.

        Returns:
            List of upcoming CalendarEvent objects.
        """
        self._ensure_active()
        now = datetime.now()
        events = self.get_events(start_date=now)
        return events[:limit]

    # ==================== Conflict & Free/Busy ====================

    def check_conflicts(
        self,
        start_time: datetime,
        end_time: datetime,
        exclude_event_id: Optional[str] = None,
    ) -> List[CalendarEvent]:
        """Check for scheduling conflicts.

        Args:
            start_time: Proposed start time.
            end_time: Proposed end time.
            exclude_event_id: Event ID to exclude from check.

        Returns:
            List of conflicting CalendarEvent objects.
        """
        self._ensure_active()
        self._increment_call_count()

        conflicts: List[CalendarEvent] = []

        for event in self._events.values():
            if event.id == exclude_event_id:
                continue
            if event.status == "cancelled":
                continue

            # Check for overlap
            if start_time < event.end_time and end_time > event.start_time:
                conflicts.append(event)

        return conflicts

    def check_free_busy(
        self,
        start_date: datetime,
        end_date: datetime,
        time_slot_minutes: int = 30,
    ) -> List[Dict[str, Any]]:
        """Check free/busy status for a date range.

        Args:
            start_date: Start of period to check.
            end_date: End of period to check.
            time_slot_minutes: Granularity of time slots.

        Returns:
            List of time slot dicts with 'start', 'end', 'status'.
        """
        self._ensure_active()
        self._increment_call_count()

        slots: List[Dict[str, Any]] = []
        slot = timedelta(minutes=time_slot_minutes)
        current = start_date

        while current < end_date:
            slot_end = current + slot
            conflicts = self.check_conflicts(current, slot_end)

            status = "free"
            busy_events = []
            if conflicts:
                status = "busy"
                busy_events = [e.title for e in conflicts]

            slots.append({
                "start": current,
                "end": slot_end,
                "status": status,
                "events": busy_events,
            })

            current = slot_end

        return slots

    # ==================== Reminder Methods ====================

    def add_reminder(
        self,
        event_id: str,
        minutes_before: int,
        method: str = "popup",
        message: str = "",
    ) -> Optional[Reminder]:
        """Add a reminder to an event.

        Args:
            event_id: Event ID.
            minutes_before: Minutes before event to trigger.
            method: Reminder method (popup, email, sound).
            message: Custom reminder message.

        Returns:
            Created Reminder, or None if event not found.
        """
        self._ensure_active()
        self._increment_call_count()

        if event_id not in self._events:
            return None

        reminder_id = str(uuid.uuid4())[:8]
        reminder = Reminder(
            id=reminder_id,
            event_id=event_id,
            minutes_before=minutes_before,
            method=method,
            message=message,
        )

        self._reminders[reminder_id] = reminder

        event = self._events[event_id]
        event.reminders.append({
            "id": reminder_id,
            "minutes_before": minutes_before,
            "method": method,
        })

        return reminder

    def list_reminders(self, event_id: str) -> List[Reminder]:
        """List all reminders for an event.

        Args:
            event_id: Event ID.

        Returns:
            List of Reminder objects.
        """
        self._ensure_active()
        self._increment_call_count()

        return [
            r for r in self._reminders.values()
            if r.event_id == event_id
        ]

    def remove_reminder(self, reminder_id: str) -> bool:
        """Remove a reminder.

        Args:
            reminder_id: Reminder ID.

        Returns:
            True if reminder was found and removed.
        """
        self._ensure_active()
        self._increment_call_count()

        if reminder_id in self._reminders:
            reminder = self._reminders[reminder_id]
            event = self._events.get(reminder.event_id)
            if event:
                event.reminders = [
                    r for r in event.reminders
                    if r.get("id") != reminder_id
                ]
            del self._reminders[reminder_id]
            return True
        return False

    def get_due_reminders(self) -> List[Dict[str, Any]]:
        """Get reminders that are due to trigger soon.

        Returns:
            List of due reminder dicts with event info.
        """
        self._ensure_active()

        now = datetime.now()
        due: List[Dict[str, Any]] = []

        for reminder in self._reminders.values():
            if reminder.is_triggered:
                continue

            event = self._events.get(reminder.event_id)
            if not event or event.status == "cancelled":
                continue

            reminder_time = event.start_time - timedelta(minutes=reminder.minutes_before)

            if reminder_time <= now:
                due.append({
                    "reminder_id": reminder.id,
                    "event_id": event.id,
                    "event_title": event.title,
                    "event_start": event.start_time,
                    "minutes_before": reminder.minutes_before,
                    "method": reminder.method,
                    "message": reminder.message or f"Reminder: {event.title}",
                })

        return due

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get calendar statistics."""
        return {
            "total_events": len(self._events),
            "total_reminders": len(self._reminders),
            "upcoming_events": len(self.get_upcoming_events(limit=1000)),
            "recurring_events": sum(1 for e in self._events.values() if e.is_recurring),
        }

    # ==================== Internal Helpers ====================

    def _build_rrule(self, recurrence: Dict[str, Any]) -> str:
        """Build an RRULE string from a recurrence dict."""
        freq = recurrence.get("frequency", "DAILY").upper()
        parts = [f"FREQ={freq}"]

        if "interval" in recurrence:
            parts.append(f"INTERVAL={recurrence['interval']}")

        if "count" in recurrence:
            parts.append(f"COUNT={recurrence['count']}")

        if "until" in recurrence:
            until = recurrence["until"]
            if isinstance(until, datetime):
                parts.append(f"UNTIL={until.strftime('%Y%m%dT%H%M%S')}")

        if "byday" in recurrence and freq == "WEEKLY":
            parts.append(f"BYDAY={recurrence['byday']}")

        return ";".join(parts)
