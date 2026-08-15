"""Persistent facts Jarvis remembers about the user across sessions/restarts.

Unlike tasks.py, these aren't retrieved on demand by the model deciding to call a tool — they're
injected straight into the system prompt every turn (see build_system_prompt() in server.py), so
Jarvis doesn't need to remember to ask itself what it knows. remember/forget/list_memories still
exist as tools because *storing* a new fact is an explicit action.
"""
import json
from datetime import datetime
from pathlib import Path

MEMORY_PATH = Path(__file__).parent / "memory.json"

REMEMBER_SCHEMA = {
    "name": "remember",
    "description": (
        "Store a durable fact about the user for future conversations — preferences, people in "
        "their life, ongoing projects, habits, anything worth recalling later. Use this proactively "
        "whenever the user states something durable about themselves, not only when they explicitly "
        "say 'remember this'."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "The fact to remember, written plainly."}},
        "required": ["text"],
    },
}

FORGET_SCHEMA = {
    "name": "forget",
    "description": "Delete a previously remembered fact. Match it by its text (doesn't need to be exact — closest match wins).",
    "input_schema": {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "Text identifying which fact to forget."}},
        "required": ["text"],
    },
}

LIST_MEMORIES_SCHEMA = {
    "name": "list_memories",
    "description": "List everything currently remembered about the user. Use when asked things like 'what do you know about me?'.",
    "input_schema": {"type": "object", "properties": {}},
}


def _load() -> list[dict]:
    if not MEMORY_PATH.exists():
        return []
    try:
        return json.loads(MEMORY_PATH.read_text())
    except json.JSONDecodeError:
        # load_facts() runs on every conversation turn (not just when a memory tool is called), so a
        # corrupted file here — e.g. a truncated write from a killed process — must not permanently
        # break every future turn. Fail open to an empty list instead.
        return []


def _save(facts: list[dict]) -> None:
    # Write to a temp file and rename over the original so a crash mid-write can't leave truncated,
    # unparseable JSON behind for the next _load() to choke on.
    tmp_path = MEMORY_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(facts, indent=2))
    tmp_path.replace(MEMORY_PATH)


def load_facts() -> list[str]:
    """Plain list of fact strings, for injection into the system prompt."""
    return [f["text"] for f in _load()]


def remember(text: str) -> str:
    facts = _load()
    facts.append({"text": text, "created": datetime.now().isoformat()})
    _save(facts)
    return f"Got it, I'll remember: {text}"


def forget(text: str) -> str:
    facts = _load()
    if not facts:
        return "There's nothing remembered to forget."

    match = next((f for f in facts if text.lower() in f["text"].lower()), None)
    if not match:
        return f"Couldn't find a remembered fact matching '{text}'."

    facts.remove(match)
    _save(facts)
    return f"Forgotten: {match['text']}"


def list_memories() -> str:
    facts = load_facts()
    if not facts:
        return "I don't have anything remembered about you yet."
    return "\n".join(f"- {f}" for f in facts)
