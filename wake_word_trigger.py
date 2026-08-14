"""Listens continuously for "Hey Jarvis" (via openWakeWord, fully offline) and wakes Jarvis.

Run this instead of clap_trigger.py for voice-activated wake — same underlying behavior (finds and
focuses an already-open Jarvis tab, or opens a new one from nothing if none is found), just triggered
by saying "Hey Jarvis" instead of clapping.
"""
import os
import time

import numpy as np
import sounddevice as sd
from openwakeword.model import Model
from scipy.signal import resample_poly

from clap_trigger import config, on_wake, pick_input_device

CHUNK_MS = 80  # openWakeWord's models expect ~80ms (1280 samples @ 16kHz) chunks

WAKE_THRESHOLD = config.get("wake_word_threshold", 0.5)  # openWakeWord confidence (0-1) to trigger
COOLDOWN_AFTER_WAKE_S = 4.0  # ignore further detections for this long right after waking

DEBUG = os.environ.get("WAKE_DEBUG") == "1"  # set WAKE_DEBUG=1 to print live confidence scores

model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
last_wake_time = 0.0


def make_callback(native_sr: int):
    def callback(indata, frames, time_info, status):
        global last_wake_time

        now = time.monotonic()
        if now - last_wake_time < COOLDOWN_AFTER_WAKE_S:
            return

        mono = indata[:, 0]
        resampled = resample_poly(mono, 16000, native_sr)
        pcm16 = (resampled * 32767).astype(np.int16)
        prediction = model.predict(pcm16)
        score = prediction.get("hey_jarvis", 0.0)

        if DEBUG and score > 0.05:
            print(f"[wake_word_trigger] score={score:.3f}", flush=True)

        if score > WAKE_THRESHOLD:
            last_wake_time = now
            on_wake(f"'Hey Jarvis' detected (score={score:.2f})", tag="wake_word_trigger")

    return callback


def main():
    device = pick_input_device()
    device_info = sd.query_devices(device)
    native_sr = int(device_info["default_samplerate"])
    native_chunk = int(native_sr * CHUNK_MS / 1000)

    print(
        f"[wake_word_trigger] Listening on '{device_info['name']}' ({native_sr} Hz) for \"Hey Jarvis\".",
        flush=True,
    )
    with sd.InputStream(
        samplerate=native_sr,
        blocksize=native_chunk,
        channels=1,
        dtype="float32",
        device=device,
        callback=make_callback(native_sr),
    ):
        while True:
            time.sleep(1)


if __name__ == "__main__":
    main()
