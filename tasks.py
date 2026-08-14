"""A simple local to-do list Jarvis can read from and write to."""
import json
from datetime import datetime
from pathlib import Path

TASKS_PATH = Path(__file__).parent / "tasks.json"

LIST_TASKS_SCHEMA = {
    "name": "list_tasks",
    "description": "List the user's pending to-do items. Use this proactively on wake/greetings and whenever tasks/to-dos come up.",
    "input_schema": {"type": "object", "properties": {}},
}

ADD_TASK_SCHEMA = {
    "name": "add_task",
    "description": "Add a new item to the user's to-do list.",
    "input_schema": {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "The task to add."}},
        "required": ["text"],
    },
}

COMPLETE_TASK_SCHEMA = {
    "name": "complete_task",
    "description": "Mark a to-do item as done. Match it by its text (doesn't need to be exact — closest match wins).",
    "input_schema": {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "Text identifying which task to complete."}},
        "required": ["text"],
    },
}


def _load() -> list[dict]:
    if not TASKS_PATH.exists():
        return []
    return json.loads(TASKS_PATH.read_text())


def _save(tasks: list[dict]) -> None:
    TASKS_PATH.write_text(json.dumps(tasks, indent=2))


def list_tasks() -> str:
    tasks = [t for t in _load() if not t["done"]]
    if not tasks:
        return "No pending tasks."
    return "\n".join(f"- {t['text']}" for t in tasks)


def add_task(text: str) -> str:
    tasks = _load()
    tasks.append({"text": text, "done": False, "created": datetime.now().isoformat()})
    _save(tasks)
    return f"Added: {text}"


def complete_task(text: str) -> str:
    tasks = _load()
    pending = [t for t in tasks if not t["done"]]
    if not pending:
        return "There's nothing on the list to complete."

    match = next((t for t in pending if text.lower() in t["text"].lower()), None)
    if not match:
        return f"Couldn't find a pending task matching '{text}'."

    match["done"] = True
    _save(tasks)
    return f"Marked done: {match['text']}"
