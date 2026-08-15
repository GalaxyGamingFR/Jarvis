"""Timers ("set a timer for 10 minutes") and reminders ("remind me at 3pm") for Jarvis.

Integration note for server.py (not wired up here — this module only exposes the hook):
    get_due_items() is a plain function, NOT a tool schema, meant to be polled rather than called
    by the model. The intended wiring is a background asyncio task started once at app startup
    (e.g. `asyncio.create_task(due_items_poll_loop())` alongside the existing `_background_tasks`
    pattern), looping `while True: await asyncio.sleep(5-10); items = timers.get_due_items(); ...`.
    For each string in `items`, push it to every live tab the way `/trigger-wake`'s
    `external_wake()` does: iterate `active_connections`, skip any where `is_connection_active()`
    is True (already mid-conversation — queue or defer instead of talking over the user), else
    send a websocket message (e.g. `{"type": "timer_fired", "text": item}`) and speak it through
    the existing `synthesize_speech()` -> `assistant_message` flow, similar to `external_wake()`.

Storage: timers.json, a flat list of dicts:
    {"id": str, "kind": "timer"|"reminder", "text": str, "due": isoformat, "created": isoformat}
`id` is a short uuid4 hex (not text-matched by default) since timer/reminder text is often generic
or absent ("set a timer for 5 minutes" has nothing distinctive to substring-match later).

Parsing scope (deliberately limited, no new pip dependency):
  set_timer duration_text — sums up `(\\d+)\\s*(hour|hr|minute|min|second|sec)s?` chunks, e.g.
      "10 minutes", "1 hour 30 minutes", "90 seconds", "2 hrs 15 mins". Bare numbers with no unit
      ("set a timer for 10") are rejected with a clear error rather than guessed at.
  set_reminder when_text — supports:
      - relative: "in 20 minutes", "in 2 hours" (same duration grammar as set_timer)
      - absolute clock time: "3pm", "3:30pm", "9am", "15:00" (24-hour also accepted, with or
        without a colon, e.g. "15:00" or "1500")
      - optional day qualifier: "tomorrow at 9am", "today at 6pm"
      - a bare hour 1-12 with no am/pm is ambiguous and rejected (e.g. "remind me at 3" fails;
        "remind me at 3pm" or "remind me at 15:00" both work)
      - if the resulting time has already passed today and no explicit day was given, it rolls
        forward to tomorrow (a reminder is always for the future)
    NOT supported: weekdays ("next Tuesday"), relative dates ("in 3 days"), date-of-month ("on the
    5th"). These fail with a clear "couldn't understand" message rather than misfiring.
"""
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
import json

TIMERS_PATH = Path(__file__).parent / "timers.json"

SET_TIMER_SCHEMA = {
    "name": "set_timer",
    "description": (
        "Set a countdown timer for a relative duration (e.g. 'in 10 minutes', '1 hour 30 minutes', "
        "'90 seconds'). Use for short-lived, duration-based alerts like cooking or work breaks. "
        "For a specific clock time (e.g. '3pm', 'tomorrow at 9am'), use set_reminder instead."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "duration_text": {
                "type": "string",
                "description": "The duration, in natural language, e.g. '10 minutes', '1 hour 30 minutes', '90 seconds'.",
            },
            "label": {
                "type": "string",
                "description": "Optional short label for what the timer is for, e.g. 'pasta'. Omit for a plain duration timer.",
            },
        },
        "required": ["duration_text"],
    },
}

SET_REMINDER_SCHEMA = {
    "name": "set_reminder",
    "description": (
        "Set a reminder for a specific clock time (e.g. '3pm', 'tomorrow at 9am', 'in 2 hours'). "
        "Use for a one-off alert at a particular moment, as opposed to a plain countdown — "
        "use set_timer for those instead."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "when_text": {
                "type": "string",
                "description": "When to remind, in natural language, e.g. '3pm', 'tomorrow at 9am', 'in 20 minutes'.",
            },
            "text": {
                "type": "string",
                "description": "What to remind the user about, e.g. 'call the dentist'.",
            },
        },
        "required": ["when_text", "text"],
    },
}

