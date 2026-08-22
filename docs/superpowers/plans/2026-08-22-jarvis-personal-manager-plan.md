# Jarvis Personal Manager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild Jarvis from scratch in `transfer\jarvis` as a domain-routed, voice-driven personal manager covering system control, dev-session orchestration, school scheduling, content drafting, and business/venture tracking — all on Gemini, with ElevenLabs TTS.

**Architecture:** FastAPI/WebSocket backend (ported skeleton) receives a transcript from the browser HUD's Web Speech API, runs it through a two-hop Gemini flow — a cheap router call classifies the utterance into one domain, then a domain-scoped tool-calling call executes — and speaks the result back via ElevenLabs. Structured data (facts, business ventures, notifications, clip jobs, dev sessions) lives in a new SQLite store, replacing the old flat `memory.json`.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, `google-genai` (Gemini `interactions` API), ElevenLabs REST API, openWakeWord, Web Speech API (browser-side STT/TTS fallback), sqlite3 (stdlib), pytest.

**Spec:** `docs/superpowers/specs/2026-08-22-jarvis-personal-manager-design.md`

## Global Constraints

- LLM backend is Gemini only for this build (no Claude API code) — the `llm/client.py` interface must make a future swap config-only, per the spec.
- ElevenLabs TTS uses the free-tier Jarvis premade voice; must degrade to a logged, text-only response (not an error) when the character quota is exhausted.
- Wake-word activation opens a continuous conversation session (not per-utterance wake) — this behavior already exists in the archived frontend and must be preserved unchanged.
- Every tool dispatch call must be wrapped so a single tool's exception degrades only that tool call, never the whole turn (carried forward from the old Jarvis's dispatch safety net).
- The `generate_clips` tool calls the *existing* `publikclip` pipeline (`transfer\websites\publikclip`, invoked as `uv run publikclip run <url>`) — do not build a new clip-generation method; that is an explicitly separate, future spec.
- No automated notification ingestion (email, contact-form) in this build — business notifications are voice-reported only.

---

## Context for the implementer

The prior Jarvis implementation is archived on the `jarvis-v1-archive` branch of `github.com/GalaxyGamingFR/Jarvis` (this repo). It is a working FastAPI/WebSocket app using the Gemini `google-genai` SDK's `client.interactions.create(...)` for tool-calling turns (with `previous_interaction_id` for multi-turn state), ElevenLabs for TTS with a browser `speechSynthesis` fallback, `openWakeWord` for offline "Hey Jarvis" detection, and the browser's `SpeechRecognition` API for STT. Several pieces of that implementation are reused verbatim in this plan (Task 4) because they already work correctly and don't need to change:

- `app_launcher.py`, `browser_tools.py`, `clipboard_tools.py`, `email_calendar.py`, `file_search.py`, `macros.py`, `obsidian_notes.py`, `proactive.py`, `screen_capture.py`, `smart_home.py`, `system_control.py`, `tasks.py`, `timers.py`, `window_focus.py` — each exports its own `TOOL_SCHEMA(S)` constant(s) and plain functions; see Task 5 for how they're aggregated.
- `clap_trigger.py`, `wake_word_trigger.py` — standalone processes that call `POST /trigger-wake` and `GET /session-active` on the FastAPI server; unchanged, they don't touch the tool-calling logic at all.
- `frontend/index.html`, `frontend/main.js`, `frontend/style.css` — the HUD; `main.js`'s `SpeechRecognition` handling *already* implements "stay open until idle timeout" conversational sessions, not per-utterance wake. The WebSocket message contract (`user_message` → `assistant_message`, `wake` → greeting, `status` updates) is unchanged, so no frontend edits are needed.
- `scripts/launch-session.ps1`, `assets/image.png`, `assets/image1.png` — launcher script and HUD images, unchanged.

What's new: the domain-routing layer (`llm/`), the domain modules for dev/school/content/business (`domains/`), and the SQLite-backed structured store (`memory_store.py`) replacing `memory.py`. `tools.py` and `server.py` are rewritten (Tasks 5 and 12) to use the new domain registry instead of one flat tool list.

Independent verification of two external integrations, done during spec research and safe to rely on:
- **SchoolPlan** (`transfer\SchoolPlan`, deployed at `school.tariqkhalif.me`) is a Cloudflare Worker. Auth: `POST /login` with form fields `username`/`password` sets an HttpOnly session cookie (cookie name defined in that repo's `src/index.js` as `COOKIE_NAME`). Data API: `GET /api/calendar` returns `{"blocks": [...]}`; `PUT /api/calendar` accepts `{"blocks": [...]}` and replaces the whole list (no partial-update endpoint). A block has the shape `{"id": str, "day": "mon"|"tue"|"wed"|"thu"|"fri"|"sat"|"sun", "start": "HH:MM", "end": "HH:MM", "label": str, "category": "study"|"filming"|"personal"|"coding", "done": bool, "course": str (optional, one of "MATH 150"/"CMPT 120"/"MACM 101"/"CMPT 105W")}`. Any request without a valid session cookie gets back the login page HTML (status 200, not JSON) regardless of path — detect this by JSON-parse failure, not status code.
- **publikclip** (`transfer\websites\publikclip`) is invoked as `uv run publikclip run <url>` from that directory; job output lands in `~/.publikclip/jobs/<job-id>/clips/`. It is slow (this is the exact problem the future fast-pipeline spec addresses), so it must be launched as a background subprocess, never awaited inline in a tool call.

---

### Task 1: Wipe the old implementation

**Files:**
- Delete (git rm): every tracked file except `docs/` and `.gitignore` — i.e. `README.md`, `SETUP.md`, `app_launcher.py`, `assets/image.png`, `assets/image1.png`, `browser_tools.py`, `clap_trigger.py`, `clipboard_tools.py`, `config.example.json`, `email_calendar.py`, `file_search.py`, `frontend/index.html`, `frontend/main.js`, `frontend/style.css`, `macros.py`, `memory.py`, `obsidian_notes.py`, `proactive.py`, `requirements.txt`, `screen_capture.py`, `scripts/launch-session.ps1`, `server.py`, `smart_home.py`, `system_control.py`, `tasks.py`, `timers.py`, `tools.py`, `wake_word_trigger.py`, `window_focus.py`
- Create: `requirements.txt`, `.gitignore` (append `jarvis.db`)

**Interfaces:**
- Produces: nothing consumed by later tasks except the clean working tree and the new `requirements.txt`.

- [ ] **Step 1: Confirm the archive branch has everything before deleting anything**

Run: `git log jarvis-v1-archive --oneline -1` and `git diff master jarvis-v1-archive --stat`
Expected: the archive branch exists and its diff against current `master` is empty (they're at the same commit) — confirming nothing will be lost.

- [ ] **Step 2: Wipe the tracked files**

```bash
git rm README.md SETUP.md app_launcher.py assets/image.png assets/image1.png \
  browser_tools.py clap_trigger.py clipboard_tools.py config.example.json \
  email_calendar.py file_search.py frontend/index.html frontend/main.js \
  frontend/style.css macros.py memory.py obsidian_notes.py proactive.py \
  requirements.txt screen_capture.py scripts/launch-session.ps1 server.py \
  smart_home.py system_control.py tasks.py timers.py tools.py \
  wake_word_trigger.py window_focus.py
```

- [ ] **Step 3: Add `jarvis.db` to `.gitignore`**

Append a line `jarvis.db` to `.gitignore` (the new SQLite store file, built in Task 2 — must never be committed, same reasoning as the old `memory.json`/`tasks.json`/`timers.json` entries already there).

- [ ] **Step 4: Write the new `requirements.txt`**

```
fastapi
uvicorn[standard]
google-genai
python-dotenv
playwright
pillow
sounddevice
numpy
requests
websockets
openwakeword
scipy
pycaw
winsdk
icalendar
pytest
```

(Identical to the archived `requirements.txt` plus `pytest` for the new test suite — no new runtime dependencies are needed anywhere in this plan.)

- [ ] **Step 5: Commit**

```bash
git add .gitignore requirements.txt
git commit -m "Wipe pre-rebuild Jarvis implementation (see jarvis-v1-archive)"
```

---

### Task 2: `memory_store.py` — SQLite structured store

**Files:**
- Create: `memory_store.py`
- Test: `tests/test_memory_store.py`

**Interfaces:**
- Produces: `init_db() -> None`; `REMEMBER_SCHEMA`, `FORGET_SCHEMA`, `LIST_MEMORIES_SCHEMA` (dicts, same shape as the old `memory.py`); `load_facts() -> list[str]`; `remember(text: str) -> str`; `forget(text: str) -> str`; `list_memories() -> str`; `create_venture(name: str, status: str = "idea", notes: str = "", next_steps: str = "") -> int`; `update_venture(name: str, status: str | None = None, notes: str | None = None, next_steps: str | None = None) -> bool`; `list_ventures() -> list[dict]`; `get_venture(name: str) -> dict | None`; `add_notification(source: str, summary: str) -> int`; `list_notifications(status: str | None = None) -> list[dict]`; `resolve_notification(id_or_text: str) -> bool`; `create_clip_job(source_url: str) -> int`; `update_clip_job(job_id: int, status: str | None = None, result_path: str | None = None, error: str | None = None, finished_at: str | None = None) -> None`; `list_recent_clip_jobs(limit: int = 5) -> list[dict]`; `create_dev_session(project: str, prompt: str, pid: int) -> int`; `update_dev_session(session_id: int, status: str | None = None, finished_at: str | None = None) -> None`; `list_dev_sessions(status: str | None = None) -> list[dict]`; module-level `DB_PATH: Path` (tests override this per-test via monkeypatch, see Step 1).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_memory_store.py
import importlib
import sqlite3

import pytest


@pytest.fixture
def store(tmp_path, monkeypatch):
    import memory_store
    monkeypatch.setattr(memory_store, "DB_PATH", tmp_path / "test.db")
    importlib.reload(memory_store)  # picks up the patched DB_PATH inside the module
    memory_store.DB_PATH = tmp_path / "test.db"
    memory_store.init_db()
    return memory_store


def test_remember_and_load_facts(store):
    store.remember("Tariq prefers dark mode")
    assert store.load_facts() == ["Tariq prefers dark mode"]


def test_forget_removes_matching_fact(store):
    store.remember("Tariq's favorite color is blue")
    result = store.forget("favorite color")
    assert "blue" in result
    assert store.load_facts() == []


def test_forget_no_match(store):
    result = store.forget("nonexistent")
    assert "Couldn't find" in result


def test_list_memories_empty(store):
    assert "don't have anything" in store.list_memories()


def test_create_and_list_ventures(store):
    store.create_venture("Acme Co", status="idea", notes="clip automation business")
    ventures = store.list_ventures()
    assert len(ventures) == 1
    assert ventures[0]["name"] == "Acme Co"
    assert ventures[0]["status"] == "idea"


def test_update_venture_partial_fields(store):
    store.create_venture("Acme Co")
    ok = store.update_venture("Acme Co", status="active", next_steps="Register domain")
    assert ok is True
    v = store.get_venture("Acme Co")
    assert v["status"] == "active"
    assert v["next_steps"] == "Register domain"
    assert v["notes"] == ""  # untouched field stays as-is


def test_update_venture_not_found(store):
    assert store.update_venture("Nope", status="active") is False


def test_notifications_add_list_resolve(store):
    store.add_notification("client email", "Acme wants a quote by Friday")
    open_notifs = store.list_notifications(status="new")
    assert len(open_notifs) == 1
    ok = store.resolve_notification("Acme wants a quote")
    assert ok is True
    assert store.list_notifications(status="new") == []


def test_clip_job_lifecycle(store):
    job_id = store.create_clip_job("https://youtube.com/watch?v=abc")
    jobs = store.list_recent_clip_jobs()
    assert jobs[0]["status"] == "queued"
    store.update_clip_job(job_id, status="done", result_path="/clips/abc")
    jobs = store.list_recent_clip_jobs()
    assert jobs[0]["status"] == "done"
    assert jobs[0]["result_path"] == "/clips/abc"


def test_dev_session_lifecycle(store):
    session_id = store.create_dev_session("websites", "fix the calendar bug", pid=1234)
    sessions = store.list_dev_sessions(status="running")
    assert len(sessions) == 1
    assert sessions[0]["pid"] == 1234
    store.update_dev_session(session_id, status="finished")
    assert store.list_dev_sessions(status="running") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_memory_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'memory_store'`

- [ ] **Step 3: Implement `memory_store.py`**

```python
"""Structured local storage for Jarvis: durable facts about the user (replaces the old
memory.json), business ventures, client/project notifications, clip-generation jobs, and
Claude Code dev sessions. One SQLite file, one connection helper, shared by every domain
module that needs to persist something.
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "jarvis.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ventures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'idea',
    notes TEXT NOT NULL DEFAULT '',
    next_steps TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    summary TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS clip_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    result_path TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS dev_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL,
    prompt TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    pid INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    finished_at TEXT
);
"""


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(SCHEMA)


# --- facts: same tool schemas/behavior as the old memory.py, SQLite-backed instead of JSON ---

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


def load_facts() -> list[str]:
    with _connect() as conn:
        rows = conn.execute("SELECT text FROM facts ORDER BY id").fetchall()
    return [r["text"] for r in rows]


def remember(text: str) -> str:
    with _connect() as conn:
        conn.execute("INSERT INTO facts (text, created_at) VALUES (?, ?)", (text, datetime.now().isoformat()))
    return f"Got it, I'll remember: {text}"


def forget(text: str) -> str:
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, text FROM facts WHERE lower(text) LIKE ?", (f"%{text.lower()}%",)
        ).fetchone()
        if not row:
            return f"Couldn't find a remembered fact matching '{text}'."
        conn.execute("DELETE FROM facts WHERE id = ?", (row["id"],))
        return f"Forgotten: {row['text']}"


def list_memories() -> str:
    facts = load_facts()
    if not facts:
        return "I don't have anything remembered about you yet."
    return "\n".join(f"- {f}" for f in facts)


# --- ventures (business domain) ---

def create_venture(name: str, status: str = "idea", notes: str = "", next_steps: str = "") -> int:
    now = datetime.now().isoformat()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO ventures (name, status, notes, next_steps, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (name, status, notes, next_steps, now, now),
        )
        return cur.lastrowid


def update_venture(
    name: str, status: str | None = None, notes: str | None = None, next_steps: str | None = None
) -> bool:
    with _connect() as conn:
        row = conn.execute("SELECT id FROM ventures WHERE name = ?", (name,)).fetchone()
        if not row:
            return False
        fields, values = [], []
        if status is not None:
            fields.append("status = ?")
            values.append(status)
        if notes is not None:
            fields.append("notes = ?")
            values.append(notes)
        if next_steps is not None:
            fields.append("next_steps = ?")
            values.append(next_steps)
        fields.append("updated_at = ?")
        values.append(datetime.now().isoformat())
        values.append(row["id"])
        conn.execute(f"UPDATE ventures SET {', '.join(fields)} WHERE id = ?", values)
        return True


def get_venture(name: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM ventures WHERE name = ?", (name,)).fetchone()
    return dict(row) if row else None


def list_ventures() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM ventures ORDER BY updated_at DESC").fetchall()
    return [dict(r) for r in rows]


# --- notifications (business domain) ---

def add_notification(source: str, summary: str) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO notifications (source, summary, status, created_at) VALUES (?, ?, 'new', ?)",
            (source, summary, datetime.now().isoformat()),
        )
        return cur.lastrowid


def list_notifications(status: str | None = None) -> list[dict]:
    with _connect() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM notifications WHERE status = ? ORDER BY created_at DESC", (status,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM notifications ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def resolve_notification(id_or_text: str) -> bool:
    with _connect() as conn:
        if id_or_text.isdigit():
            row = conn.execute("SELECT id FROM notifications WHERE id = ?", (int(id_or_text),)).fetchone()
        else:
            row = conn.execute(
                "SELECT id FROM notifications WHERE lower(summary) LIKE ?", (f"%{id_or_text.lower()}%",)
            ).fetchone()
        if not row:
            return False
        conn.execute("UPDATE notifications SET status = 'resolved' WHERE id = ?", (row["id"],))
        return True


# --- clip jobs (content domain) ---

def create_clip_job(source_url: str) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO clip_jobs (source_url, status, created_at) VALUES (?, 'queued', ?)",
            (source_url, datetime.now().isoformat()),
        )
        return cur.lastrowid


