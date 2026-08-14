"""Listens on the microphone for a double-clap and wakes Jarvis by opening the browser UI.

Run this alongside server.py (see SETUP.md for running it at login via Task Scheduler).
"""
import json
import time
import webbrowser
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

SAMPLE_RATE = 16000
BLOCK_MS = 30
BLOCK_SIZE = int(SAMPLE_RATE * BLOCK_MS / 1000)

CLAP_THRESHOLD = config.get("clap_threshold", 0.35)   # RMS amplitude (0-1) counted as a clap
CLAP_REFRACTORY_S = 0.25                               # ignore new claps for this long after one is detected
DOUBLE_CLAP_WINDOW_S = 1.2                              # max gap between the two claps
COOLDOWN_AFTER_WAKE_S = 4.0                             # ignore everything right after waking

last_clap_time = 0.0
clap_times = []
last_wake_time = 0.0


def on_wake():
    print("[clap_trigger] Double clap detected — waking Jarvis.")
    if focus_jarvis_window():
        print("[clap_trigger] Found an open Jarvis window, brought it to the front.")
        try:
            requests.post(f"http://{HOST}:{PORT}/trigger-wake", timeout=5)
        except requests.RequestException as e:
            print(f"[clap_trigger] Focused the window but couldn't nudge it to listen: {e}")
    else:
        webbrowser.open(WAKE_URL)


def audio_callback(indata, frames, time_info, status):
    global last_clap_time, last_wake_time, clap_times

    now = time.monotonic()
    if now - last_wake_time < COOLDOWN_AFTER_WAKE_S:
        return

    rms = float(np.sqrt(np.mean(np.square(indata))))
    if rms < CLAP_THRESHOLD:
        return
    if now - last_clap_time < CLAP_REFRACTORY_S:
        return

    last_clap_time = now
    clap_times = [t for t in clap_times if now - t < DOUBLE_CLAP_WINDOW_S]
    clap_times.append(now)

    if len(clap_times) >= 2:
        clap_times = []
        last_wake_time = now
        on_wake()


def main():
    print(f"[clap_trigger] Listening for a double clap. Will open {WAKE_URL}")
    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        blocksize=BLOCK_SIZE,
        channels=1,
        dtype="float32",
        callback=audio_callback,
    ):
        while True:
            time.sleep(1)


if __name__ == "__main__":
    main()
