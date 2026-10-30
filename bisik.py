"""Bisik - tekan fn 2x untuk dikte suara ke teks, lokal dan offline."""

import os
import subprocess
import threading
import time

import mlx_whisper
import numpy as np
import sounddevice as sd
import Quartz

MODEL = os.environ.get("BISIK_MODEL", "mlx-community/whisper-large-v3-turbo")
LANGUAGE = os.environ.get("BISIK_LANGUAGE") or None  # None = auto-detect (id/en)
SAMPLE_RATE = 16000
DOUBLE_TAP_WINDOW = 0.4
FN_MASK = Quartz.kCGEventFlagMaskSecondaryFn
KEYCODE_V = 9

chunks = []
stream = None
recording = False
fn_down = False
last_press = 0.0
lock = threading.Lock()


def play(name):
    subprocess.Popen(["afplay", f"/System/Library/Sounds/{name}.aiff"])


def on_audio(indata, frames, time_info, status):
    chunks.append(indata.copy())


def start_recording():
    global stream, recording
    chunks.clear()
    stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=on_audio)
    stream.start()
    recording = True
    play("Tink")
    print("Merekam... (tekan fn sekali untuk berhenti)")


def stop_recording():
    global stream, recording
    stream.stop()
    stream.close()
    recording = False
    play("Pop")
    audio = np.concatenate(chunks).flatten() if chunks else np.zeros(0, dtype="float32")
    threading.Thread(target=transcribe_and_paste, args=(audio,), daemon=True).start()


def transcribe_and_paste(audio):
    if len(audio) < SAMPLE_RATE * 0.3:
        print("Terlalu pendek, diabaikan.")
        return
    result = mlx_whisper.transcribe(audio, path_or_hf_repo=MODEL, language=LANGUAGE)
    text = result["text"].strip()
    if not text:
        print("Tidak ada suara terdeteksi.")
        return
    print(f"[{result.get('language')}] {text}")
    subprocess.run(["pbcopy"], input=text.encode(), check=True)
    paste()


def paste():
    for down in (True, False):
        event = Quartz.CGEventCreateKeyboardEvent(None, KEYCODE_V, down)
        Quartz.CGEventSetFlags(event, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def on_fn_press():
    global last_press
    now = time.monotonic()
    with lock:
        if recording:
            stop_recording()
        elif now - last_press <= DOUBLE_TAP_WINDOW:
            start_recording()
            last_press = 0.0
            return
        last_press = now


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
    print("Model siap. Tekan fn 2x untuk mulai merekam.")


tap = Quartz.CGEventTapCreate(
    Quartz.kCGSessionEventTap,
    Quartz.kCGHeadInsertEventTap,
    Quartz.kCGEventTapOptionListenOnly,
    Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged),
    tap_callback,
    None,
)


def main():
    if tap is None:
        raise SystemExit(
            "Gagal memantau tombol fn. Beri izin Accessibility dan Input Monitoring "
            "untuk Terminal di System Settings > Privacy & Security."
        )
    print(f"Memuat model {MODEL} ...")
    threading.Thread(target=warm_up, daemon=True).start()
    source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
    Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetCurrent(), source, Quartz.kCFRunLoopCommonModes)
    Quartz.CGEventTapEnable(tap, True)
    Quartz.CFRunLoopRun()


if __name__ == "__main__":
    main()
