"""Jarvis backend: FastAPI + WebSocket chat, Gemini tool-calling loop, ElevenLabs TTS."""
import asyncio
import base64
import json
import os
from pathlib import Path

import requests
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google import genai

import tools

load_dotenv()

CONFIG_PATH = Path(__file__).parent / "config.json"

MAX_TOOL_ROUNDS = 8  # safety cap so a confused tool loop can't run forever


def load_config() -> dict:
    config = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
    config.setdefault("gemini_api_key", os.getenv("GEMINI_API_KEY", ""))
    config.setdefault("gemini_model", "gemini-3-flash-preview")
    config.setdefault("elevenlabs_api_key", os.getenv("ELEVENLABS_API_KEY", ""))
    config.setdefault("elevenlabs_voice_id", os.getenv("ELEVENLABS_VOICE_ID", ""))
    config.setdefault("user_name", "sir")
    config.setdefault("default_location", "New York")
    config.setdefault("wake_greeting", "Welcome back, {user_name}. Systems online.")
    config.setdefault("apps", {})
    config.setdefault("server_host", "127.0.0.1")
    config.setdefault("server_port", 8420)
    if not config["gemini_api_key"]:
        config["gemini_api_key"] = os.getenv("GEMINI_API_KEY", "")
    return config


config = load_config()

if not config["gemini_api_key"]:
    raise RuntimeError(
        "No Gemini API key found. Copy config.example.json to config.json and set "
        "gemini_api_key, or put GEMINI_API_KEY in a .env file. Get a free key at "
        "https://aistudio.google.com/apikey"
    )

client = genai.Client(api_key=config["gemini_api_key"])

SYSTEM_PROMPT = f"""You are Jarvis, {config['user_name']}'s personal AI assistant, in the spirit of \
Tony Stark's AI: dry, witty, imperturbable, and always a step ahead — but genuinely useful, not just \
sarcastic for its own sake. Address the user as "{config['user_name']}" occasionally, not every line.

Keep responses SHORT and conversational — this is a spoken voice interface, not a chat window. \
One to three sentences unless the user asks for detail. No markdown, no bullet points, no headers — \
just natural spoken language.

You have tools to search the web, open and read pages, view the user's screen, launch apps, check \
the weather, and check the time/date. Use them proactively when they'd help — don't ask permission \
first, just do it and report back concisely."""

app = FastAPI()
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "frontend")), name="static")


@app.get("/")
def index():
    return FileResponse(str(Path(__file__).parent / "frontend" / "index.html"))


@app.get("/status")
def status():
    """Cheap, non-LLM data for the HUD's corner readouts (no Gemini call)."""
    return {
        "datetime": tools.get_current_datetime(),
        "weather": tools.get_weather(None, config.get("default_location", "")),
        "user_name": config.get("user_name", "sir"),
        "location": config.get("default_location", ""),
    }


def synthesize_speech(text: str) -> str | None:
    """Returns base64-encoded MP3 via ElevenLabs, or None if not configured (client falls back to browser TTS)."""
    if not config.get("elevenlabs_api_key") or not config.get("elevenlabs_voice_id"):
        return None
    try:
        resp = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{config['elevenlabs_voice_id']}",
            headers={"xi-api-key": config["elevenlabs_api_key"], "Content-Type": "application/json"},
            json={
                "text": text,
                "model_id": "eleven_turbo_v2_5",
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
            },
            timeout=20,
        )
        resp.raise_for_status()
        return base64.b64encode(resp.content).decode("ascii")
    except requests.RequestException:
        return None


def _to_gemini_tool(schema: dict) -> dict:
    return {
        "type": "function",
        "name": schema["name"],
        "description": schema["description"],
        "parameters": schema["input_schema"],
    }


def _to_gemini_result_parts(content_blocks: list[dict]) -> list[dict]:
    parts = []
    for block in content_blocks:
        if block["type"] == "text":
            parts.append({"type": "text", "text": block["text"]})
        elif block["type"] == "image":
            parts.append(
                {
                    "type": "image",
                    "mime_type": block["source"]["media_type"],
                    "data": block["source"]["data"],
                }
            )
    return parts


GEMINI_TOOLS = [_to_gemini_tool(s) for s in tools.get_tool_schemas()]


def run_agent_turn(user_input: str, previous_interaction_id: str | None, on_status) -> tuple[str, str]:
    """Runs the Gemini tool-calling loop until a final text reply. Returns (text, new_interaction_id)."""
    interaction = client.interactions.create(
        model=config["gemini_model"],
        system_instruction=SYSTEM_PROMPT,
        input=user_input,
        tools=GEMINI_TOOLS,
        previous_interaction_id=previous_interaction_id,
    )

    rounds = 0
    while interaction.status == "requires_action" and rounds < MAX_TOOL_ROUNDS:
        rounds += 1
        results_input = []
        for step in interaction.steps:
            if step.type != "function_call":
                continue
            on_status(f"Using {step.name}...")
            content_blocks = tools.dispatch_tool(step.name, step.arguments, config)
            results_input.append(
                {
                    "type": "function_result",
                    "name": step.name,
                    "call_id": step.id,
                    "result": _to_gemini_result_parts(content_blocks),
                }
            )
        interaction = client.interactions.create(
            model=config["gemini_model"],
            previous_interaction_id=interaction.id,
            input=results_input,
            tools=GEMINI_TOOLS,
        )

    text = interaction.output_text or "Sorry, I got stuck on that one."
    return text, interaction.id


WAKE_PROMPT = (
    "(System: the user just clapped to wake you up. Greet them briefly — "
    "mention the time and/or weather if it's natural to. 1-2 sentences.)"
)

# Connections currently viewing the page, so a clap can nudge an already-open tab instead of
# always opening a new one (see /trigger-wake).
active_connections: list[dict] = []


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    last_interaction_id: str | None = None
    loop = asyncio.get_event_loop()

    async def handle_turn(user_input: str):
        nonlocal last_interaction_id

        def on_status(text: str):
            asyncio.run_coroutine_threadsafe(
                websocket.send_json({"type": "status", "text": text}), loop
            )

        final_text, new_id = await loop.run_in_executor(
            None, run_agent_turn, user_input, last_interaction_id, on_status
        )
        last_interaction_id = new_id
        audio_b64 = synthesize_speech(final_text)
        await websocket.send_json({"type": "assistant_message", "text": final_text, "audio_b64": audio_b64})

    async def external_wake():
        """Triggered by /trigger-wake when clap_trigger.py finds this tab already open."""
        await websocket.send_json({"type": "wake_push"})
        await handle_turn(WAKE_PROMPT)

    connection = {"websocket": websocket, "external_wake": external_wake}
    active_connections.append(connection)

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)

            if msg["type"] == "wake":
                await handle_turn(WAKE_PROMPT)
            elif msg["type"] == "user_message":
                await handle_turn(msg["text"])
    except WebSocketDisconnect:
        pass
    finally:
        active_connections.remove(connection)


_background_tasks: set[asyncio.Task] = set()


@app.post("/trigger-wake")
async def trigger_wake():
    """clap_trigger.py calls this when it found and refocused an already-open Jarvis window,
    so that tab starts listening without needing a full page reload."""
    for connection in list(active_connections):
        # asyncio only holds a weak reference to tasks — without keeping one ourselves, the task
        # can get garbage-collected mid-run and silently never finish.
        task = asyncio.create_task(connection["external_wake"]())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    return {"woke": len(active_connections)}


if __name__ == "__main__":
    uvicorn.run(app, host=config["server_host"], port=config["server_port"])