LIST_TIMERS_SCHEMA = {
    "name": "list_timers",
    "description": "List all pending timers and reminders, with time remaining / due time.",
    "input_schema": {"type": "object", "properties": {}},
}

CANCEL_TIMER_SCHEMA = {
    "name": "cancel_timer",
    "description": "Cancel a pending timer or reminder. Match it by its id, or by its label/text (doesn't need to be exact — closest match wins).",
    "input_schema": {
        "type": "object",
        "properties": {
            "id_or_text": {
                "type": "string",
                "description": "The timer/reminder id, or text identifying which one to cancel.",
            }
        },
        "required": ["id_or_text"],
    },
}


_UNIT_MAP = {
    "hour": "hour", "hours": "hour", "hr": "hour", "hrs": "hour",
    "minute": "minute", "minutes": "minute", "min": "minute", "mins": "minute",
    "second": "second", "seconds": "second", "sec": "second", "secs": "second",
}
_DURATION_RE = re.compile(r"(\d+)\s*(hours?|hrs?|minutes?|mins?|seconds?|secs?)\b", re.IGNORECASE)


def _parse_duration(text: str) -> tuple[timedelta, str] | None:
    """Parses a natural-language duration like '1 hour 30 minutes' into (timedelta, human label).
    Returns None if no recognizable duration chunks were found."""
    matches = _DURATION_RE.findall(text or "")
    if not matches:
        return None
    total = timedelta()
    parts = []
    for num, raw_unit in matches:
        n = int(num)
        unit = _UNIT_MAP[raw_unit.lower()]
        if unit == "hour":
            total += timedelta(hours=n)
        elif unit == "minute":
            total += timedelta(minutes=n)
        else:
            total += timedelta(seconds=n)
        parts.append(f"{n} {unit}{'s' if n != 1 else ''}")
    return total, " ".join(parts)


_TIME_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", re.IGNORECASE)


def _parse_when(text: str, now: datetime | None = None) -> datetime | None:
    """Parses a natural-language time reference into an absolute datetime. See module docstring
    for exactly what phrasings are supported. Returns None if it couldn't be understood."""
    now = now or datetime.now()
    lower = (text or "").lower().strip()
    if not lower:
        return None

    # Relative: "in 20 minutes" / "in 2 hours" / bare "20 minutes" with no clock-time markers.
    has_clock_markers = bool(re.search(r"\d{1,2}:\d{2}", lower)) or bool(re.search(r"\b(am|pm)\b", lower))
    dur = _parse_duration(lower)
    if dur and not has_clock_markers:
        return now + dur[0]

    # Absolute clock time, optionally with a day qualifier.
    day_offset = 0
    if "tomorrow" in lower:
        day_offset = 1
        lower = lower.replace("tomorrow", "")
    elif "today" in lower:
        lower = lower.replace("today", "")
    lower = lower.replace(" at ", " ").strip()

    m = _TIME_RE.search(lower)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    ampm = m.group(3)

    if ampm:
        ampm = ampm.lower()
        if ampm == "pm" and hour != 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
    elif hour >= 13 or hour == 0:
        pass  # already 24-hour, e.g. "15:00" or "1500"-style hour
    else:
        return None  # ambiguous bare hour 1-12 with no am/pm — reject rather than guess

    if hour > 23 or minute > 59:
        return None

    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if day_offset == 1:
        candidate += timedelta(days=1)
    elif candidate <= now:
        candidate += timedelta(days=1)  # already passed today and no explicit day given — assume tomorrow
    return candidate


def _load() -> list[dict]:
    if not TIMERS_PATH.exists():
        return []
    try:
        return json.loads(TIMERS_PATH.read_text())
    except json.JSONDecodeError:
        return []