def update_clip_job(
    job_id: int,
    status: str | None = None,
    result_path: str | None = None,
    error: str | None = None,
    finished_at: str | None = None,
) -> None:
    with _connect() as conn:
        fields, values = [], []
        for col, val in (("status", status), ("result_path", result_path), ("error", error), ("finished_at", finished_at)):
            if val is not None:
                fields.append(f"{col} = ?")
                values.append(val)
        if not fields:
            return
        values.append(job_id)
        conn.execute(f"UPDATE clip_jobs SET {', '.join(fields)} WHERE id = ?", values)


def list_recent_clip_jobs(limit: int = 5) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM clip_jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# --- dev sessions (dev domain) ---

def create_dev_session(project: str, prompt: str, pid: int) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO dev_sessions (project, prompt, status, pid, created_at) VALUES (?, ?, 'running', ?, ?)",
            (project, prompt, pid, datetime.now().isoformat()),
        )
        return cur.lastrowid


def update_dev_session(session_id: int, status: str | None = None, finished_at: str | None = None) -> None:
    with _connect() as conn:
        fields, values = [], []
        if status is not None:
            fields.append("status = ?")
            values.append(status)
        if finished_at is not None:
            fields.append("finished_at = ?")
            values.append(finished_at)
        if not fields:
            return
        values.append(session_id)
        conn.execute(f"UPDATE dev_sessions SET {', '.join(fields)} WHERE id = ?", values)


def list_dev_sessions(status: str | None = None) -> list[dict]:
    with _connect() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM dev_sessions WHERE status = ? ORDER BY created_at DESC", (status,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM dev_sessions ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_memory_store.py -v`
Expected: PASS (12 tests)

- [ ] **Step 5: Commit**

```bash
git add memory_store.py tests/test_memory_store.py
git commit -m "Add SQLite structured store (facts, ventures, notifications, clip jobs, dev sessions)"
```

---

### Task 3: `domains/registry.py` — domain registration

**Files:**
- Create: `domains/__init__.py` (empty), `domains/registry.py`
- Test: `tests/test_registry.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Domain` dataclass with fields `name: str`, `system_prompt_section: str`, `tool_schemas: list[dict]`, `dispatch: dict[str, Callable]`; `register(domain: Domain) -> None`; `get_domain(name: str) -> Domain`; `all_domain_names() -> list[str]`. Every later domain module (`domains/system.py`, `domains/dev.py`, `domains/school.py`, `domains/content.py`, `domains/business.py`) calls `register(...)` at import time.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_registry.py
import pytest

from domains.registry import Domain, register, get_domain, all_domain_names


def test_register_and_get_domain():
    d = Domain(name="test_domain", system_prompt_section="Test tools.", tool_schemas=[], dispatch={})
    register(d)
    assert get_domain("test_domain") is d


def test_get_domain_unknown_falls_back_to_general():
    general = Domain(name="general", system_prompt_section="", tool_schemas=[], dispatch={})
    register(general)
    assert get_domain("not_a_real_domain") is general


def test_all_domain_names_lists_registered():
    register(Domain(name="alpha", system_prompt_section="", tool_schemas=[], dispatch={}))
    register(Domain(name="beta", system_prompt_section="", tool_schemas=[], dispatch={}))
    names = all_domain_names()
    assert "alpha" in names and "beta" in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'domains'`

- [ ] **Step 3: Implement `domains/registry.py`**

```python
"""Domain registry: maps a router-classified domain name (see llm/router.py) to its tool
schemas, dispatch table, and system-prompt section. Each domains/*.py module builds a
Domain and calls register() at import time; server.py imports every domain module once
at startup so this registry is fully populated before the first request.
"""
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Domain:
    name: str
    system_prompt_section: str
    tool_schemas: list[dict]
    dispatch: dict[str, Callable] = field(default_factory=dict)


_DOMAINS: dict[str, Domain] = {}


def register(domain: Domain) -> None:
    _DOMAINS[domain.name] = domain


def get_domain(name: str) -> Domain:
    return _DOMAINS.get(name, _DOMAINS["general"])


def all_domain_names() -> list[str]:
    return list(_DOMAINS.keys())
```

Create `domains/__init__.py` as an empty file (makes `domains` a package).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_registry.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add domains/__init__.py domains/registry.py tests/test_registry.py
git commit -m "Add domain registry"
```

---

### Task 4: Port unchanged modules from the archive

**Files:**
- Create (copied verbatim, no edits): `app_launcher.py`, `browser_tools.py`, `clap_trigger.py`, `clipboard_tools.py`, `email_calendar.py`, `file_search.py`, `macros.py`, `obsidian_notes.py`, `proactive.py`, `screen_capture.py`, `smart_home.py`, `system_control.py`, `tasks.py`, `timers.py`, `wake_word_trigger.py`, `window_focus.py`, `frontend/index.html`, `frontend/main.js`, `frontend/style.css`, `assets/image.png`, `assets/image1.png`, `scripts/launch-session.ps1`

