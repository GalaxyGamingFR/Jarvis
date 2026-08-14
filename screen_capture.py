"""Screen capture so Jarvis can 'see' what's on the user's screen."""
import base64
import io

from PIL import ImageGrab

TOOL_SCHEMA = {
    "name": "view_screen",
    "description": "Take a screenshot of the user's screen right now so you can see what they're looking at. Use when the user asks 'what's on my screen', asks you to look at something, or references something visible without describing it.",
    "input_schema": {"type": "object", "properties": {}},
}


def capture_screenshot_b64() -> str:
    img = ImageGrab.grab()
    img.thumbnail((1280, 1280))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")