def _save(items: list[dict]) -> None:
    tmp_path = TIMERS_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(items, indent=2))
    tmp_path.replace(TIMERS_PATH)


def _format_remaining(due: datetime, now: datetime | None = None) -> str:
    now = now or datetime.now()
    delta = due - now
    if delta.total_seconds() <= 0:
        return "any moment now"
    total_seconds = int(delta.total_seconds())
    hours, rem = divmod(total_seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if not parts and seconds:
        parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")
    return ", ".join(parts) if parts else "under a minute"


def set_timer(duration_text: str, label: str | None = None) -> str:
    parsed = _parse_duration(duration_text)
    if not parsed:
        return (
            f"Couldn't understand '{duration_text}' as a duration. Try something like "
            "'10 minutes', '1 hour 30 minutes', or '90 seconds'."
        )
    delta, human_duration = parsed
    now = datetime.now()
    due = now + delta
    text = label.strip() if label and label.strip() else human_duration

    items = _load()
    items.append({
        "id": uuid.uuid4().hex[:8],
        "kind": "timer",
        "text": text,
        "due": due.isoformat(),
        "created": now.isoformat(),
    })
    _save(items)
    return f"Timer set: {text} — due at {due.strftime('%I:%M %p').lstrip('0')}."


def set_reminder(when_text: str, text: str) -> str:
    due = _parse_when(when_text)
    if not due:
        return (
            f"Couldn't understand '{when_text}' as a time. Try something like '3pm', "
            "'tomorrow at 9am', '15:00', or 'in 20 minutes'."
        )
    now = datetime.now()
    items = _load()
    items.append({
        "id": uuid.uuid4().hex[:8],
        "kind": "reminder",
        "text": text,
        "due": due.isoformat(),
        "created": now.isoformat(),
    })
    _save(items)
    when_str = due.strftime("%A %I:%M %p").lstrip("0") if due.date() != now.date() else due.strftime("%I:%M %p").lstrip("0")
    return f"Reminder set: {text} — {when_str}."


def list_timers() -> str:
    items = _load()
    if not items:
        return "No pending timers or reminders."
    now = datetime.now()
    lines = []
    for item in sorted(items, key=lambda i: i["due"]):
        due = datetime.fromisoformat(item["due"])
        if item["kind"] == "timer":
            lines.append(f"- [{item['id']}] Timer: {item['text']} — {_format_remaining(due, now)} left")
        else:
            when_str = due.strftime("%A %I:%M %p").lstrip("0") if due.date() != now.date() else due.strftime("%I:%M %p").lstrip("0")
            lines.append(f"- [{item['id']}] Reminder: {item['text']} — {when_str} ({_format_remaining(due, now)} left)")
    return "\n".join(lines)


def cancel_timer(id_or_text: str) -> str:
    items = _load()
    if not items:
        return "There's nothing pending to cancel."

    needle = id_or_text.strip()
    match = next((i for i in items if i["id"] == needle), None)
    if not match:
        match = next((i for i in items if needle.lower() in i["text"].lower()), None)
    if not match:
        return f"Couldn't find a pending timer or reminder matching '{id_or_text}'."

    items.remove(match)
    _save(items)
    kind = "Timer" if match["kind"] == "timer" else "Reminder"
    return f"Cancelled {kind.lower()}: {match['text']}"


def get_due_items() -> list[str]:
    """Polled by server.py (not model-invoked). Returns human-readable, TTS-ready strings for any
    timer/reminder whose due time has passed, and removes them from storage so they only fire once."""
    items = _load()
    now = datetime.now()
    due_items = [i for i in items if datetime.fromisoformat(i["due"]) <= now]
    if not due_items:
        return []

    remaining = [i for i in items if i not in due_items]
    _save(remaining)

    messages = []
    for item in due_items:
        if item["kind"] == "timer":
            messages.append(f"Your {item['text']} timer is up.")
        else:
            messages.append(f"Reminder: {item['text']}.")
    return messages