**Interfaces:**
- Produces: each module's existing `TOOL_SCHEMA`/`TOOL_SCHEMAS` constants and plain functions, unchanged from the archived version (see the tools.py dispatch table on the `jarvis-v1-archive` branch for the exact names: `browser_tools.web_search`/`open_url`, `app_launcher.launch_app`, `screen_capture.capture_screenshot_b64`, `system_control.media_control`/`set_volume`/`get_now_playing`/`system_power`, `timers.set_timer`/`set_reminder`/`list_timers`/`cancel_timer`, `file_search.search_files`/`read_file`, `email_calendar.list_unread_emails`/`search_emails`/`send_email`/`get_todays_events`/`get_upcoming_events`, `smart_home.list_devices`/`control_device`/`get_device_state`, `clipboard_tools.get_clipboard`/`set_clipboard`, `obsidian_notes.search_notes`/`read_note`/`list_recent_notes`, `macros.run_macro`/`list_macros`, `tasks.list_tasks`/`add_task`/`complete_task`). Task 5 consumes these directly.

- [ ] **Step 1: Restore each file verbatim from the archive branch**

```bash
for f in app_launcher.py browser_tools.py clap_trigger.py clipboard_tools.py \
  email_calendar.py file_search.py macros.py obsidian_notes.py proactive.py \
  screen_capture.py smart_home.py system_control.py tasks.py timers.py \
  wake_word_trigger.py window_focus.py frontend/index.html frontend/main.js \
  frontend/style.css assets/image.png assets/image1.png scripts/launch-session.ps1; do
  mkdir -p "$(dirname "$f")"
  git show jarvis-v1-archive:"$f" > "$f"
done
```

(`assets/*.png` and other binary files come through `git show` correctly since it writes raw blob bytes to stdout.)

- [ ] **Step 2: Spot-check one file restored correctly**

Run: `diff <(git show jarvis-v1-archive:system_control.py) system_control.py`
Expected: no output (files identical)

- [ ] **Step 3: Commit**

```bash
git add app_launcher.py browser_tools.py clap_trigger.py clipboard_tools.py \
  email_calendar.py file_search.py macros.py obsidian_notes.py proactive.py \
  screen_capture.py smart_home.py system_control.py tasks.py timers.py \
  wake_word_trigger.py window_focus.py frontend/ assets/ scripts/
git commit -m "Port unchanged utility/voice/frontend modules from jarvis-v1-archive"
```

---

### Task 5: `domains/system.py` — system domain (ports old `tools.py`'s aggregation)

**Files:**
- Create: `domains/system.py`
- Test: `tests/test_system_domain.py`

**Interfaces:**
- Consumes: every module ported in Task 4, plus `memory_store.REMEMBER_SCHEMA`/`FORGET_SCHEMA`/`LIST_MEMORIES_SCHEMA`/`remember`/`forget`/`list_memories` (Task 2), `domains.registry.Domain`/`register` (Task 3).
- Produces: registers a `Domain(name="system", ...)` covering every utility tool the old Jarvis had (media/volume/power, app launching, weather, date/time, files, screenshots, macros, clipboard, web search, Obsidian notes, smart home, email/calendar, timers/reminders, to-do tasks, remembering/forgetting facts). Also exports `dispatch_tool(domain: Domain, name: str, tool_input: dict, config: dict) -> list[dict]`, the generic per-domain dispatcher every domain module (and `llm/client.py`) uses — this replaces the old `tools.dispatch_tool`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_system_domain.py
from domains.registry import get_domain
import domains.system  # noqa: F401 — import triggers registration


def test_system_domain_registered_with_tools():
    d = get_domain("system")
    assert d.name == "system"
    names = {s["name"] for s in d.tool_schemas}
    # spot-check a few from each ported module, not an exhaustive list
    assert {"get_weather", "get_current_datetime", "set_volume", "launch_app",
            "search_files", "remember", "forget", "run_macro"}.issubset(names)


def test_every_schema_has_a_dispatch_entry():
    d = get_domain("system")
    schema_names = {s["name"] for s in d.tool_schemas}
    # view_screen is special-cased in dispatch_tool() itself (returns an image block), not in the dict
    assert schema_names - {"view_screen"} <= set(d.dispatch.keys())


def test_dispatch_tool_wraps_plain_string_result():
    d = get_domain("system")
    blocks = domains.system.dispatch_tool(d, "get_current_datetime", {}, {})
    assert blocks == [{"type": "text", "text": d.dispatch["get_current_datetime"]({}, {})}]


def test_dispatch_tool_unknown_tool_name():
    d = get_domain("system")
    blocks = domains.system.dispatch_tool(d, "not_a_real_tool", {}, {})
    assert "Unknown tool" in blocks[0]["text"]


def test_dispatch_tool_catches_handler_exception():
    d = get_domain("system")
    d.dispatch["_boom"] = lambda i, c: (_ for _ in ()).throw(RuntimeError("kaboom"))
    d.tool_schemas.append({"name": "_boom", "description": "", "input_schema": {"type": "object", "properties": {}}})
    blocks = domains.system.dispatch_tool(d, "_boom", {}, {})
    assert "didn't work" in blocks[0]["text"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_system_domain.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'domains.system'`

- [ ] **Step 3: Implement `domains/system.py`**

```python
"""System domain: every general-utility tool the old Jarvis had — media/volume/power,
app launching, weather, date/time, files, screenshots, macros, clipboard, web search,
Obsidian notes, smart home, email/calendar, timers/reminders, to-do tasks, and
remembering/forgetting durable facts. This is a straight port of the old tools.py's
aggregation, scoped as one domain in the new router (see design spec's System control
section — "ported from the old Jarvis" covers its full tool surface, not just the
literal system-control subset).
"""
from datetime import datetime

import requests

import app_launcher
import browser_tools
import clipboard_tools
import email_calendar
import file_search
import macros
import memory_store
import obsidian_notes
import screen_capture
import smart_home
import system_control
import tasks
import timers
from domains.registry import Domain, register

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
        "properties": {"location": {"type": "string", "description": "City name. Optional — defaults to the user's home location."}},
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
            "https://geocoding-api.open-meteo.com/v1/search", params={"name": location, "count": 1}, timeout=8
        ).json()
        results = geo.get("results")
        if not results:
            return f"Couldn't find a location called '{location}'."
        lat, lon = results[0]["latitude"], results[0]["longitude"]
        name = results[0]["name"]
        forecast = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={"latitude": lat, "longitude": lon, "current": "temperature_2m,weather_code,wind_speed_10m"},
            timeout=8,
        ).json()
        current = forecast["current"]
        condition = _WEATHER_CODES.get(current["weather_code"], "unknown conditions")
        return f"{name}: {current['temperature_2m']}°C, {condition}, wind {current['wind_speed_10m']} km/h."
    except (requests.RequestException, KeyError, IndexError) as e:
        return f"Couldn't fetch weather: {e}"


def get_current_datetime() -> str:
    return datetime.now().strftime("%A, %B %d, %Y at %I:%M %p")


