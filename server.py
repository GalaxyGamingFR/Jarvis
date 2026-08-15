"""Jarvis backend: FastAPI + WebSocket chat, Gemini tool-calling loop, ElevenLabs TTS."""
import asyncio
import base64
import json
import os
import time
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
    # For secrets, fall back to the env var whenever the config.json value is empty/missing — not just
    # missing. A config.json with an explicit "" placeholder (e.g. copied from config.example.json)
    # would otherwise permanently shadow a real value set via .env, since setdefault() only fires when
    # the key is entirely absent.
    config["gemini_api_key"] = config.get("gemini_api_key") or os.getenv("GEMINI_API_KEY", "")
    config["elevenlabs_api_key"] = config.get("elevenlabs_api_key") or os.getenv("ELEVENLABS_API_KEY", "")
    config["elevenlabs_voice_id"] = config.get("elevenlabs_voice_id") or os.getenv("ELEVENLABS_VOICE_ID", "")
    config.setdefault("gemini_model", "gemini-3-flash-preview")
    config.setdefault("user_name", "sir")
    config.setdefault("default_location", "New York")
    config.setdefault("wake_greeting", "Welcome back, {user_name}. Systems online.")
    config.setdefault("apps", {})
    config.setdefault("server_host", "127.0.0.1")
    config.setdefault("server_port", 8420)
    return config


config = load_config()

if not config["gemini_api_key"]:
    raise RuntimeError(
        "No Gemini API key found. Copy config.example.json to config.json and set "
        "gemini_api_key, or put GEMINI_API_KEY in a .env file. Get a free key at "
        "https://aistudio.google.com/apikey"
    )

def load_gemini_keys() -> list[str]:
    """Primary key from config/GEMINI_API_KEY, plus any GEMINI_API_KEY_2, _3, ... found in the
    environment — lets multiple free-tier keys/accounts be rotated across to stretch the combined
    rate limit further."""
    keys = [config["gemini_api_key"]]
    i = 2
    while True:
        key = os.getenv(f"GEMINI_API_KEY_{i}", "")
        if not key:
            break
        keys.append(key)
        i += 1
    return keys


GEMINI_API_KEYS = load_gemini_keys()
GEMINI_CLIENTS = [genai.Client(api_key=k) for k in GEMINI_API_KEYS]
print(f"[server] Loaded {len(GEMINI_CLIENTS)} Gemini API key(s).", flush=True)

_next_client_index = 0


def assign_client_index() -> int:
    """Round-robins which key a new session starts on, so fresh conversations spread across keys."""
    global _next_client_index
    idx = _next_client_index
    _next_client_index = (_next_client_index + 1) % len(GEMINI_CLIENTS)
    return idx


def is_rate_limit_error(e: Exception) -> bool:
    text = f"{type(e).__name__} {e}".lower()
    return "429" in text or "quota" in text or ("rate" in text and "limit" in text)

SYSTEM_PROMPT = f"""You are Jarvis, {config['user_name']}'s personal AI assistant, in the spirit of \
Tony Stark's AI: dry, witty, imperturbable, and always a step ahead — but genuinely useful, not just \
sarcastic for its own sake. Address the user as "{config['user_name']}" occasionally, not every line.

Keep responses SHORT and conversational — this is a spoken voice interface, not a chat window. \
One to three sentences unless the user asks for detail. No markdown, no bullet points, no headers — \
just natural spoken language.

You have tools to search the web, open and read pages, view the user's screen, launch apps, check \
the weather, check the time/date, and manage a to-do list (list/add/complete tasks). Use them \
proactively when they'd help — don't ask permission first, just do it and report back concisely."""

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


def _run_with_client(client: genai.Client, user_input, previous_interaction_id, on_status) -> tuple[str, str]:
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


def run_agent_turn(
    user_input: str, previous_interaction_id: str | None, on_status, client_index: int
) -> tuple[str, str, int]:
    """Runs the Gemini tool-calling loop, falling back to other API keys if the current one is
    rate-limited. Returns (text, new_interaction_id, client_index_actually_used).

    Conversation state (previous_interaction_id) is scoped to whichever key created it, so a fallback
    to a different key has to start a fresh interaction rather than continue the old one — an
    acceptable tradeoff for a personal assistant vs. failing outright.
    """
    last_error: Exception | None = None
    for attempt in range(len(GEMINI_CLIENTS)):
        idx = (client_index + attempt) % len(GEMINI_CLIENTS)
        pid = previous_interaction_id if attempt == 0 else None
        try:
            text, new_id = _run_with_client(GEMINI_CLIENTS[idx], user_input, pid, on_status)
            return text, new_id, idx
        except Exception as e:
            last_error = e
            if is_rate_limit_error(e) and attempt < len(GEMINI_CLIENTS) - 1:
                on_status("Switching to a backup key...")
                continue
            raise
    raise last_error


