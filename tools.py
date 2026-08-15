"""Aggregates all tool schemas and dispatches tool_use calls to their implementations."""
from datetime import datetime

import requests

import app_launcher
import browser_tools
import clipboard_tools
import email_calendar
import file_search
import macros
import memory
import obsidian_notes
import screen_capture
import smart_home
import system_control
import tasks
import timers

_WEATHER_CODES = {
    0: "clear sky", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "foggy", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 71: "light snow", 73: "snow",
    75: "heavy snow", 80: "rain showers", 81: "rain showers", 82: "violent rain showers",
    95: "thunderstorms", 96: "thunderstorms with hail", 99: "thunderstorms with hail",
}

WEATHER_SCHEMA = {
    "name": "get_weather",
    "description": "Get the current weather for a location. Defaults to the user's configured home location if none is given.",
    "input_schema": {
        "type": "object",
        "properties": {
            "location": {"type": "string", "description": "City name. Optional — defaults to the user's home location."}
        },
    },
}

DATETIME_SCHEMA = {
    "name": "get_current_datetime",
    "description": "Get the current local date and time.",
    "input_schema": {"type": "object", "properties": {}},
}


def get_weather(location: str | None, default_location: str) -> str:
    location = location or default_location
    try:
        geo = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": location, "count": 1},
            timeout=8,
        ).json()
        results = geo.get("results")
        if not results:
            return f"Couldn't find a location called '{location}'."
        lat, lon = results[0]["latitude"], results[0]["longitude"]
        name = results[0]["name"]

        forecast = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,weather_code,wind_speed_10m",
            },
            timeout=8,
        ).json()
        current = forecast["current"]
        condition = _WEATHER_CODES.get(current["weather_code"], "unknown conditions")
        return (
            f"{name}: {current['temperature_2m']}°C, {condition}, "
            f"wind {current['wind_speed_10m']} km/h."
        )
    except (requests.RequestException, KeyError, IndexError) as e:
        return f"Couldn't fetch weather: {e}"


def get_current_datetime() -> str:
    return datetime.now().strftime("%A, %B %d, %Y at %I:%M %p")


def get_tool_schemas() -> list[dict]:
    return [
        *browser_tools.TOOL_SCHEMAS,
        screen_capture.TOOL_SCHEMA,
        app_launcher.TOOL_SCHEMA,
        WEATHER_SCHEMA,
        DATETIME_SCHEMA,
        tasks.LIST_TASKS_SCHEMA,
        tasks.ADD_TASK_SCHEMA,
        tasks.COMPLETE_TASK_SCHEMA,
        memory.REMEMBER_SCHEMA,
        memory.FORGET_SCHEMA,
        memory.LIST_MEMORIES_SCHEMA,
        system_control.MEDIA_CONTROL_SCHEMA,
        system_control.SET_VOLUME_SCHEMA,
        system_control.GET_NOW_PLAYING_SCHEMA,
        system_control.SYSTEM_POWER_SCHEMA,
        timers.SET_TIMER_SCHEMA,
        timers.SET_REMINDER_SCHEMA,
        timers.LIST_TIMERS_SCHEMA,
        timers.CANCEL_TIMER_SCHEMA,
        file_search.SEARCH_FILES_SCHEMA,
        file_search.READ_FILE_SCHEMA,
        email_calendar.LIST_UNREAD_EMAILS_SCHEMA,
        email_calendar.SEARCH_EMAILS_SCHEMA,
        email_calendar.SEND_EMAIL_SCHEMA,
        email_calendar.GET_TODAYS_EVENTS_SCHEMA,
        email_calendar.GET_UPCOMING_EVENTS_SCHEMA,
        smart_home.LIST_DEVICES_SCHEMA,
        smart_home.CONTROL_DEVICE_SCHEMA,
        smart_home.GET_DEVICE_STATE_SCHEMA,
        clipboard_tools.GET_CLIPBOARD_SCHEMA,
        clipboard_tools.SET_CLIPBOARD_SCHEMA,
        obsidian_notes.SEARCH_NOTES_SCHEMA,
        obsidian_notes.READ_NOTE_SCHEMA,
        obsidian_notes.LIST_RECENT_NOTES_SCHEMA,
        macros.RUN_MACRO_SCHEMA,
        macros.LIST_MACROS_SCHEMA,
    ]


