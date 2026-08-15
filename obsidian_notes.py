"""Search and read the user's Obsidian vault notes directly from disk.

This is deliberately separate from app_launcher.py's 'obsidian' app entry: that just
opens the Obsidian app via its URI scheme, this reads/searches note *content* on disk
so Jarvis can answer "what does my note on X say" without the app even being open.
"""
import re
from datetime import datetime
from pathlib import Path

MAX_SEARCH_RESULTS = 10
MAX_NOTE_CHARS = 20_000
SNIPPET_RADIUS = 100

SEARCH_NOTES_SCHEMA = {
    "name": "search_notes",
    "description": (
        "Search the user's Obsidian vault for notes matching a query. Matches note titles "
        "and note content. Use this for 'search my notes for X' or 'what have I written about Y'."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Text to search for in note titles/content."}
        },
        "required": ["query"],
    },
}

READ_NOTE_SCHEMA = {
    "name": "read_note",
    "description": (
        "Read the full content of a specific Obsidian note by title (e.g. 'Project Ideas') or "
        "relative path within the vault. Use this for 'what does my note on X say' once you know "
        "which note the user means, or after search_notes has found it."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title_or_path": {
                "type": "string",
                "description": "The note's title (filename without .md) or a relative path within the vault.",
            }
        },
        "required": ["title_or_path"],
    },
}

LIST_RECENT_NOTES_SCHEMA = {
    "name": "list_recent_notes",
    "description": (
        "List the user's most recently modified Obsidian notes. Use this for "
        "'what have I been working on' or 'what did I last write'."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Max number of notes to list. Defaults to 10."}
        },
    },
}


def _get_vault(config: dict) -> tuple[Path | None, str | None]:
    """Resolves the configured vault path. Returns (path, error_message) — exactly one is set."""
    raw = (config or {}).get("obsidian_vault_path")
    if not raw:
        return None, "Obsidian vault isn't configured — add obsidian_vault_path to config.json"
    vault = Path(raw).expanduser()
    if not vault.exists() or not vault.is_dir():
        return None, f"Your Obsidian vault is configured at '{raw}' but that path doesn't exist."
    return vault, None


def _iter_notes(vault: Path):
    """Yields all .md files under the vault, skipping Obsidian's own .obsidian/ config dir."""
    try:
        for path in vault.rglob("*.md"):
            try:
                if ".obsidian" in path.relative_to(vault).parts:
                    continue
                if path.is_file():
                    yield path
            except (OSError, ValueError):
                continue
    except OSError:
        return


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def _clean_markdown(text: str) -> str:
    """Light-touch cleanup of Obsidian syntax: [[Target|Alias]] -> Alias, [[Target]] -> Target.
    Tags (#tag) are left as-is since they read fine in plain text."""

    def _wikilink(m: re.Match) -> str:
        alias = m.group(2)
        return alias[1:] if alias else m.group(1)

    return re.sub(r"\[\[([^\]|]+)(\|[^\]]+)?\]\]", _wikilink, text)


def _snippet(content: str, query: str) -> str:
    idx = content.lower().find(query.lower())
    if idx == -1:
        snippet = content.strip().replace("\n", " ")[: SNIPPET_RADIUS * 2]
        return snippet
    start = max(0, idx - SNIPPET_RADIUS)
    end = min(len(content), idx + len(query) + SNIPPET_RADIUS)
    snippet = content[start:end].strip().replace("\n", " ")
    if start > 0:
        snippet = "..." + snippet
    if end < len(content):
        snippet = snippet + "..."
    return snippet


def search_notes(query: str, config: dict) -> str:
    vault, error = _get_vault(config)
    if error:
        return error
    if not query or not query.strip():
        return "Give me something to search for."

    query_lower = query.lower()
    results = []
    for path in _iter_notes(vault):
        title = path.stem
        title_match = query_lower in title.lower()
        content = _read_text(path)
        if content is None:
            continue
        content_match = query_lower in content.lower()
        if not title_match and not content_match:
            continue
        snippet = _snippet(_clean_markdown(content), query) if content_match else ""
        results.append((title, snippet))
        if len(results) >= MAX_SEARCH_RESULTS:
            break

    if not results:
        return f"No notes found matching '{query}'."

    lines = [f"Found {len(results)} note(s) matching '{query}':"]
    for title, snippet in results:
        if snippet:
            lines.append(f"- {title}: \"{snippet}\"")
        else:
            lines.append(f"- {title}")
    return "\n".join(lines)


def read_note(title_or_path: str, config: dict) -> str:
    vault, error = _get_vault(config)
    if error:
        return error
    if not title_or_path or not title_or_path.strip():
        return "Which note do you mean?"

    wanted = title_or_path.strip()
    wanted_no_ext = wanted[:-3] if wanted.lower().endswith(".md") else wanted
    wanted_norm = wanted_no_ext.replace("\\", "/").lower()

    # Match by relative path (handles 'Folder/Note' references) or bare filename, both
    # case-insensitively. Iterating (rather than a direct Path.is_file() shortcut) also
    # ensures we surface the on-disk casing rather than whatever casing the caller typed —
    # Windows filesystems are case-insensitive so a shortcut would silently pick up the
    # wrong-cased Path object.
    match = None
    exact_matches = []
    substring_matches = []
    for path in _iter_notes(vault):
        rel = path.relative_to(vault).as_posix()
        rel_no_ext = rel[:-3] if rel.lower().endswith(".md") else rel
        if rel_no_ext.lower() == wanted_norm or path.stem.lower() == wanted_norm:
            exact_matches.append(path)
        elif wanted_norm in path.stem.lower():
            substring_matches.append(path)

    if exact_matches:
        match = exact_matches[0]
    elif substring_matches:
        match = sorted(substring_matches, key=lambda p: len(p.stem))[0]

    if match is None:
        return f"Couldn't find a note matching '{title_or_path}'."

    content = _read_text(match)
    if content is None:
        return f"Found '{match.stem}' but couldn't read it."

    content = _clean_markdown(content)
    truncated = len(content) > MAX_NOTE_CHARS
    if truncated:
        content = content[:MAX_NOTE_CHARS]

    header = f"# {match.stem}\n\n"
    footer = f"\n\n[Note truncated — {len(content)}+ characters shown, rest omitted]" if truncated else ""
    return header + content + footer


def list_recent_notes(config: dict, limit: int = 10) -> str:
    vault, error = _get_vault(config)
    if error:
        return error
    limit = limit or 10

    entries = []
    for path in _iter_notes(vault):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        entries.append((mtime, path.stem))

    if not entries:
        return "No notes found in the vault."

    entries.sort(key=lambda e: e[0], reverse=True)
    lines = [f"{len(entries[:limit])} most recently modified note(s):"]
    for mtime, title in entries[:limit]:
        when = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        lines.append(f"- {title} (modified {when})")
    return "\n".join(lines)
