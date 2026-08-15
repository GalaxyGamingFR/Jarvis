"""Windows clipboard read/write via raw ctypes/Win32 API.

Uses OpenClipboard/GetClipboardData/SetClipboardData directly through ctypes.windll.user32 (plus
kernel32's GlobalAlloc/GlobalLock family for the CF_UNICODETEXT memory block), matching
system_control.py's approach of driving Windows-native APIs directly rather than pulling in a
wrapper library (e.g. pyperclip) for something this small.

dispatch_tool() in tools.py has NO per-tool try/except, so every function here must catch its own
errors and return a user-facing string rather than ever raising.
"""
import ctypes
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

# Correct restype/argtypes are required here: ctypes defaults to a 32-bit c_int return, which
# silently truncates the 64-bit HANDLE/void* values these Win32 calls actually return on Win64.
user32.OpenClipboard.restype = wintypes.BOOL
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.CloseClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.restype = wintypes.BOOL
user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = ctypes.c_void_p
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.SetClipboardData.restype = ctypes.c_void_p
user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]

kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
kernel32.GlobalFree.restype = ctypes.c_void_p
kernel32.GlobalFree.argtypes = [ctypes.c_void_p]

GET_CLIPBOARD_SCHEMA = {
    "name": "get_clipboard",
    "description": "Read the current text content of the Windows clipboard. Use for requests like "
                    "'what's on my clipboard' or 'read my clipboard out loud'.",
    "input_schema": {"type": "object", "properties": {}},
}

SET_CLIPBOARD_SCHEMA = {
    "name": "set_clipboard",
    "description": "Copy the given text to the Windows clipboard, replacing whatever is there. Use "
                    "for requests like 'copy that to clipboard', passing text the model already has "
                    "(e.g. from a prior tool result or its own generated text).",
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The text to copy to the clipboard."}
        },
        "required": ["text"],
    },
}

_MAX_OPEN_ATTEMPTS = 5
_RETRY_DELAY_SECONDS = 0.1


def _open_clipboard() -> bool:
    """Tries to open the clipboard, retrying briefly — OpenClipboard transiently fails (returns
    FALSE) whenever another process (a screenshot tool, another app's paste handler, etc.) currently
    holds it, so a single failed attempt doesn't necessarily mean the clipboard is unusable."""
    for attempt in range(_MAX_OPEN_ATTEMPTS):
        if user32.OpenClipboard(None):
            return True
        time.sleep(_RETRY_DELAY_SECONDS)
    return False


def get_clipboard() -> str:
    """Returns the clipboard's current text, or a clear message if it's empty/non-text/unavailable."""
    try:
        # Cheap pre-check that doesn't require the clipboard to be open: avoids opening/closing it
        # at all when there's plainly no text format present (e.g. an image was copied).
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return "Clipboard is empty or doesn't contain text."

        if not _open_clipboard():
            return "Couldn't access the clipboard right now — it's busy in another app."

        try:
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return "Clipboard is empty or doesn't contain text."

            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                return "Clipboard is empty or doesn't contain text."
            try:
                text = ctypes.wstring_at(pointer)
            finally:
                kernel32.GlobalUnlock(handle)

            return text if text else "Clipboard is empty or doesn't contain text."
        finally:
            user32.CloseClipboard()
    except Exception as e:
        return f"Couldn't read the clipboard: {e}"


def set_clipboard(text: str) -> str:
    """Sets the clipboard's text content to `text` and returns a short confirmation message."""
    try:
        text = text or ""

        if not _open_clipboard():
            return "Couldn't access the clipboard right now — it's busy in another app."

        h_mem = None
        try:
            user32.EmptyClipboard()

            data = text.encode("utf-16-le") + b"\x00\x00"  # CF_UNICODETEXT wants a null terminator
            size = len(data)

            h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
            if not h_mem:
                return "Couldn't copy to clipboard: out of memory."

            pointer = kernel32.GlobalLock(h_mem)
            if not pointer:
                return "Couldn't copy to clipboard."
            ctypes.memmove(pointer, data, size)
            kernel32.GlobalUnlock(h_mem)

            if not user32.SetClipboardData(CF_UNICODETEXT, h_mem):
                return "Couldn't copy to clipboard."

            h_mem = None  # ownership transferred to the system on success — must not be freed
            if not text:
                return "Cleared the clipboard."
            preview = text if len(text) <= 60 else text[:57] + "..."
            return f'Copied to clipboard: "{preview}"'
        finally:
            if h_mem:
                kernel32.GlobalFree(h_mem)
            user32.CloseClipboard()
    except Exception as e:
        return f"Couldn't copy to clipboard: {e}"