TOOL_SCHEMAS = [
    *browser_tools.TOOL_SCHEMAS,
    screen_capture.TOOL_SCHEMA,
    app_launcher.TOOL_SCHEMA,
    WEATHER_SCHEMA,
    DATETIME_SCHEMA,
    tasks.LIST_TASKS_SCHEMA,
    tasks.ADD_TASK_SCHEMA,
    tasks.COMPLETE_TASK_SCHEMA,
    memory_store.REMEMBER_SCHEMA,
    memory_store.FORGET_SCHEMA,
    memory_store.LIST_MEMORIES_SCHEMA,
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

DISPATCH = {
    "web_search": lambda i, c: browser_tools.web_search(i["query"]),
    "open_url": lambda i, c: browser_tools.open_url(i["url"]),
    "launch_app": lambda i, c: app_launcher.launch_app(i["app"], c.get("apps", {})),
    "get_weather": lambda i, c: get_weather(i.get("location"), c.get("default_location", "")),
    "get_current_datetime": lambda i, c: get_current_datetime(),
    "list_tasks": lambda i, c: tasks.list_tasks(),
    "add_task": lambda i, c: tasks.add_task(i["text"]),
    "complete_task": lambda i, c: tasks.complete_task(i["text"]),
    "remember": lambda i, c: memory_store.remember(i["text"]),
    "forget": lambda i, c: memory_store.forget(i["text"]),
    "list_memories": lambda i, c: memory_store.list_memories(),
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

SYSTEM_PROMPT_SECTION = (
    "You have tools to search the web, open and read pages, view the user's screen, launch apps, check "
    "the weather, check the time/date, manage a to-do list, control media playback and volume, check "
    "what's currently playing, lock or sleep the PC, set timers and reminders, search and read local "
    "files, check email and calendar, control smart home devices, read/write the clipboard, search "
    "Obsidian notes, run configured macros, and remember/forget durable facts about the user. Use them "
    "proactively — don't ask permission first, just do it and report back concisely.\n\n"
    "Exception: never call set_volume unless the user explicitly asks you to change the volume. Do not "
    "adjust volume as a side effect of any other request."
)

register(Domain(name="system", system_prompt_section=SYSTEM_PROMPT_SECTION, tool_schemas=TOOL_SCHEMAS, dispatch=DISPATCH))


def dispatch_tool(domain: Domain, name: str, tool_input: dict, config: dict) -> list[dict]:
    """Runs a tool call within the given domain and returns Anthropic-shaped content blocks.
    Every domain's tool-calling loop (see llm/client.py) goes through this, not just system's —
    it lives here because system.py is where the special view_screen image case originated, but
    it's domain-agnostic (looks up domain.dispatch, not this module's DISPATCH specifically).
    """
    if name == "view_screen":
        b64 = screen_capture.capture_screenshot_b64()
        return [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
            {"type": "text", "text": "Screenshot captured."},
        ]

    handler = domain.dispatch.get(name)
    if handler is None:
        return [{"type": "text", "text": f"Unknown tool: {name}"}]

    try:
        result = handler(tool_input, config)
    except Exception as e:
        result = f"That didn't work — {e}"
    return [{"type": "text", "text": result}]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_system_domain.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add domains/system.py tests/test_system_domain.py
git commit -m "Add system domain (ports old tools.py aggregation)"
```

---

### Task 6: `domains/business.py` — venture and notification tracking

**Files:**
- Create: `domains/business.py`
- Test: `tests/test_business_domain.py`

**Interfaces:**
- Consumes: `memory_store.create_venture`/`update_venture`/`list_ventures`/`get_venture`/`add_notification`/`list_notifications`/`resolve_notification` (Task 2), `domains.registry.Domain`/`register` (Task 3), `domains.system.dispatch_tool` (Task 5, reused generically).
- Produces: registers `Domain(name="business", ...)`. Tool names: `create_venture`, `update_venture`, `list_ventures`, `log_notification`, `list_notifications`, `resolve_notification`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_business_domain.py
import importlib

import pytest

from domains.registry import get_domain
from domains.system import dispatch_tool


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    import memory_store
    monkeypatch.setattr(memory_store, "DB_PATH", tmp_path / "test.db")
    memory_store.init_db()
    import domains.business
    importlib.reload(domains.business)  # re-registers against the now-empty test DB
    return memory_store


def test_business_domain_registered():
    d = get_domain("business")
    names = {s["name"] for s in d.tool_schemas}
    assert {"create_venture", "update_venture", "list_ventures", "log_notification",
            "list_notifications", "resolve_notification"} == names


def test_create_and_list_ventures_via_dispatch():
    d = get_domain("business")
    dispatch_tool(d, "create_venture", {"name": "Acme Co", "status": "idea"}, {})
    result = dispatch_tool(d, "list_ventures", {}, {})
    assert "Acme Co" in result[0]["text"]


def test_update_venture_via_dispatch():
    d = get_domain("business")
    dispatch_tool(d, "create_venture", {"name": "Acme Co"}, {})
    result = dispatch_tool(d, "update_venture", {"name": "Acme Co", "status": "active"}, {})
    assert "Updated" in result[0]["text"]
    listing = dispatch_tool(d, "list_ventures", {}, {})
    assert "active" in listing[0]["text"]


def test_update_venture_not_found_via_dispatch():
    d = get_domain("business")
    result = dispatch_tool(d, "update_venture", {"name": "Nope", "status": "active"}, {})
    assert "couldn't find" in result[0]["text"].lower()


def test_log_and_list_notifications_via_dispatch():
    d = get_domain("business")
    dispatch_tool(d, "log_notification", {"source": "email", "summary": "Acme wants a quote"}, {})
    result = dispatch_tool(d, "list_notifications", {}, {})
    assert "Acme wants a quote" in result[0]["text"]


def test_resolve_notification_via_dispatch():
    d = get_domain("business")
    dispatch_tool(d, "log_notification", {"source": "email", "summary": "Acme wants a quote"}, {})
    result = dispatch_tool(d, "resolve_notification", {"id_or_text": "Acme"}, {})
    assert "Resolved" in result[0]["text"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_business_domain.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'domains.business'`

- [ ] **Step 3: Implement `domains/business.py`**

```python
"""Business domain: tracks ventures/projects/companies Tariq is building (structured
entities — name, status, notes, next steps) and client/project notifications he reports
by voice. No automated notification ingestion in this build — see design spec.
"""
import memory_store
from domains.registry import Domain, register

CREATE_VENTURE_SCHEMA = {
    "name": "create_venture",
    "description": "Start tracking a new business venture/project/company idea.",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Venture name."},
            "status": {"type": "string", "description": "e.g. 'idea', 'active', 'paused'. Defaults to 'idea'."},
            "notes": {"type": "string", "description": "Freeform notes about the venture."},
        },
        "required": ["name"],
    },
}

UPDATE_VENTURE_SCHEMA = {
    "name": "update_venture",
    "description": "Update an existing venture's status, notes, or next steps. Only the given fields change.",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Venture name to update."},
            "status": {"type": "string"},
            "notes": {"type": "string"},
            "next_steps": {"type": "string"},
        },
        "required": ["name"],
    },
}

LIST_VENTURES_SCHEMA = {
    "name": "list_ventures",
    "description": "List all tracked business ventures with their status and next steps.",
    "input_schema": {"type": "object", "properties": {}},
}

LOG_NOTIFICATION_SCHEMA = {
    "name": "log_notification",
    "description": "Record a client/project notification the user just told you about (an email, a message, a heads-up) so it can be recalled or resolved later.",
    "input_schema": {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Where it came from, e.g. 'email', 'a call with Acme'."},
            "summary": {"type": "string", "description": "What it's about."},
        },
        "required": ["source", "summary"],
    },
}

LIST_NOTIFICATIONS_SCHEMA = {
    "name": "list_notifications",
    "description": "List open (unresolved) client/project notifications.",
    "input_schema": {"type": "object", "properties": {}},
}

RESOLVE_NOTIFICATION_SCHEMA = {
    "name": "resolve_notification",
    "description": "Mark a notification as resolved/handled. Match by id or by text in its summary.",
    "input_schema": {
        "type": "object",
        "properties": {"id_or_text": {"type": "string", "description": "The notification's id, or text matching its summary."}},
        "required": ["id_or_text"],
    },
}


def _create_venture(i: dict, c: dict) -> str:
    memory_store.create_venture(i["name"], status=i.get("status", "idea"), notes=i.get("notes", ""))
    return f"Started tracking venture '{i['name']}'."


def _update_venture(i: dict, c: dict) -> str:
    ok = memory_store.update_venture(
        i["name"], status=i.get("status"), notes=i.get("notes"), next_steps=i.get("next_steps")
    )
    return f"Updated '{i['name']}'." if ok else f"Couldn't find a venture named '{i['name']}'."


def _list_ventures(i: dict, c: dict) -> str:
    ventures = memory_store.list_ventures()
    if not ventures:
        return "No ventures tracked yet."
    return "\n".join(
        f"- {v['name']} ({v['status']}): {v['notes'] or 'no notes'} — next: {v['next_steps'] or 'none set'}"
        for v in ventures
    )


def _log_notification(i: dict, c: dict) -> str:
    memory_store.add_notification(i["source"], i["summary"])
    return f"Logged: {i['summary']}"


def _list_notifications(i: dict, c: dict) -> str:
    notifs = memory_store.list_notifications(status="new")
    if not notifs:
        return "No open notifications."
    return "\n".join(f"- [{n['id']}] {n['summary']} (from {n['source']})" for n in notifs)


def _resolve_notification(i: dict, c: dict) -> str:
    ok = memory_store.resolve_notification(i["id_or_text"])
    return "Resolved." if ok else f"Couldn't find a notification matching '{i['id_or_text']}'."


TOOL_SCHEMAS = [
    CREATE_VENTURE_SCHEMA, UPDATE_VENTURE_SCHEMA, LIST_VENTURES_SCHEMA,
    LOG_NOTIFICATION_SCHEMA, LIST_NOTIFICATIONS_SCHEMA, RESOLVE_NOTIFICATION_SCHEMA,
]

DISPATCH = {
    "create_venture": _create_venture,
    "update_venture": _update_venture,
    "list_ventures": _list_ventures,
    "log_notification": _log_notification,
    "list_notifications": _list_notifications,
    "resolve_notification": _resolve_notification,
}

SYSTEM_PROMPT_SECTION = (
    "You help track the user's business ventures (projects/companies they're building) and "
    "client/project notifications they tell you about. Create a venture the first time one comes "
    "up in conversation; update it as things change. Log notifications when the user reports one "
    "(e.g. 'I got an email from Acme'); there's no automated inbox scanning, so only what the user "
    "tells you gets tracked."
)

register(Domain(name="business", system_prompt_section=SYSTEM_PROMPT_SECTION, tool_schemas=TOOL_SCHEMAS, dispatch=DISPATCH))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_business_domain.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add domains/business.py tests/test_business_domain.py
git commit -m "Add business domain (venture and notification tracking)"
```

---

### Task 7: `domains/dev.py` — Claude Code session orchestration

**Files:**
- Create: `domains/dev.py`
- Test: `tests/test_dev_domain.py`

**Interfaces:**
- Consumes: `memory_store.create_dev_session`/`update_dev_session`/`list_dev_sessions` (Task 2), `domains.registry.Domain`/`register` (Task 3), `domains.system.dispatch_tool` (Task 5).
- Produces: registers `Domain(name="dev", ...)`. Tool names: `launch_claude_code_session`, `list_dev_sessions`. Also exports `poll_dev_sessions() -> None`, called from `server.py`'s proactive loop (Task 12) to reap finished subprocesses.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_dev_domain.py
import importlib
from unittest.mock import MagicMock, patch

import pytest

from domains.registry import get_domain
from domains.system import dispatch_tool


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    import memory_store
    monkeypatch.setattr(memory_store, "DB_PATH", tmp_path / "test.db")
    memory_store.init_db()
    import domains.dev
    importlib.reload(domains.dev)
    return memory_store


def test_dev_domain_registered():
    d = get_domain("dev")
    names = {s["name"] for s in d.tool_schemas}
    assert names == {"launch_claude_code_session", "list_dev_sessions"}


@patch("domains.dev.subprocess.Popen")
def test_launch_claude_code_session_starts_process_and_records_it(mock_popen):
    mock_popen.return_value = MagicMock(pid=4242)
    d = get_domain("dev")
    config = {"claude_code_workspaces": {"websites": "C:\\fake\\websites"}}
    result = dispatch_tool(d, "launch_claude_code_session", {"project": "websites", "prompt": "fix the bug"}, config)
    assert "websites" in result[0]["text"]
    mock_popen.assert_called_once_with(
        ["claude", "-p", "fix the bug"], cwd="C:\\fake\\websites", stdout=-3, stderr=-3
    )
    import memory_store
    sessions = memory_store.list_dev_sessions(status="running")
    assert len(sessions) == 1 and sessions[0]["pid"] == 4242


def test_launch_claude_code_session_unknown_project():
    d = get_domain("dev")
    result = dispatch_tool(d, "launch_claude_code_session", {"project": "nope", "prompt": "x"}, {"claude_code_workspaces": {}})
    assert "don't know" in result[0]["text"].lower()


@patch("domains.dev.subprocess.Popen")
def test_list_dev_sessions_via_dispatch(mock_popen):
    mock_popen.return_value = MagicMock(pid=1)
    d = get_domain("dev")
    config = {"claude_code_workspaces": {"websites": "C:\\fake\\websites"}}
    dispatch_tool(d, "launch_claude_code_session", {"project": "websites", "prompt": "fix the bug"}, config)
    result = dispatch_tool(d, "list_dev_sessions", {}, {})
    assert "websites" in result[0]["text"]
    assert "running" in result[0]["text"]


def test_poll_dev_sessions_marks_finished_process_done():
    import domains.dev as dev
    import memory_store
    session_id = memory_store.create_dev_session("websites", "fix the bug", pid=999)
    finished_proc = MagicMock()
    finished_proc.poll.return_value = 0  # exited
    dev._RUNNING[session_id] = finished_proc
    dev.poll_dev_sessions()
    sessions = memory_store.list_dev_sessions(status="finished")
    assert len(sessions) == 1
    assert session_id not in dev._RUNNING
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dev_domain.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'domains.dev'`

- [ ] **Step 3: Implement `domains/dev.py`**

```python
"""Dev domain: launch and check on Claude Code sessions for the user's projects by voice.
Scope is deliberately limited to launching/managing sessions — no git-status awareness or
generic task tracking (see design spec's YAGNI note).

Sessions are launched headless (`claude -p "<prompt>"`) as background subprocesses — never
awaited inline, since a real coding session can run for minutes. `_RUNNING` tracks live
Popen handles in-memory (lost on server restart, which is fine — poll_dev_sessions() only
needs to reap processes started by this same server run); memory_store is the durable
record of what was launched and its last-known status.
"""
import subprocess

import memory_store
from domains.registry import Domain, register

LAUNCH_SCHEMA = {
    "name": "launch_claude_code_session",
    "description": "Launch a headless Claude Code session on one of the user's configured projects.",
    "input_schema": {
        "type": "object",
        "properties": {
            "project": {"type": "string", "description": "Project name, must match a key in the configured workspaces."},
            "prompt": {"type": "string", "description": "What to ask Claude Code to do."},
        },
        "required": ["project", "prompt"],
    },
}

LIST_SESSIONS_SCHEMA = {
    "name": "list_dev_sessions",
    "description": "List recent Claude Code sessions launched by Jarvis and their status.",
    "input_schema": {"type": "object", "properties": {}},
}

_RUNNING: dict[int, subprocess.Popen] = {}


def _launch(i: dict, c: dict) -> str:
    workspaces = c.get("claude_code_workspaces", {})
    project_path = workspaces.get(i["project"])
    if not project_path:
        known = ", ".join(workspaces) or "none configured"
        return f"I don't know a project called '{i['project']}'. Configured: {known}."

    proc = subprocess.Popen(
        ["claude", "-p", i["prompt"]], cwd=project_path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    session_id = memory_store.create_dev_session(i["project"], i["prompt"], pid=proc.pid)
    _RUNNING[session_id] = proc
    return f"Kicked off Claude Code on {i['project']}: {i['prompt']}"


def _list_sessions(i: dict, c: dict) -> str:
    sessions = memory_store.list_dev_sessions()
    if not sessions:
        return "No dev sessions launched yet."
    return "\n".join(f"- {s['project']} ({s['status']}): {s['prompt']}" for s in sessions[:10])


def poll_dev_sessions() -> None:
    """Reaps finished subprocesses, updating memory_store. Call periodically from server.py's
    proactive loop — see design spec's Dev domain section."""
    for session_id, proc in list(_RUNNING.items()):
        if proc.poll() is not None:
            memory_store.update_dev_session(session_id, status="finished")
            del _RUNNING[session_id]


TOOL_SCHEMAS = [LAUNCH_SCHEMA, LIST_SESSIONS_SCHEMA]
DISPATCH = {"launch_claude_code_session": _launch, "list_dev_sessions": _list_sessions}

SYSTEM_PROMPT_SECTION = (
    "You can launch headless Claude Code sessions on the user's configured projects and report "
    "on ones already launched. Sessions run in the background — launching one doesn't wait for "
    "it to finish; tell the user it's started and that you'll have status if they ask later."
)

register(Domain(name="dev", system_prompt_section=SYSTEM_PROMPT_SECTION, tool_schemas=TOOL_SCHEMAS, dispatch=DISPATCH))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_dev_domain.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add domains/dev.py tests/test_dev_domain.py
git commit -m "Add dev domain (Claude Code session orchestration)"
```

---

### Task 8: `domains/school.py` — SchoolPlan integration

**Files:**
- Create: `domains/school.py`
- Test: `tests/test_school_domain.py`

**Interfaces:**
- Consumes: `domains.registry.Domain`/`register` (Task 3), `domains.system.dispatch_tool` (Task 5). Config keys: `schoolplan_url`, `schoolplan_user`, `schoolplan_password`.
- Produces: registers `Domain(name="school", ...)`. Tool names: `get_schedule`, `add_study_block`, `mark_block_done`. Internal: `SchoolPlanClient` class (session-cookie auth against the real `school.tariqkhalif.me` API described in "Context for the implementer" above).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_school_domain.py
from unittest.mock import MagicMock, patch

import pytest

from domains.registry import get_domain
from domains.system import dispatch_tool

CONFIG = {"schoolplan_url": "https://school.tariqkhalif.me", "schoolplan_user": "tariq", "schoolplan_password": "secret"}


def _json_response(data):
    resp = MagicMock()
    resp.json.return_value = data
    return resp


@patch("domains.school.requests.Session")
def test_get_schedule_via_dispatch(mock_session_cls):
    session = MagicMock()
    session.get.return_value = _json_response({"blocks": [
        {"id": "1", "day": "mon", "start": "09:30", "end": "10:20", "label": "CMPT 120 LEC", "category": "study", "done": False, "course": "CMPT 120"}
    ]})
    mock_session_cls.return_value = session

    d = get_domain("school")
    result = dispatch_tool(d, "get_schedule", {}, CONFIG)
    assert "CMPT 120 LEC" in result[0]["text"]
    assert "mon" in result[0]["text"]


@patch("domains.school.requests.Session")
def test_add_study_block_via_dispatch(mock_session_cls):
    session = MagicMock()
    session.get.return_value = _json_response({"blocks": []})
    session.put.return_value = MagicMock(status_code=200)
    mock_session_cls.return_value = session

    d = get_domain("school")
    result = dispatch_tool(
        d, "add_study_block",
        {"day": "wed", "start": "18:00", "end": "19:00", "label": "Review MACM proofs", "category": "study", "course": "MACM 101"},
        CONFIG,
    )
    assert "Added" in result[0]["text"]
    put_body = session.put.call_args.kwargs["json"]
    assert len(put_body["blocks"]) == 1
    assert put_body["blocks"][0]["label"] == "Review MACM proofs"


@patch("domains.school.requests.Session")
def test_mark_block_done_via_dispatch(mock_session_cls):
    session = MagicMock()
    session.get.return_value = _json_response({"blocks": [
        {"id": "1", "day": "mon", "start": "09:30", "end": "10:20", "label": "Assignment 1", "category": "study", "done": False}
    ]})
    session.put.return_value = MagicMock(status_code=200)
    mock_session_cls.return_value = session

    d = get_domain("school")
    result = dispatch_tool(d, "mark_block_done", {"label_or_id": "Assignment 1"}, CONFIG)
    assert "Marked" in result[0]["text"]
    put_body = session.put.call_args.kwargs["json"]
    assert put_body["blocks"][0]["done"] is True


@patch("domains.school.requests.Session")
def test_mark_block_done_not_found(mock_session_cls):
    session = MagicMock()
    session.get.return_value = _json_response({"blocks": []})
    mock_session_cls.return_value = session

    d = get_domain("school")
    result = dispatch_tool(d, "mark_block_done", {"label_or_id": "Nothing"}, CONFIG)
    assert "couldn't find" in result[0]["text"].lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_school_domain.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'domains.school'`

- [ ] **Step 3: Implement `domains/school.py`**

```python
"""School domain: reads/writes SchoolPlan's weekly study-block schedule at
school.tariqkhalif.me. SchoolPlan is a Cloudflare Worker; auth is a POST /login with
username/password that sets a session cookie, then GET/PUT /api/calendar with
{"blocks": [...]}. There's no partial-update endpoint — PUT replaces the whole list, so
add/complete both read the current list, modify it, and PUT the result back. An
unauthenticated request to any path returns the login page HTML (status 200), not a 401 —
so auth failure is detected by JSON-decode failure, not status code.
"""
import requests
from domains.registry import Domain, register

GET_SCHEDULE_SCHEMA = {
    "name": "get_schedule",
    "description": "Get the user's current weekly study/class schedule from SchoolPlan.",
    "input_schema": {"type": "object", "properties": {}},
}

ADD_STUDY_BLOCK_SCHEMA = {
    "name": "add_study_block",
    "description": "Add a new block to the weekly study schedule.",
    "input_schema": {
        "type": "object",
        "properties": {
            "day": {"type": "string", "description": "One of mon, tue, wed, thu, fri, sat, sun."},
            "start": {"type": "string", "description": "Start time, 24h HH:MM."},
            "end": {"type": "string", "description": "End time, 24h HH:MM."},
            "label": {"type": "string", "description": "What the block is, e.g. 'Review MACM proofs'."},
            "category": {"type": "string", "description": "One of study, filming, personal, coding."},
            "course": {"type": "string", "description": "Optional: MATH 150, CMPT 120, MACM 101, or CMPT 105W."},
        },
        "required": ["day", "start", "end", "label", "category"],
    },
}

MARK_BLOCK_DONE_SCHEMA = {
    "name": "mark_block_done",
    "description": "Mark a schedule block as done, matched by its id or label text.",
    "input_schema": {
        "type": "object",
        "properties": {"label_or_id": {"type": "string", "description": "The block's id, or text matching its label."}},
        "required": ["label_or_id"],
    },
}


class SchoolPlanClient:
    def __init__(self, config: dict):
        self.base_url = config["schoolplan_url"].rstrip("/")
        self.user = config["schoolplan_user"]
        self.password = config["schoolplan_password"]
        self.session = requests.Session()
        self._logged_in = False

    def _login(self) -> None:
        self.session.post(f"{self.base_url}/login", data={"username": self.user, "password": self.password}, timeout=10)
        self._logged_in = True

    def get_blocks(self) -> list[dict]:
        if not self._logged_in:
            self._login()
        resp = self.session.get(f"{self.base_url}/api/calendar", timeout=10)
        try:
            data = resp.json()
        except ValueError:
            self._login()
            resp = self.session.get(f"{self.base_url}/api/calendar", timeout=10)
            data = resp.json()
        return data.get("blocks", [])

    def put_blocks(self, blocks: list[dict]) -> None:
        if not self._logged_in:
            self._login()
        self.session.put(f"{self.base_url}/api/calendar", json={"blocks": blocks}, timeout=10)


def _get_schedule(i: dict, c: dict) -> str:
    blocks = SchoolPlanClient(c).get_blocks()
    if not blocks:
        return "No schedule blocks set."
    return "\n".join(
        f"- {b['day']} {b['start']}-{b['end']}: {b['label']}" + (" (done)" if b.get("done") else "")
        for b in blocks
    )


def _add_study_block(i: dict, c: dict) -> str:
    client = SchoolPlanClient(c)
    blocks = client.get_blocks()
    new_block = {
        "id": str(max((int(b["id"]) for b in blocks if str(b["id"]).isdigit()), default=0) + 1),
        "day": i["day"], "start": i["start"], "end": i["end"],
        "label": i["label"], "category": i["category"], "done": False,
    }
    if i.get("course"):
        new_block["course"] = i["course"]
    client.put_blocks(blocks + [new_block])
    return f"Added '{i['label']}' on {i['day']} {i['start']}-{i['end']}."


def _mark_block_done(i: dict, c: dict) -> str:
    client = SchoolPlanClient(c)
    blocks = client.get_blocks()
    target = next(
        (b for b in blocks if b["id"] == i["label_or_id"] or i["label_or_id"].lower() in b["label"].lower()), None
    )
    if not target:
        return f"Couldn't find a schedule block matching '{i['label_or_id']}'."
    target["done"] = True
    client.put_blocks(blocks)
    return f"Marked '{target['label']}' as done."


TOOL_SCHEMAS = [GET_SCHEDULE_SCHEMA, ADD_STUDY_BLOCK_SCHEMA, MARK_BLOCK_DONE_SCHEMA]
DISPATCH = {"get_schedule": _get_schedule, "add_study_block": _add_study_block, "mark_block_done": _mark_block_done}

SYSTEM_PROMPT_SECTION = (
    "You can read and update the user's weekly study/class schedule on SchoolPlan — a recurring "
    "weekly calendar of class times and study blocks, not a date-specific deadline tracker. Use "
    "add_study_block to schedule new study time and mark_block_done when the user says they've "
    "finished something on it."
)

register(Domain(name="school", system_prompt_section=SYSTEM_PROMPT_SECTION, tool_schemas=TOOL_SCHEMAS, dispatch=DISPATCH))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_school_domain.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add domains/school.py tests/test_school_domain.py
git commit -m "Add school domain (SchoolPlan integration)"
```

---

### Task 9: `domains/content.py` — script drafting and clip generation

**Files:**
- Create: `domains/content.py`
- Test: `tests/test_content_domain.py`

**Interfaces:**
- Consumes: `memory_store.create_clip_job`/`update_clip_job`/`list_recent_clip_jobs` (Task 2), `domains.registry.Domain`/`register` (Task 3), `domains.system.dispatch_tool` (Task 5). Config key: `publikclip_path` (defaults to `transfer\websites\publikclip` if unset).
- Produces: registers `Domain(name="content", ...)`. Tool names: `generate_clips`, `check_clip_jobs`. Also exports `poll_clip_jobs() -> None`, called from `server.py`'s proactive loop (Task 12).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_content_domain.py
import importlib
from unittest.mock import MagicMock, patch

import pytest

from domains.registry import get_domain
from domains.system import dispatch_tool


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    import memory_store
    monkeypatch.setattr(memory_store, "DB_PATH", tmp_path / "test.db")
    memory_store.init_db()
    import domains.content
    importlib.reload(domains.content)
    return memory_store


def test_content_domain_registered():
    d = get_domain("content")
    names = {s["name"] for s in d.tool_schemas}
    assert names == {"generate_clips", "check_clip_jobs"}


@patch("domains.content.subprocess.Popen")
def test_generate_clips_starts_background_job(mock_popen):
    mock_popen.return_value = MagicMock(pid=555)
    d = get_domain("content")
    url = "https://youtube.com/watch?v=abc"
    result = dispatch_tool(d, "generate_clips", {"url": url}, {})
    assert "started" in result[0]["text"].lower()
    args, kwargs = mock_popen.call_args
    assert args[0] == ["uv", "run", "publikclip", "run", url]
    import memory_store
    jobs = memory_store.list_recent_clip_jobs()
    assert jobs[0]["source_url"] == url
    assert jobs[0]["status"] == "queued"


@patch("domains.content.subprocess.Popen")
def test_check_clip_jobs_via_dispatch(mock_popen):
    mock_popen.return_value = MagicMock(pid=1)
    d = get_domain("content")
    dispatch_tool(d, "generate_clips", {"url": "https://youtube.com/watch?v=abc"}, {})
    result = dispatch_tool(d, "check_clip_jobs", {}, {})
    assert "queued" in result[0]["text"]


def test_poll_clip_jobs_marks_finished_job_done():
    import domains.content as content
    import memory_store
    job_id = memory_store.create_clip_job("https://youtube.com/watch?v=abc")
    finished_proc = MagicMock()
    finished_proc.poll.return_value = 0
    content._RUNNING[job_id] = finished_proc
    content.poll_clip_jobs()
    jobs = memory_store.list_recent_clip_jobs()
    assert jobs[0]["status"] == "done"
    assert job_id not in content._RUNNING


def test_poll_clip_jobs_marks_failed_job_error():
    import domains.content as content
    import memory_store
    job_id = memory_store.create_clip_job("https://youtube.com/watch?v=abc")
    failed_proc = MagicMock()
    failed_proc.poll.return_value = 1  # nonzero exit
    content._RUNNING[job_id] = failed_proc
    content.poll_clip_jobs()
    jobs = memory_store.list_recent_clip_jobs()
    assert jobs[0]["status"] == "failed"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_content_domain.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'domains.content'`

- [ ] **Step 3: Implement `domains/content.py`**

```python
"""Content domain: conversational script/caption drafting for @dailytariq (no tool needed
— just system-prompt guidance), plus a generate_clips tool that calls the *existing*
publikclip pipeline for whoaskinyou. publikclip is slow, so this launches it as a
background subprocess and tracks it in memory_store — never awaited inline. The future
faster pipeline (see design spec's Out of scope section) swaps the subprocess command in
_generate, nothing else in this file.
"""
import subprocess
from pathlib import Path

import memory_store
from domains.registry import Domain, register

DEFAULT_PUBLIKCLIP_PATH = str(Path(__file__).parent.parent.parent / "websites" / "publikclip")

GENERATE_CLIPS_SCHEMA = {
    "name": "generate_clips",
    "description": "Start generating clips from a YouTube video for the whoaskinyou account. Runs in the background — check progress with check_clip_jobs.",
    "input_schema": {
        "type": "object",
        "properties": {"url": {"type": "string", "description": "The YouTube video URL to clip."}},
        "required": ["url"],
    },
}

CHECK_CLIP_JOBS_SCHEMA = {
    "name": "check_clip_jobs",
    "description": "Check the status of recent clip-generation jobs.",
    "input_schema": {"type": "object", "properties": {}},
}

_RUNNING: dict[int, subprocess.Popen] = {}


def _generate(i: dict, c: dict) -> str:
    publikclip_path = c.get("publikclip_path", DEFAULT_PUBLIKCLIP_PATH)
    proc = subprocess.Popen(
        ["uv", "run", "publikclip", "run", i["url"]],
        cwd=publikclip_path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    job_id = memory_store.create_clip_job(i["url"])
    _RUNNING[job_id] = proc
    return f"Clip generation started for {i['url']} — I'll let you know, or ask me to check on it."


def _check_jobs(i: dict, c: dict) -> str:
    jobs = memory_store.list_recent_clip_jobs()
    if not jobs:
        return "No clip jobs yet."
    return "\n".join(f"- {j['source_url']}: {j['status']}" for j in jobs)


def poll_clip_jobs() -> None:
    """Reaps finished publikclip subprocesses. Call periodically from server.py's proactive loop."""
    from datetime import datetime
    for job_id, proc in list(_RUNNING.items()):
        returncode = proc.poll()
        if returncode is None:
            continue
        status = "done" if returncode == 0 else "failed"
        memory_store.update_clip_job(job_id, status=status, finished_at=datetime.now().isoformat())
        del _RUNNING[job_id]


TOOL_SCHEMAS = [GENERATE_CLIPS_SCHEMA, CHECK_CLIP_JOBS_SCHEMA]
DISPATCH = {"generate_clips": _generate, "check_clip_jobs": _check_jobs}

SYSTEM_PROMPT_SECTION = (
    "You help brainstorm and draft scripts and captions for the @dailytariq account through "
    "normal conversation — no tool needed for that, just talk it through. For whoaskinyou, use "
    "generate_clips to kick off clip generation from a YouTube URL (it runs in the background and "
    "is currently slow, so tell the user it'll take a while) and check_clip_jobs to report status."
)

register(Domain(name="content", system_prompt_section=SYSTEM_PROMPT_SECTION, tool_schemas=TOOL_SCHEMAS, dispatch=DISPATCH))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_content_domain.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add domains/content.py tests/test_content_domain.py
git commit -m "Add content domain (script drafting guidance + publikclip clip jobs)"
```

---

### Task 10: `llm/router.py` — domain classification

**Files:**
- Create: `llm/__init__.py` (empty), `llm/router.py`
- Test: `tests/test_router.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (only needs `domains.registry.all_domain_names`, populated once all domain modules are imported — but the router hard-codes the same six names so it works even in isolation/tests without importing every domain module).
- Produces: `DOMAIN_NAMES: list[str]`, `classify_domain(client, model: str, message: str) -> str`. Consumed by `llm/client.py` (Task 11).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_router.py
from unittest.mock import MagicMock

from llm.router import classify_domain, DOMAIN_NAMES


def _client_returning(text: str) -> MagicMock:
    client = MagicMock()
    client.interactions.create.return_value = MagicMock(output_text=text)
    return client


def test_classify_domain_valid_response():
    client = _client_returning("school")
    assert classify_domain(client, "gemini-3-flash-preview", "what's on my schedule today") == "school"


def test_classify_domain_strips_whitespace_and_case():
    client = _client_returning("  Business \n")
    assert classify_domain(client, "gemini-3-flash-preview", "log a client email") == "business"


def test_classify_domain_invalid_response_falls_back_to_general():
    client = _client_returning("not_a_real_domain")
    assert classify_domain(client, "gemini-3-flash-preview", "hello") == "general"


def test_classify_domain_empty_response_falls_back_to_general():
    client = _client_returning("")
    assert classify_domain(client, "gemini-3-flash-preview", "hello") == "general"


def test_domain_names_matches_registry_shape():
    assert set(DOMAIN_NAMES) == {"system", "dev", "school", "content", "business", "general"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_router.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'llm'`

- [ ] **Step 3: Implement `llm/router.py`**

```python
"""Classifies a user utterance into one of the domains defined in domains.registry, via a
single lightweight Gemini call — kept separate from the full tool-calling turn so each
domain's system prompt/tools stay small and focused (see design spec's Architecture
section for why: a flat tool list across all domains was the old Jarvis's approach and
doesn't scale past its 37 tools).
"""
DOMAIN_NAMES = ["system", "dev", "school", "content", "business", "general"]

ROUTER_PROMPT = """Classify the user's message into exactly one category. Reply with only \
the category word, nothing else.

- system: media/volume/power, launching apps, files, screenshots, macros, clipboard, \
weather, date/time, web search, smart home, email/calendar, timers/reminders, to-do tasks, \
remembering/forgetting facts about the user.
- dev: launching or checking on Claude Code sessions for a coding project.
- school: SchoolPlan weekly study schedule — viewing, adding, or completing study blocks.
- content: drafting scripts/captions for @dailytariq, or generating/checking clips for \
whoaskinyou from a YouTube video.
- business: tracking business ventures/projects/companies, or client/project notifications \
the user reports.
- general: anything else — small talk, opinions, questions that don't need a tool.

Categories: system, dev, school, content, business, general

Message: {message}"""


def classify_domain(client, model: str, message: str) -> str:
    interaction = client.interactions.create(model=model, input=ROUTER_PROMPT.format(message=message))
    guess = (interaction.output_text or "general").strip().lower()
    return guess if guess in DOMAIN_NAMES else "general"
```

Create `llm/__init__.py` as an empty file.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_router.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add llm/__init__.py llm/router.py tests/test_router.py
git commit -m "Add domain router"
```

---

### Task 11: `llm/client.py` — Gemini tool-calling client

**Files:**
- Create: `llm/client.py`
- Test: `tests/test_llm_client.py`

**Interfaces:**
- Consumes: `llm.router.classify_domain` (Task 10), `domains.registry.Domain`/`get_domain` (Task 3), `domains.system.dispatch_tool` (Task 5).
- Produces: `GeminiClient` class with `__init__(self, api_keys: list[str], model: str)`, `run_turn(self, user_input, previous_interaction_id: str | None, on_status: Callable[[str], None], client_index: int, config: dict) -> tuple[str, str, int, str]` (returns `(response_text, new_interaction_id, client_index_used, domain_used)`). This is the "swappable interface" the spec calls for — `server.py` (Task 12) only ever calls `run_turn`; a future `ClaudeClient` implementing the same method signature is the whole migration.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_llm_client.py
from unittest.mock import MagicMock, patch

import pytest

from domains.registry import Domain, register
from llm.client import GeminiClient


@pytest.fixture(autouse=True)
def general_domain():
    register(Domain(name="general", system_prompt_section="Just chat.", tool_schemas=[], dispatch={}))


def _interaction(status="completed", output_text="Hello!", steps=None, interaction_id="int_1"):
    return MagicMock(status=status, output_text=output_text, steps=steps or [], id=interaction_id)


@patch("llm.client.genai.Client")
@patch("llm.client.classify_domain", return_value="general")
def test_run_turn_no_tool_calls(mock_classify, mock_client_cls):
    client_instance = MagicMock()
    client_instance.interactions.create.return_value = _interaction()
    mock_client_cls.return_value = client_instance

    gc = GeminiClient(api_keys=["key1"], model="gemini-3-flash-preview")
    text, interaction_id, client_index, domain = gc.run_turn("hi", None, lambda s: None, 0, {})

    assert text == "Hello!"
    assert interaction_id == "int_1"
    assert client_index == 0
    assert domain == "general"


@patch("llm.client.genai.Client")
@patch("llm.client.classify_domain", return_value="general")
def test_run_turn_falls_back_on_rate_limit(mock_classify, mock_client_cls):
    failing = MagicMock()
    failing.interactions.create.side_effect = Exception("429 quota exceeded")
    working = MagicMock()
    working.interactions.create.return_value = _interaction()
    mock_client_cls.side_effect = [failing, working]

    gc = GeminiClient(api_keys=["key1", "key2"], model="gemini-3-flash-preview")
    text, interaction_id, client_index, domain = gc.run_turn("hi", None, lambda s: None, 0, {})

    assert text == "Hello!"
    assert client_index == 1


@patch("llm.client.genai.Client")
@patch("llm.client.classify_domain", return_value="general")
def test_run_turn_reraises_non_rate_limit_error(mock_classify, mock_client_cls):
    client_instance = MagicMock()
    client_instance.interactions.create.side_effect = ValueError("boom")
    mock_client_cls.return_value = client_instance

    gc = GeminiClient(api_keys=["key1"], model="gemini-3-flash-preview")
    with pytest.raises(ValueError):
        gc.run_turn("hi", None, lambda s: None, 0, {})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_llm_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'llm.client'`

- [ ] **Step 3: Implement `llm/client.py`**

```python
"""Wraps the Gemini interactions API behind run_turn(...), the one method server.py calls.
This is the "swappable interface" from the design spec: upgrading to the Claude API later
means writing a class with the same run_turn signature and changing which one server.py
instantiates — not touching domain or router code. Reuses the old Jarvis's rotating-API-key
rate-limit fallback (20 req/min x N rotating keys on the free tier).
"""
from typing import Callable

from google import genai

from domains.registry import get_domain
from domains.system import dispatch_tool
from llm.router import classify_domain

MAX_TOOL_ROUNDS = 8  # safety cap so a confused tool loop can't run forever


def _to_gemini_tool(schema: dict) -> dict:
    return {"type": "function", "name": schema["name"], "description": schema["description"], "parameters": schema["input_schema"]}


def _to_gemini_result_parts(content_blocks: list[dict]) -> list[dict]:
    parts = []
    for block in content_blocks:
        if block["type"] == "text":
            parts.append({"type": "text", "text": block["text"]})
        elif block["type"] == "image":
            parts.append({"type": "image", "mime_type": block["source"]["media_type"], "data": block["source"]["data"]})
    return parts


def is_rate_limit_error(e: Exception) -> bool:
    text = f"{type(e).__name__} {e}".lower()
    return "429" in text or "quota" in text or ("rate" in text and "limit" in text)


class GeminiClient:
    def __init__(self, api_keys: list[str], model: str):
        self.model = model
        self._clients = [genai.Client(api_key=k) for k in api_keys]

    def run_turn(
        self,
        user_input,
        previous_interaction_id: str | None,
        on_status: Callable[[str], None],
        client_index: int,
        config: dict,
    ) -> tuple[str, str, int, str]:
        """Classifies the domain, then runs a domain-scoped tool-calling turn, falling back to
        other API keys on rate limits. Returns (text, new_interaction_id, client_index_used, domain).
        A key fallback starts a fresh interaction rather than continuing the old one, since
        interaction state is scoped to whichever client created it — the same tradeoff the old
        Jarvis made.
        """
        last_error: Exception | None = None
        for attempt in range(len(self._clients)):
            idx = (client_index + attempt) % len(self._clients)
            pid = previous_interaction_id if attempt == 0 else None
            try:
                domain_name = classify_domain(self._clients[idx], self.model, str(user_input))
                domain = get_domain(domain_name)
                text, new_id = self._run_with_client(self._clients[idx], domain, user_input, pid, on_status, config)
                return text, new_id, idx, domain.name
            except Exception as e:
                last_error = e
                if is_rate_limit_error(e) and attempt < len(self._clients) - 1:
                    on_status("Switching to a backup key...")
                    continue
                raise
        raise last_error

    def _run_with_client(self, client, domain, user_input, previous_interaction_id, on_status, config) -> tuple[str, str]:
        gemini_tools = [_to_gemini_tool(s) for s in domain.tool_schemas]
        interaction = client.interactions.create(
            model=self.model,
            system_instruction=domain.system_prompt_section,
            input=user_input,
            tools=gemini_tools,
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
                content_blocks = dispatch_tool(domain, step.name, step.arguments, config)
                results_input.append(
                    {"type": "function_result", "name": step.name, "call_id": step.id, "result": _to_gemini_result_parts(content_blocks)}
                )
            interaction = client.interactions.create(
                model=self.model, previous_interaction_id=interaction.id, input=results_input, tools=gemini_tools
            )

        text = interaction.output_text or "Sorry, I got stuck on that one."
        return text, interaction.id
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_llm_client.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add llm/client.py tests/test_llm_client.py
git commit -m "Add Gemini tool-calling client with domain routing"
```

---

### Task 12: `server.py` — rewire for domain routing, SQLite memory, and new config

**Files:**
- Create: `server.py`, `config.example.json`
- Modify: none (this is the first write of the new `server.py`)

**Interfaces:**
- Consumes: `llm.client.GeminiClient` (Task 11), `memory_store.init_db`/`load_facts` (Task 2), `domains.dev.poll_dev_sessions` (Task 7), `domains.content.poll_clip_jobs` (Task 9), and — by importing them so they register — `domains.system`, `domains.business`, `domains.dev`, `domains.school`, `domains.content` (Tasks 5-9).
- Produces: the running FastAPI app; no other module imports `server.py`.

- [ ] **Step 1: Implement `server.py`**

(No new automated test for this task — it's the integration point wiring together everything already unit-tested; verification is the manual end-to-end pass in Task 13, matching the design spec's testing approach for the voice/server layer.)

```python
"""Jarvis backend: FastAPI + WebSocket chat, domain-routed Gemini tool-calling, ElevenLabs TTS."""
import asyncio
import base64
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

import requests
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import memory_store
import proactive
import timers
# Importing each domain module registers it with domains.registry (see each module's
# register(...) call at import time) — this import block is the only place that has to
# know every domain exists.
import domains.system  # noqa: F401
import domains.business  # noqa: F401
import domains.dev as dev_domain
import domains.school  # noqa: F401
import domains.content as content_domain
from domains.registry import Domain, register
from llm.client import GeminiClient, is_rate_limit_error

load_dotenv()

CONFIG_PATH = Path(__file__).parent / "config.json"

# "general" has no tools — small talk / anything that doesn't need a tool falls here.
register(Domain(name="general", system_prompt_section="Just talk naturally — no tools needed here.", tool_schemas=[], dispatch={}))


def load_config() -> dict:
    config = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
    config["gemini_api_key"] = config.get("gemini_api_key") or os.getenv("GEMINI_API_KEY", "")
    config["elevenlabs_api_key"] = config.get("elevenlabs_api_key") or os.getenv("ELEVENLABS_API_KEY", "")
    config["elevenlabs_voice_id"] = config.get("elevenlabs_voice_id") or os.getenv("ELEVENLABS_VOICE_ID", "")
    config.setdefault("gemini_model", "gemini-3-flash-preview")
    config.setdefault("user_name", "sir")
    config.setdefault("default_location", "New York")
    config.setdefault("apps", {})
    config.setdefault("server_host", "127.0.0.1")
    config.setdefault("server_port", 8420)
    config.setdefault("claude_code_workspaces", {})
    config.setdefault("schoolplan_url", "")
    config.setdefault("schoolplan_user", "")
    config.setdefault("schoolplan_password", "")
    config.setdefault("publikclip_path", "")
    config.setdefault("elevenlabs_monthly_char_quota", 10000)
    return config


config = load_config()

if not config["gemini_api_key"]:
    raise RuntimeError(
        "No Gemini API key found. Copy config.example.json to config.json and set "
        "gemini_api_key, or put GEMINI_API_KEY in a .env file. Get a free key at "
        "https://aistudio.google.com/apikey"
    )

memory_store.init_db()


def load_gemini_keys() -> list[str]:
    keys = [config["gemini_api_key"]]
    i = 2
    while True:
        key = os.getenv(f"GEMINI_API_KEY_{i}", "")
        if not key:
            break
        keys.append(key)
        i += 1
    return keys


llm = GeminiClient(api_keys=load_gemini_keys(), model=config["gemini_model"])
print(f"[server] Loaded {len(load_gemini_keys())} Gemini API key(s).", flush=True)

_next_client_index = 0


def assign_client_index() -> int:
    global _next_client_index
    idx = _next_client_index
    _next_client_index = (_next_client_index + 1) % max(len(load_gemini_keys()), 1)
    return idx


# --- ElevenLabs TTS with free-tier quota tracking (see Global Constraints: degrade, don't error) ---
_elevenlabs_chars_used_this_month = 0


def synthesize_speech(text: str) -> str | None:
    global _elevenlabs_chars_used_this_month
    if not config.get("elevenlabs_api_key") or not config.get("elevenlabs_voice_id"):
        return None
    if _elevenlabs_chars_used_this_month + len(text) > config["elevenlabs_monthly_char_quota"]:
        print("[server] ElevenLabs free-tier quota likely exhausted — falling back to browser TTS.", flush=True)
        return None
    try:
        resp = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{config['elevenlabs_voice_id']}",
            headers={"xi-api-key": config["elevenlabs_api_key"], "Content-Type": "application/json"},
            json={"text": text, "model_id": "eleven_turbo_v2_5", "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}},
            timeout=20,
        )
        resp.raise_for_status()
        _elevenlabs_chars_used_this_month += len(text)
        return base64.b64encode(resp.content).decode("ascii")
    except requests.RequestException:
        return None


_proactive_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _proactive_task
    _proactive_task = asyncio.create_task(proactive_poll_loop())
    yield


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "frontend")), name="static")


@app.get("/")
def index():
    return FileResponse(str(Path(__file__).parent / "frontend" / "index.html"))


@app.get("/status")
def status():
    import domains.system as system_domain
    return {
        "datetime": system_domain.get_current_datetime(),
        "weather": system_domain.get_weather(None, config.get("default_location", "")),
        "user_name": config.get("user_name", "sir"),
        "location": config.get("default_location", ""),
    }


WAKE_PROMPT = (
    "(System: the user just woke you up — by voice or manually. Greet them briefly — "
    "mention the time and/or weather if it's natural to. Keep it tight, 2-3 sentences.)"
)

active_connections: list[dict] = []
SESSION_ACTIVE_MAX_AGE_S = 45


def is_connection_active(connection: dict) -> bool:
    return connection["session_active"] and (time.monotonic() - connection["session_active_since"]) < SESSION_ACTIVE_MAX_AGE_S


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
            asyncio.run_coroutine_threadsafe(websocket.send_json({"type": "status", "text": text}), loop)

        try:
            final_text, new_id, used_index, _domain = await loop.run_in_executor(
                None, llm.run_turn, user_input, last_interaction_id, on_status, client_index, config
            )
            last_interaction_id = new_id
            client_index = used_index
        except Exception as e:
            print(f"[server] run_turn failed: {e}", flush=True)
            if is_rate_limit_error(e):
                final_text = "All my API keys just hit their free-tier limit — give it under a minute and try again."
            else:
                final_text = "Sorry — I'm having trouble reaching my brain right now. Give it a moment and try again."

        audio_b64 = synthesize_speech(final_text)
        try:
            await websocket.send_json({"type": "assistant_message", "text": final_text, "audio_b64": audio_b64})
        except (WebSocketDisconnect, RuntimeError):
            pass

    async def external_wake():
        mark_session_active(connection, True)
        await websocket.send_json({"type": "wake_push"})
        await handle_turn(WAKE_PROMPT)

    connection = {"websocket": websocket, "external_wake": external_wake, "session_active": False, "session_active_since": 0.0}
    active_connections.append(connection)

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            if msg["type"] == "wake":
                mark_session_active(connection, True)
                await handle_turn(WAKE_PROMPT)
            elif msg["type"] == "user_message":
                mark_session_active(connection, True)
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
    for connection in list(active_connections):
        if is_connection_active(connection):
            continue
        task = asyncio.create_task(connection["external_wake"]())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    return {"woke": len(active_connections)}


async def proactive_poll_loop():
    """Periodically checks for due timers/reminders, reaps finished dev/clip-job subprocesses,
    and speaks anything due into any connection that isn't mid-conversation."""
    while True:
        await asyncio.sleep(5)
        dev_domain.poll_dev_sessions()
        content_domain.poll_clip_jobs()
        due = timers.get_due_items() + proactive.drain_queue()
        if not due:
            continue
        for connection in list(active_connections):
            if is_connection_active(connection):
                continue
            for text in due:
                audio_b64 = synthesize_speech(text)
                await connection["websocket"].send_json({"type": "proactive_message", "text": text, "audio_b64": audio_b64})


if __name__ == "__main__":
    uvicorn.run(app, host=config["server_host"], port=config["server_port"])
```

- [ ] **Step 2: Write `config.example.json`**

```json
{
  "gemini_api_key": "",
  "gemini_model": "gemini-3-flash-preview",
  "elevenlabs_api_key": "",
  "elevenlabs_voice_id": "",
  "elevenlabs_monthly_char_quota": 10000,
  "user_name": "sir",
  "default_location": "New York",
  "apps": {
    "spotify": "spotify:",
    "vscode": "code",
    "chrome": "chrome",
    "obsidian": "obsidian://open",
    "notepad": "notepad",
    "explorer": "explorer"
  },
  "server_host": "127.0.0.1",
  "server_port": 8420,
  "clap_threshold": 0.2,
  "wake_word_threshold": 0.5,
  "mic_device": "",

  "obsidian_vault_path": "",

  "ha_url": "",
  "ha_token": "",

  "email_address": "",
  "email_app_password": "",
  "imap_host": "imap.gmail.com",
  "imap_port": 993,
  "smtp_host": "smtp.gmail.com",
  "smtp_port": 587,
  "calendar_ics_url": "",

  "macros": {
    "work session": [
      {"action": "launch_app", "target": "vscode"},
      {"action": "launch_app", "target": "chrome"},
      {"action": "launch_app", "target": "spotify"}
    ]
  },

  "claude_code_workspaces": {
    "websites": "C:\\Users\\there\\desktop\\transfer\\websites",
    "schoolplan": "C:\\Users\\there\\desktop\\transfer\\SchoolPlan",
    "jarvis": "C:\\Users\\there\\desktop\\transfer\\jarvis"
  },

  "schoolplan_url": "https://school.tariqkhalif.me",
  "schoolplan_user": "",
  "schoolplan_password": "",

  "publikclip_path": "C:\\Users\\there\\desktop\\transfer\\websites\\publikclip"
}
```

- [ ] **Step 3: Verify the app imports and starts cleanly**

Run: `python -c "import server"` (with a `config.json` containing at least a valid `gemini_api_key` copied from `config.example.json`)
Expected: no exceptions; prints `[server] Loaded N Gemini API key(s).`

- [ ] **Step 4: Run the full test suite to make sure nothing regressed**

Run: `pytest -v`
Expected: PASS (all tests from Tasks 2, 3, 5, 6, 7, 8, 9, 10, 11)

- [ ] **Step 5: Commit**

```bash
git add server.py config.example.json
git commit -m "Rewire server.py for domain-routed Gemini calls, SQLite memory, and new config"
```

---

### Task 13: Manual end-to-end verification and docs

**Files:**
- Modify: `README.md`, `SETUP.md` (rewritten to reflect the new config keys and domain structure — not ported verbatim, since the old ones documented the old flat-tool-list structure)

**Interfaces:**
- Consumes: nothing new — this is verification of everything built in Tasks 1-12.
- Produces: nothing consumed by later tasks (terminal task).

- [ ] **Step 1: Set up a real `config.json`**

Copy `config.example.json` to `config.json`. Fill in `gemini_api_key` (or set `GEMINI_API_KEY` in `.env`, same as before), `elevenlabs_api_key` + `elevenlabs_voice_id` (search ElevenLabs' voice library for "Jarvis" and copy its voice ID — same free-tier setup as the archived build), `schoolplan_user`/`schoolplan_password` (Tariq's real SchoolPlan login), and confirm `claude_code_workspaces` paths match real project folders.

- [ ] **Step 2: Start the server and confirm the HUD loads**

Run: `.venv\Scripts\python.exe server.py`, then open `http://127.0.0.1:8420/` in Chrome (required for `SpeechRecognition` support, same as the old build).
Expected: HUD loads, `/status` shows real weather/time, no console errors.

- [ ] **Step 3: Voice-verify each domain**

With the mic open (click-to-wake or say "Hey Jarvis" if `wake_word_trigger.py` is also running), talk through one request per domain and confirm both the spoken response and the underlying action are correct:
- **system**: "what's the weather" / "set a 2 minute timer" — confirm timer fires via the proactive loop.
- **dev**: "kick off Claude Code on websites, just say hello" — confirm a `claude` process launches (check Task Manager) and `list_dev_sessions` reports it.
- **school**: "what's on my schedule" — confirm it matches what's live on `school.tariqkhalif.me`; "add a study block Wednesday 6 to 7pm for MACM review" — confirm it appears on the live site after refresh.
- **content**: "help me brainstorm a script idea for daily tariq" — confirm a normal conversational response, no tool call; "generate clips from [a real YouTube URL]" — confirm a `publikclip` background process starts and `check_clip_jobs` reports "queued".
- **business**: "I'm starting a new venture called Test Co" then "what ventures am I tracking" — confirm round-trip.
- Confirm a normal chit-chat message (e.g. "how's it going") routes to `general` with no tool call and no errors.

- [ ] **Step 4: Verify continuous-conversation behavior is intact**

Wake Jarvis once, ask two follow-up questions without saying "Hey Jarvis" again, then go quiet — confirm it ends the session on its own after the idle timeout rather than staying open forever or requiring re-wake between turns.

- [ ] **Step 5: Rewrite `README.md` and `SETUP.md`**

Update both to describe: the domain-routed architecture (one paragraph, pointing at the design spec for detail), the new `config.json` keys added in this plan (`claude_code_workspaces`, `schoolplan_url`/`schoolplan_user`/`schoolplan_password`, `publikclip_path`, `elevenlabs_monthly_char_quota`), and that `memory.json`/`tasks.json`/`timers.json` are gone in favor of `jarvis.db` (SQLite, gitignored, auto-created on first run via `memory_store.init_db()`). Keep the existing sections on Task Scheduler auto-start (`scripts/launch-session.ps1`) and wake-word setup unchanged — those didn't change.

- [ ] **Step 6: Commit**

```bash
git add README.md SETUP.md
git commit -m "Update docs for domain-routed rebuild"
git push origin master
```
