"""Listens on the microphone for a double-clap and wakes Jarvis by opening the browser UI.

Run this alongside server.py (see SETUP.md for running it at login via Task Scheduler).
"""
import json
import os
import time
from pathlib import Path

import numpy as np
import requests
import sounddevice as sd

from window_focus import focus_jarvis_window

CONFIG_PATH = Path(__file__).parent / "config.json"
config = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}

HOST = config.get("server_host", "127.0.0.1")
PORT = config.get("server_port", 8420)
WAKE_URL = f"http://{HOST}:{PORT}/?autolisten=1"

BLOCK_MS = 30  # actual samplerate/blocksize are picked per-device in main() — see note there

CLAP_THRESHOLD = config.get("clap_threshold", 0.2)    # RMS amplitude (0-1) counted as a clap
CLAP_REFRACTORY_S = 0.25                               # ignore new claps for this long after one is detected
DOUBLE_CLAP_WINDOW_S = 1.2                              # max gap between the two claps
COOLDOWN_AFTER_WAKE_S = 4.0                             # ignore everything right after waking

# Known virtual/loopback "microphones" that will never pick up a real clap — skip these when
# auto-picking an input device, even if Windows has one set as the system default.
_VIRTUAL_MIC_MARKERS = ("steam streaming", "stereo mix", "sound mapper", "primary sound capture")

DEBUG = os.environ.get("CLAP_DEBUG") == "1"  # set CLAP_DEBUG=1 to print live RMS levels for tuning

last_clap_time = 0.0
clap_times = []
last_wake_time = 0.0


def pick_input_device():
    """Returns a sounddevice device index/name to use, preferring a configured `mic_device` (name
    substring match), and otherwise steering away from known-virtual "microphones" that the system
    default sometimes points at (e.g. Steam's streaming mic, which is silent unless Steam is actively
    streaming to a receiving device — a real source of "clapping does nothing" bug reports)."""
    devices = sd.query_devices()

    configured = config.get("mic_device")
    if configured:
        for i, d in enumerate(devices):
            if d["max_input_channels"] > 0 and configured.lower() in d["name"].lower():
                return i
        print(f"[clap_trigger] Configured mic_device '{configured}' not found — auto-picking instead.", flush=True)

    default_idx = sd.default.device[0]
    default_name = devices[default_idx]["name"] if default_idx is not None and default_idx >= 0 else ""

    if any(marker in default_name.lower() for marker in _VIRTUAL_MIC_MARKERS):
        for i, d in enumerate(devices):
            name_lower = d["name"].lower()
            if d["max_input_channels"] > 0 and not any(m in name_lower for m in _VIRTUAL_MIC_MARKERS):
                print(
                    f"[clap_trigger] System default mic '{default_name}' looks virtual/silent — "
                    f"using '{d['name']}' instead. Set \"mic_device\" in config.json to override.",
                    flush=True,
                )
                return i

    return default_idx


def on_wake(reason="Wake triggered", tag="clap_trigger"):
    """Shared by clap_trigger.py and wake_word_trigger.py: find and focus an already-open Jarvis tab
    (nudging it to start listening), or open a new one if none is found.

    Skips entirely if a session is already active anywhere — otherwise Jarvis's own voice coming back
    through the mic can re-trigger the wake word mid-response, layering a duplicate greeting on top of
    whatever it's already saying (a classic smart-speaker feedback loop)."""
    try:
        resp = requests.get(f"http://{HOST}:{PORT}/session-active", timeout=2)
        if resp.json().get("active", False):
            print(f"[{tag}] Ignored — a Jarvis session is already active.", flush=True)
            return
    except requests.RequestException:
        pass  # if we can't check, don't block waking — a missed check shouldn't cost the whole feature

    print(f"[{tag}] {reason} — waking Jarvis.", flush=True)
    if focus_jarvis_window():
        print(f"[{tag}] Found an open Jarvis window, brought it to the front.", flush=True)
        try:
            requests.post(f"http://{HOST}:{PORT}/trigger-wake", timeout=5)
        except requests.RequestException as e:
            print(f"[{tag}] Focused the window but couldn't nudge it to listen: {e}", flush=True)
    else:
        # microsoft-edge: is a protocol Windows/Edge registers specifically to open a URL in Edge —
        # bypasses whatever the OS default browser is set to (webbrowser.open() would respect that
        # default, which on this machine is Opera GX, not what we want for a dedicated Jarvis window).
        os.startfile(f"microsoft-edge:{WAKE_URL}")


def audio_callback(indata, frames, time_info, status):
    global last_clap_time, last_wake_time, clap_times

    now = time.monotonic()
    rms = float(np.sqrt(np.mean(np.square(indata))))

    if DEBUG and rms > 0.02:
        print(f"[clap_trigger] rms={rms:.3f}", flush=True)

    if now - last_wake_time < COOLDOWN_AFTER_WAKE_S:
        return
    if rms < CLAP_THRESHOLD:
        return
    if now - last_clap_time < CLAP_REFRACTORY_S:
        return

    last_clap_time = now
    clap_times = [t for t in clap_times if now - t < DOUBLE_CLAP_WINDOW_S]
    clap_times.append(now)
    print(f"[clap_trigger] Clap {len(clap_times)} detected (rms={rms:.3f}).", flush=True)

    if len(clap_times) >= 2:
        clap_times = []
        last_wake_time = now
        on_wake("Double clap detected")


def main():
    device = pick_input_device()
    device_info = sd.query_devices(device)
    device_name = device_info["name"]
    # Use the device's own native samplerate rather than forcing one — some drivers silently produce
    # near-silent audio when opened at a samplerate they don't natively support (observed: an AMD
    # mic array that works fine at its native 44100 Hz went dead at a forced 16000 Hz). RMS-based clap
    # detection doesn't care what the exact rate is, so there's no reason to force one.
    samplerate = int(device_info["default_samplerate"])
    block_size = int(samplerate * BLOCK_MS / 1000)
    print(
        f"[clap_trigger] Listening on '{device_name}' ({samplerate} Hz) for a double clap. "
        f"Will open {WAKE_URL}",
        flush=True,
    )
    with sd.InputStream(
        samplerate=samplerate,
        blocksize=block_size,
        channels=1,
        dtype="float32",
        device=device,
        callback=audio_callback,
    ):
        while True:
            time.sleep(1)


if __name__ == "__main__":
    main()
