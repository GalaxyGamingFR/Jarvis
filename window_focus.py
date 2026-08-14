"""Finds an already-open Jarvis browser window and brings it to the foreground (Windows only)."""
import ctypes
import re
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

SW_RESTORE = 9

# Our page's <title> is exactly "Jarvis", and browsers build the window title from the active tab's
# title plus their own chrome. Observed real formats: "Jarvis - Google Chrome", "Jarvis - Opera",
# "Jarvis and 7 more pages - Profile 1 - Microsoft Edge" (when many tabs are open). A plain "contains
# Jarvis" substring match is too loose — it also fires on unrelated windows like a GitHub repo tab
# ("GalaxyGamingFR/Jarvis - Opera") or an editor with a file open in this project
# (".env - Jarvis - Visual Studio Code", which even shares Chrome's window class since Electron apps
# report as "Chrome_WidgetWin_1" too, so class-filtering alone can't rule it out). Anchoring the match
# to the *start* of the title avoids both.
_TITLE_PATTERN = re.compile(r"^Jarvis($| - | and \d+ more pages)")


def _find_jarvis_tab_window() -> int | None:
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if _TITLE_PATTERN.match(buf.value):
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(callback, 0)
    return found[0] if found else None


def focus_jarvis_window() -> bool:
    """Finds the Jarvis tab (only when it's the active tab of its browser window — see
    `_find_jarvis_tab_window`) and brings that window to the front. Returns True if found and focused.

    Note: if the Jarvis tab is open but currently a *background* tab behind another tab, the window
    title won't say "Jarvis" and this will miss it, falling through to opening a new tab instead. Good
    enough for the common case of a dedicated Jarvis tab/window left open.
    """
    hwnd = _find_jarvis_tab_window()
    if hwnd is None:
        return False

    user32.ShowWindow(hwnd, SW_RESTORE)

    # SetForegroundWindow is blocked by Windows unless the caller already "owns" focus — temporarily
    # attaching input state to the current foreground thread is the standard workaround for this.
    foreground_hwnd = user32.GetForegroundWindow()
    foreground_thread = user32.GetWindowThreadProcessId(foreground_hwnd, None)
    current_thread = kernel32.GetCurrentThreadId()

    if foreground_thread and foreground_thread != current_thread:
        user32.AttachThreadInput(current_thread, foreground_thread, True)
        user32.SetForegroundWindow(hwnd)
        user32.AttachThreadInput(current_thread, foreground_thread, False)
    else:
        user32.SetForegroundWindow(hwnd)

    return True