_DISPATCH = {
    "web_search": lambda i, c: browser_tools.web_search(i["query"]),
    "open_url": lambda i, c: browser_tools.open_url(i["url"]),
    "launch_app": lambda i, c: app_launcher.launch_app(i["app"], c.get("apps", {})),
    "get_weather": lambda i, c: get_weather(i.get("location"), c.get("default_location", "")),
    "get_current_datetime": lambda i, c: get_current_datetime(),
    "list_tasks": lambda i, c: tasks.list_tasks(),
    "add_task": lambda i, c: tasks.add_task(i["text"]),
    "complete_task": lambda i, c: tasks.complete_task(i["text"]),
    "remember": lambda i, c: memory.remember(i["text"]),
    "forget": lambda i, c: memory.forget(i["text"]),
    "list_memories": lambda i, c: memory.list_memories(),
    "media_control": lambda i, c: system_control.media_control(i["action"]),
    "set_volume": lambda i, c: system_control.set_volume(i["action"], i.get("level")),
    "get_now_playing": lambda i, c: system_control.get_now_playing(),
    "system_power": lambda i, c: system_control.system_power(i["action"]),
    "set_timer": lambda i, c: timers.set_timer(i["duration_text"], i.get("label")),
    "set_reminder": lambda i, c: timers.set_reminder(i["when_text"], i["text"]),
    "list_timers": lambda i, c: timers.list_timers(),
    "cancel_timer": lambda i, c: timers.cancel_timer(i["id_or_text"]),
    "search_files": lambda i, c: file_search.search_files(i["query"], i.get("path"), i.get("content", False)),
    "read_file": lambda i, c: file_search.read_file(i["path"]),
    "list_unread_emails": lambda i, c: email_calendar.list_unread_emails(c, i.get("limit", 10)),
    "search_emails": lambda i, c: email_calendar.search_emails(c, i["query"], i.get("limit", 10)),
    "send_email": lambda i, c: email_calendar.send_email(c, i["to"], i["subject"], i["body"]),
    "get_todays_events": lambda i, c: email_calendar.get_todays_events(c),
    "get_upcoming_events": lambda i, c: email_calendar.get_upcoming_events(c, i.get("days", 7)),
    "list_devices": lambda i, c: smart_home.list_devices(c),
    "control_device": lambda i, c: smart_home.control_device(i["entity_id"], i["action"], i.get("value"), c),
    "get_device_state": lambda i, c: smart_home.get_device_state(i["entity_id_or_name"], c),
    "get_clipboard": lambda i, c: clipboard_tools.get_clipboard(),
    "set_clipboard": lambda i, c: clipboard_tools.set_clipboard(i["text"]),
    "search_notes": lambda i, c: obsidian_notes.search_notes(i["query"], c),
    "read_note": lambda i, c: obsidian_notes.read_note(i["title_or_path"], c),
    "list_recent_notes": lambda i, c: obsidian_notes.list_recent_notes(c, i.get("limit", 10)),
    "run_macro": lambda i, c: macros.run_macro(i["name"], c),
    "list_macros": lambda i, c: macros.list_macros(c),
}


def dispatch_tool(name: str, tool_input: dict, config: dict) -> list[dict]:
    """Runs a tool call and returns Anthropic tool_result content blocks.

    Wrapped in a try/except around every dispatched call: individual tool functions are written to
    never raise, but with this many tool modules there's no guarantee every edge case is covered, and
    an uncaught exception here would otherwise propagate all the way up through the Gemini tool loop
    and degrade the *entire* conversational turn to a generic "having trouble" fallback — losing
    whatever else happened in that turn — instead of just this one tool call failing gracefully.
    """
    if name == "view_screen":
        b64 = screen_capture.capture_screenshot_b64()
        return [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
            {"type": "text", "text": "Screenshot captured."},
        ]

    handler = _DISPATCH.get(name)
    if handler is None:
        return [{"type": "text", "text": f"Unknown tool: {name}"}]

    try:
        result = handler(tool_input, config)
    except Exception as e:
        result = f"That didn't work — {e}"
    return [{"type": "text", "text": result}]
