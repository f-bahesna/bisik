"""Bisik - tekan fn 2x untuk dikte suara ke teks, lokal dan offline."""

import os
import queue
import re
import subprocess
import threading
import time
import traceback

import mlx_whisper
import numpy as np
import Quartz
import sounddevice as sd

MODEL = os.environ.get("BISIK_MODEL", "mlx-community/whisper-large-v3-turbo")
LANGUAGE = os.environ.get("BISIK_LANGUAGE") or None  # None = auto-detect (id/en)
SAMPLE_RATE = 16000
DOUBLE_TAP_WINDOW = 0.4
FN_MASK = Quartz.kCGEventFlagMaskSecondaryFn
KEYCODE_V = 9
SILENCE_PEAK = 0.002  # a quieter signal means the microphone delivered nothing at all
QUIET_RMS = 0.01  # below this level Whisper tends to invent a stock phrase
# Phrases Whisper writes for silence or room noise, compared after lowercasing and removing punctuation.
HALLUCINATIONS = {
    "thank you",
    "thank you for watching",
    "thanks for watching",
    "you",
    "bye",
    "terima kasih",
    "terima kasih telah menonton",
}

chunks = []
stream = None
recording = False
fn_down = False
last_press = 0.0
lock = threading.Lock()
jobs = queue.Queue()


def play(name):
    subprocess.Popen(["afplay", f"/System/Library/Sounds/{name}.aiff"])


def hud(state):
    """Tell Bisik.app which overlay to show (recording, transcribing, idle)."""
    if os.environ.get("BISIK_HUD"):
        print(f"@state:{state}", flush=True)


def on_audio(indata, frames, time_info, status):
    chunks.append(indata.copy())


def start_recording():
    global stream, recording
    chunks.clear()
    try:
        stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=on_audio)
        stream.start()
    except Exception:
        recording = False
        raise
    play("Tink")
    hud("recording")
    print("Merekam... (tekan fn sekali untuk berhenti)", flush=True)


def stop_recording():
    global stream
    if stream is None:
        return  # start_recording failed, so there is nothing to stop
    stream.stop()
    stream.close()
    stream = None
    play("Pop")
    hud("transcribing")
    print("Berhenti merekam, memproses...", flush=True)
    audio = np.concatenate(chunks).flatten() if chunks else np.zeros(0, dtype="float32")
    transcribe_and_paste(audio)


def normalize(text):
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


def is_hallucination(audio, text):
    """True when quiet audio produced one of Whisper's stock phrases instead of real speech."""
    rms = float(np.sqrt(np.mean(np.square(audio))))
    return rms < QUIET_RMS and normalize(text) in HALLUCINATIONS


def transcribe_and_paste(audio):
    try:
        if len(audio) < SAMPLE_RATE * 0.3:
            print("Terlalu pendek, diabaikan.")
            return
        if np.abs(audio).max() < SILENCE_PEAK:
            print("Tidak ada suara terdeteksi.")
            return
        result = mlx_whisper.transcribe(audio, path_or_hf_repo=MODEL, language=LANGUAGE)
        text = result["text"].strip()
        if not text or is_hallucination(audio, text):
            print("Tidak ada suara terdeteksi.")
            return
        print(f"[{result.get('language')}] {text}")
        subprocess.run(["pbcopy"], input=text.encode(), check=True)
        paste()
    finally:
        hud("idle")


def paste():
    for down in (True, False):
        event = Quartz.CGEventCreateKeyboardEvent(None, KEYCODE_V, down)
        Quartz.CGEventSetFlags(event, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def on_fn_press():
    """Runs inside the event tap callback, so it only queues work and returns at once."""
    global last_press, recording
    now = time.monotonic()
    print("fn ditekan", flush=True)
    with lock:
        if recording:
            recording = False
            jobs.put(stop_recording)
        elif now - last_press <= DOUBLE_TAP_WINDOW:
            recording = True
            jobs.put(start_recording)
            last_press = 0.0
            return
        last_press = now


def worker():
    """One thread runs every audio and model job, because MLX streams are bound to their thread."""
    while True:
        job = jobs.get()
        try:
            job()
        except Exception:
            traceback.print_exc()
            hud("idle")


def tap_callback(proxy, event_type, event, refcon):
    global fn_down
    if event_type in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
        Quartz.CGEventTapEnable(tap, True)
        return event
    pressed = bool(Quartz.CGEventGetFlags(event) & FN_MASK)
    if pressed and not fn_down:
        on_fn_press()
    fn_down = pressed
    return event


def warm_up():
    mlx_whisper.transcribe(np.zeros(SAMPLE_RATE, dtype="float32"), path_or_hf_repo=MODEL)
    print("Model siap. Tekan fn 2x untuk mulai merekam.", flush=True)


tap = Quartz.CGEventTapCreate(
    Quartz.kCGSessionEventTap,
    Quartz.kCGHeadInsertEventTap,
    Quartz.kCGEventTapOptionListenOnly,
    Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged),
    tap_callback,
    None,
)


def main():
    # Without Input Monitoring the tap is still created but never receives key events,
    # so exit and let Bisik.app retry until the permission is granted.
    if tap is None or not Quartz.CGPreflightListenEventAccess():
        raise SystemExit(
            "Gagal memantau tombol fn. Beri izin Accessibility dan Input Monitoring "
            "untuk Bisik (atau Terminal jika dijalankan manual) di System Settings > Privacy & Security."
        )
    print(f"Memuat model {MODEL} ...")
    threading.Thread(target=worker, daemon=True).start()
    jobs.put(warm_up)
    source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
    Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetCurrent(), source, Quartz.kCFRunLoopCommonModes)
    Quartz.CGEventTapEnable(tap, True)
    print("Pemantau tombol fn aktif.", flush=True)
    Quartz.CFRunLoopRun()


if __name__ == "__main__":
    main()
