"""Finds and reads local files by name or content so Jarvis can answer things like
'find my invoice from last month' or 'what's in my notes on X'.
"""
import difflib
from pathlib import Path

# --- Limits -----------------------------------------------------------------
# Keep a broad query on a big home directory from hanging the tool call or
# blowing up the response size sent back to the model.
MAX_RESULTS = 20            # top matches returned to the model
MAX_FILES_SCANNED = 50_000  # hard cap on files walked per search
MAX_CANDIDATES = 500        # stop collecting once we have this many candidate matches to rank

# Directories that are noise for a "find my file" search.
NOISE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "AppData"}

# Plain-text-ish extensions considered for content search. Office formats
# (.docx, .pdf, .xlsx, ...) need real parsers and are explicitly out of scope.
TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".py", ".js", ".ts", ".jsx", ".tsx", ".json",
    ".csv", ".tsv", ".log", ".yaml", ".yml", ".ini", ".cfg", ".conf", ".xml",
    ".html", ".htm", ".css", ".sh", ".bat", ".ps1", ".toml", ".rst",
}

CONTENT_SIZE_LIMIT = 2 * 1024 * 1024  # 2MB - skip bigger files before reading them

READ_MAX_BYTES = 5 * 1024 * 1024  # never read more than this off disk for read_file
READ_MAX_CHARS = 20_000           # returned content is capped/truncated to this

FUZZY_THRESHOLD = 0.6  # difflib ratio floor for a "close enough" filename match

SEARCH_FILES_SCHEMA = {
    "name": "search_files",
    "description": (
        "Search the local filesystem for files by name, and optionally by their text content. "
        "Use this when the user asks to find a file, e.g. 'find my invoice from last month' or "
        "'where's that notes file about X'. Defaults to searching the user's home directory."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Filename text to search for (substring/fuzzy match), e.g. 'invoice' or 'notes'.",
            },
            "path": {
                "type": "string",
                "description": "Directory to search under. Optional — defaults to the user's home directory.",
            },
            "content": {
                "type": "boolean",
                "description": "If true, also search inside plain-text file contents, not just filenames. Slower.",
            },
        },
        "required": ["query"],
    },
}

READ_FILE_SCHEMA = {
    "name": "read_file",
    "description": (
        "Read and return the text content of a local file, e.g. after search_files found it, "
        "or when the user names a specific file they want you to look at ('what's in my notes on X')."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Full path to the text file to read."}
        },
        "required": ["path"],
    },
}


def _iter_files(root: Path):
    """Yields (dirpath, filename) pairs under root, pruning noise dirs, up to MAX_FILES_SCANNED."""
    import os

    scanned = 0
    for dirpath, dirnames, filenames in os.walk(root, topdown=True, onerror=lambda e: None):
        dirnames[:] = [
            d for d in dirnames
            if d not in NOISE_DIRS and not d.startswith(".")
        ]
        for name in filenames:
            if scanned >= MAX_FILES_SCANNED:
                return
            scanned += 1
            yield Path(dirpath), name


def _content_snippet(text: str, query: str) -> str | None:
    q = query.lower()
    for line in text.splitlines():
        if q in line.lower():
            line = line.strip()
            if len(line) > 120:
                idx = line.lower().find(q)
                start = max(0, idx - 40)
                line = ("..." if start > 0 else "") + line[start:start + 120] + "..."
            return line
    return None


def search_files(query: str, path: str | None = None, content: bool = False) -> str:
    if not query or not query.strip():
        return "Please provide something to search for."

    root = Path(path).expanduser() if path else Path.home()
    try:
        root = root.resolve()
    except OSError:
        pass

    if not root.exists():
        return f"Search path not found: {root}"
    if not root.is_dir():
        return f"'{root}' is not a directory."

    query_l = query.lower()
    candidates = []  # list of (score, path, snippet_or_None)

    try:
        for dirpath, name in _iter_files(root):
            full = dirpath / name

            name_match = query_l in name.lower()
            snippet = None

            if not name_match:
                ratio = difflib.SequenceMatcher(None, query_l, name.lower()).ratio()
                fuzzy_match = ratio >= FUZZY_THRESHOLD
                score = ratio
            else:
                fuzzy_match = False
                score = 1.0

            content_match = False
            if content and not name_match and not fuzzy_match:
                ext = full.suffix.lower()
                if ext in TEXT_EXTENSIONS:
                    try:
                        if full.stat().st_size <= CONTENT_SIZE_LIMIT:
                            text = full.read_text(encoding="utf-8", errors="ignore")
                            found_snippet = _content_snippet(text, query)
                            if found_snippet is not None:
                                content_match = True
                                snippet = found_snippet
                                score = 0.5
                    except OSError:
                        pass

            if name_match or fuzzy_match or content_match:
                candidates.append((score, str(full), snippet))
                if len(candidates) >= MAX_CANDIDATES:
                    break
    except Exception as e:
        return f"Search failed: {e}"

    if not candidates:
        where = f" under {root}" if path else ""
        return f"No files matching '{query}'{where}."

    candidates.sort(key=lambda c: c[0], reverse=True)
    top = candidates[:MAX_RESULTS]

    lines = [f"Found {len(candidates)} match(es) for '{query}' (showing top {len(top)}):"]
    for _, p, snippet in top:
        if snippet:
            lines.append(f"- {p}\n    ...{snippet}...")
        else:
            lines.append(f"- {p}")
    return "\n".join(lines)


def read_file(path: str) -> str:
    if not path or not path.strip():
        return "Please provide a file path to read."

    p = Path(path).expanduser()
    try:
        p = p.resolve()
    except OSError:
        pass

    if not p.exists():
        return f"File not found: {p}"
    if p.is_dir():
        return f"'{p}' is a directory, not a file."

    try:
        with open(p, "rb") as f:
            raw = f.read(READ_MAX_BYTES)
    except OSError as e:
        return f"Couldn't read '{p}': {e}"

    if b"\x00" in raw:
        return f"'{p}' appears to be a binary file and can't be displayed as text."

    text = raw.decode("utf-8", errors="replace")

    header = f"--- {p} ---\n"
    if len(text) > READ_MAX_CHARS:
        truncated = text[:READ_MAX_CHARS]
        note = f"\n\n[... truncated, showing first {READ_MAX_CHARS} of {len(text)} characters ...]"
        return header + truncated + note

    return header + text
