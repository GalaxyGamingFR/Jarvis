"""Aggregates all tool schemas and dispatches tool_use calls to their implementations."""
from datetime import datetime

import requests

import app_launcher
import browser_tools
import screen_capture

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
                "temperature_unit": "fahrenheit",
            },
            timeout=8,
        ).json()
        current = forecast["current"]
        condition = _WEATHER_CODES.get(current["weather_code"], "unknown conditions")
        return (
            f"{name}: {current['temperature_2m']}°F, {condition}, "
            f"wind {current['wind_speed_10m']} mph."
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
    ]


def dispatch_tool(name: str, tool_input: dict, config: dict) -> list[dict]:
    """Runs a tool call and returns Anthropic tool_result content blocks."""
    if name == "web_search":
        return [{"type": "text", "text": browser_tools.web_search(tool_input["query"])}]
    if name == "open_url":
        return [{"type": "text", "text": browser_tools.open_url(tool_input["url"])}]
    if name == "view_screen":
        b64 = screen_capture.capture_screenshot_b64()
        return [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
            {"type": "text", "text": "Screenshot captured."},
        ]
    if name == "launch_app":
        return [{"type": "text", "text": app_launcher.launch_app(tool_input["app"], config.get("apps", {}))}]
    if name == "get_weather":
        return [{"type": "text", "text": get_weather(tool_input.get("location"), config.get("default_location", ""))}]
    if name == "get_current_datetime":
        return [{"type": "text", "text": get_current_datetime()}]
    return [{"type": "text", "text": f"Unknown tool: {name}"}]
