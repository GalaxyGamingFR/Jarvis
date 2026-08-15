"""Windows media/volume/now-playing/power control.

dispatch_tool() in tools.py runs on a ThreadPoolExecutor worker thread (server.py's
loop.run_in_executor), not the main thread — every pycaw (COM-based) call here must initialize COM
on that thread itself, since COM state isn't shared across threads.
"""
import ctypes
import threading
import time

import comtypes
from comtypes import CLSCTX_ALL, cast, POINTER
from ctypes import windll
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

MEDIA_CONTROL_SCHEMA = {
    "name": "media_control",
    "description": "Control whatever media is currently playing (Spotify, a browser video, etc.) — play/pause, skip to next track, or go back to the previous track.",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["play_pause", "next", "previous"],
                "description": "Which media transport action to perform.",
            }
        },
        "required": ["action"],
    },
}

SET_VOLUME_SCHEMA = {
    "name": "set_volume",
    "description": "Adjust or mute the system volume.",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["set", "up", "down", "mute", "unmute"],
                "description": "'set' requires level; 'up'/'down' step by 10%; 'mute'/'unmute' toggle muting.",
            },
            "level": {
                "type": "integer",
                "description": "Target volume 0-100. Only used with action='set'.",
            },
        },
        "required": ["action"],
    },
}

GET_NOW_PLAYING_SCHEMA = {
    "name": "get_now_playing",
    "description": "Get the title, artist, and play/pause state of whatever media is currently active (Spotify, browser video, etc.). Use for 'what's playing' / 'what song is this'.",
    "input_schema": {"type": "object", "properties": {}},
}

SYSTEM_POWER_SCHEMA = {
    "name": "system_power",
    "description": "Lock the workstation or put the PC to sleep. There is no shutdown or restart action — those are intentionally not exposed.",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["lock", "sleep"],
                "description": "'lock' locks the workstation immediately; 'sleep' suspends the PC after a short delay.",
            }
        },
        "required": ["action"],
    },
}

# Virtual key codes for the system media keys, sent via keybd_event so they act system-wide —
# whatever app currently owns media focus receives them, no per-app integration needed.
_VK_MEDIA_PLAY_PAUSE = 0xB3
_VK_MEDIA_NEXT_TRACK = 0xB0
_VK_MEDIA_PREV_TRACK = 0xB1
_KEYEVENTF_EXTENDEDKEY = 0x0001
_KEYEVENTF_KEYUP = 0x0002


def _press_media_key(vk: int) -> None:
    windll.user32.keybd_event(vk, 0, _KEYEVENTF_EXTENDEDKEY, 0)
    windll.user32.keybd_event(vk, 0, _KEYEVENTF_EXTENDEDKEY | _KEYEVENTF_KEYUP, 0)


def media_control(action: str) -> str:
    vk = {
        "play_pause": _VK_MEDIA_PLAY_PAUSE,
        "next": _VK_MEDIA_NEXT_TRACK,
        "previous": _VK_MEDIA_PREV_TRACK,
    }.get(action)
    if vk is None:
        return f"Unknown media action: {action}"
    try:
        _press_media_key(vk)
    except OSError as e:
        return f"Couldn't send the media key: {e}"
    return {
        "play_pause": "Toggled play/pause.",
        "next": "Skipped to the next track.",
        "previous": "Went back to the previous track.",
    }[action]


def set_volume(action: str, level: int | None = None) -> str:
    comtypes.CoInitialize()
    try:
        devices = AudioUtilities.GetSpeakers()
        if devices is None:
            return "Couldn't find a default playback device."
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))

        if action == "mute":
            volume.SetMute(1, None)
            return "Muted."
        if action == "unmute":
            volume.SetMute(0, None)
            return "Unmuted."

        current = volume.GetMasterVolumeLevelScalar()
        if action == "set":
            if level is None:
                return "No level given for 'set'."
            target = max(0, min(100, level))
        elif action == "up":
            target = max(0, min(100, round(current * 100) + 10))
        elif action == "down":
            target = max(0, min(100, round(current * 100) - 10))
        else:
            return f"Unknown volume action: {action}"

        volume.SetMasterVolumeLevelScalar(target / 100, None)
        # Adjusting the level should audibly take effect — clear any pre-existing mute, otherwise
        # "turn it up" silently succeeds at the API level while staying inaudible.
        volume.SetMute(0, None)
        return f"Volume set to {target}%."
    except Exception as e:
        return f"Couldn't change the volume: {e}"
    finally:
        comtypes.CoUninitialize()


def get_now_playing() -> str:
    import asyncio

    from winsdk.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as SessionManager,
    )

    async def _query() -> str:
        manager = await SessionManager.request_async()
        session = manager.get_current_session()
        if session is None:
            return "Nothing is playing right now."

        info = await session.try_get_media_properties_async()
        playback = session.get_playback_info()
        status = playback.playback_status
        state = "Playing" if status == status.__class__.PLAYING else "Paused" if status == status.__class__.PAUSED else str(status)

        title = (info.title or "").strip() or "Unknown title"
        artist = (info.artist or "").strip()
        artist_part = f" by {artist}" if artist else ""
        return f"{state}: {title}{artist_part}."

    try:
        return asyncio.run(_query())
    except Exception as e:
        return f"Couldn't read what's playing: {e}"


def system_power(action: str) -> str:
    if action == "lock":
        try:
            windll.user32.LockWorkStation()
        except OSError as e:
            return f"Couldn't lock the workstation: {e}"
        return "Locking the workstation."

    if action == "sleep":
        def _sleep_after_delay():
            time.sleep(4)  # gives Jarvis's spoken response time to finish playing before suspend
            try:
                windll.powrprof.SetSuspendState(0, 1, 0)
            except OSError as e:
                # Runs detached on a daemon thread with no caller left to report to — and when
                # launched via scripts/launch-session.ps1 (hidden window, no output redirection),
                # a bare print() here would vanish into an unobservable console. flush=True at least
                # makes it show up if this is ever run attached to a visible terminal.
                print(f"[system_control] sleep failed: {e}", flush=True)

        threading.Thread(target=_sleep_after_delay, daemon=True).start()
        return "Putting the PC to sleep in a moment."

    return f"Unknown power action: {action}"
