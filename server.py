"""Jarvis backend: FastAPI + WebSocket chat, Claude tool-calling loop, ElevenLabs TTS."""
import asyncio
import base64
import json
import os
from pathlib import Path

import anthropic
import requests
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import tools

load_dotenv()

CONFIG_PATH = Path(__file__).parent / "config.json"


def load_config() -> dict:
    config = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
    config.setdefault("anthropic_api_key", os.getenv("ANTHROPIC_API_KEY", ""))
    config.setdefault("anthropic_model", "claude-sonnet-5")
    config.setdefault("elevenlabs_api_key", os.getenv("ELEVENLABS_API_KEY", ""))
    config.setdefault("elevenlabs_voice_id", os.getenv("ELEVENLABS_VOICE_ID", ""))
    config.setdefault("user_name", "sir")
    config.setdefault("default_location", "New York")
    config.setdefault("wake_greeting", "Welcome back, {user_name}. Systems online.")
    config.setdefault("apps", {})
    config.setdefault("server_host", "127.0.0.1")
    config.setdefault("server_port", 8420)
    if not config["anthropic_api_key"]:
        config["anthropic_api_key"] = os.getenv("ANTHROPIC_API_KEY", "")
    return config


config = load_config()

if not config["anthropic_api_key"]:
    raise RuntimeError(
        "No Anthropic API key found. Copy config.example.json to config.json and set "
        "anthropic_api_key, or put ANTHROPIC_API_KEY in a .env file."
    )

client = anthropic.Anthropic(api_key=config["anthropic_api_key"])

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


def run_agent_turn(history: list, on_status) -> str:
    """Runs the Claude tool-calling loop until a final text reply. Mutates history in place."""
    tool_schemas = tools.get_tool_schemas()
    while True:
        response = client.messages.create(
            model=config["anthropic_model"],
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=tool_schemas,
            messages=history,
        )
        history.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            return "".join(block.text for block in response.content if block.type == "text")

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            on_status(f"Using {block.name}...")
            result_content = tools.dispatch_tool(block.name, block.input, config)
            tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result_content})
        history.append({"role": "user", "content": tool_results})


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    history: list = []
    loop = asyncio.get_event_loop()

    async def handle_turn():
        def on_status(text: str):
            asyncio.run_coroutine_threadsafe(
                websocket.send_json({"type": "status", "text": text}), loop
            )

        final_text = await loop.run_in_executor(None, run_agent_turn, history, on_status)
        audio_b64 = synthesize_speech(final_text)
        await websocket.send_json({"type": "assistant_message", "text": final_text, "audio_b64": audio_b64})

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)

            if msg["type"] == "wake":
                history.append(
                    {
                        "role": "user",
                        "content": (
                            "(System: the user just clapped to wake you up. Greet them briefly — "
                            "mention the time and/or weather if it's natural to. 1-2 sentences.)"
                        ),
                    }
                )
                await handle_turn()
            elif msg["type"] == "user_message":
                history.append({"role": "user", "content": msg["text"]})
                await handle_turn()
    except WebSocketDisconnect:
        pass


if __name__ == "__main__":
    uvicorn.run(app, host=config["server_host"], port=config["server_port"])