WAKE_PROMPT = (
    "(System: the user just woke you up — by voice, clap, or manually. Greet them briefly — "
    "mention the time and/or weather if it's natural to, and check their to-do list, mentioning "
    "anything pending. Keep it tight, 2-3 sentences even with tasks included.)"
)

# Connections currently viewing the page, so a clap/wake-word can nudge an already-open tab instead
# of always opening a new one (see /trigger-wake).
active_connections: list[dict] = []


SESSION_ACTIVE_MAX_AGE_S = 45  # auto-expire a stale "active" flag rather than blocking wake forever


def is_connection_active(connection: dict) -> bool:
    """Whether this connection currently considers itself mid-conversation (awake and either listening
    or speaking) — used to stop the wake listener from re-triggering on Jarvis's own voice coming back
    through the mic (a classic smart-speaker feedback problem).

    Self-expires: a tab that goes away without cleanly sending session_state=false (a crashed page, a
    stuck/zombied browser context, a network drop the server hasn't noticed yet) would otherwise leave
    session_active stuck true forever, silently blocking every future wake attempt — a much worse
    failure mode than the spam this was built to prevent.
    """
    return (
        connection["session_active"]
        and (time.monotonic() - connection["session_active_since"]) < SESSION_ACTIVE_MAX_AGE_S
    )


def any_session_active() -> bool:
    return any(is_connection_active(c) for c in active_connections)


@app.get("/session-active")
def session_active_endpoint():
    return {"active": any_session_active()}


def mark_session_active(connection: dict, active: bool) -> None:
    connection["session_active"] = active
    connection["session_active_since"] = time.monotonic()


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    last_interaction_id: str | None = None
    client_index = assign_client_index()
    loop = asyncio.get_event_loop()

    async def handle_turn(user_input: str):
        nonlocal last_interaction_id, client_index

        def on_status(text: str):
            asyncio.run_coroutine_threadsafe(
                websocket.send_json({"type": "status", "text": text}), loop
            )

        try:
            final_text, new_id, used_index = await loop.run_in_executor(
                None, run_agent_turn, user_input, last_interaction_id, on_status, client_index
            )
            last_interaction_id = new_id
            client_index = used_index
        except Exception as e:
            # Most commonly every key being rate-limited at once, or a transient network error —
            # fail gracefully instead of dropping the whole WebSocket connection.
            print(f"[server] run_agent_turn failed: {e}", flush=True)
            final_text = "Sorry — I'm having trouble reaching my brain right now. Give it a moment and try again."

        audio_b64 = synthesize_speech(final_text)
        await websocket.send_json({"type": "assistant_message", "text": final_text, "audio_b64": audio_b64})

    async def external_wake():
        """Triggered by /trigger-wake when clap_trigger.py finds this tab already open."""
        mark_session_active(connection, True)
        await websocket.send_json({"type": "wake_push"})
        await handle_turn(WAKE_PROMPT)

    connection = {"websocket": websocket, "external_wake": external_wake, "session_active": False,
                  "session_active_since": 0.0}
    active_connections.append(connection)

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)

            if msg["type"] == "wake":
                mark_session_active(connection, True)
                await handle_turn(WAKE_PROMPT)
            elif msg["type"] == "user_message":
                mark_session_active(connection, True)  # refresh — an ongoing conversation stays "fresh"
                await handle_turn(msg["text"])
            elif msg["type"] == "session_state":
                mark_session_active(connection, bool(msg.get("active")))
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
        if is_connection_active(connection):
            continue  # already mid-conversation — don't layer a duplicate greeting on top
        # asyncio only holds a weak reference to tasks — without keeping one ourselves, the task
        # can get garbage-collected mid-run and silently never finish.
        task = asyncio.create_task(connection["external_wake"]())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    return {"woke": len(active_connections)}


if __name__ == "__main__":
    uvicorn.run(app, host=config["server_host"], port=config["server_port"])
