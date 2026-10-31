"""Bisik - tekan fn 2x untuk dikte suara ke teks, lokal dan offline."""

import os
import queue
import re
import subprocess
import threading
import time
import traceback
from functools import partial

import mlx_whisper
import numpy as np
import Quartz
import sounddevice as sd

MODEL = os.environ.get("BISIK_MODEL", "mlx-community/whisper-large-v3-turbo")
LANGUAGE = os.environ.get("BISIK_LANGUAGE") or None  # None = auto-detect (id/en)
SAMPLE_RATE = 16000
DOUBLE_TAP_WINDOW = 0.4
START_SOUND_SECONDS = 0.6  # the Tink that confirms recording plays through the speakers, so the microphone hears it
FN_MASK = Quartz.kCGEventFlagMaskSecondaryFn
KEYCODE_V = 9
STOP_TIMEOUT = 2.0  # CoreAudio can deadlock while closing a stream, so never wait longer than this
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
    "selamat menikmati",
    "sampai jumpa",
    "sampai jumpa lagi",
    "sampai jumpa di video berikutnya",
    "jangan lupa subscribe",
    "please subscribe",
    "see you next time",
    "see you in the next video",
}
MAX_CLOSING_WORDS = 8
TRAILING_QUIET_RATIO = 0.5  # a closing phrase this much quieter than the speech before it was not spoken

chunks = []
stream = None
recording = False
fn_down = False
last_press = 0.0
ignore_audio_until = 0.0
lock = threading.Lock()
model_ready = threading.Event()
# Starting and stopping the microphone must never wait for the model, so they run on separate threads.
jobs = queue.Queue()  # audio jobs
model_jobs = queue.Queue()  # warm-up and transcription; MLX streams are bound to the thread that created them


def log(message):
    print(f"{time.strftime('%H:%M:%S')} {message}", flush=True)


def play(name):
    subprocess.Popen(["afplay", f"/System/Library/Sounds/{name}.aiff"])


def hud(state):
    """Tell Bisik.app which overlay to show (recording, transcribing, idle)."""
    if os.environ.get("BISIK_HUD"):
        print(f"@state:{state}", flush=True)


def on_audio(indata, frames, time_info, status):
    if time.monotonic() < ignore_audio_until:
        return
    chunks.append(indata.copy())


def start_recording():
    global stream, recording, ignore_audio_until
    chunks.clear()
    try:
        stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=on_audio)
        stream.start()
    except Exception:
        recording = False
        raise
    ignore_audio_until = time.monotonic() + START_SOUND_SECONDS
    play("Tink")
    hud("recording")
    if not model_ready.is_set():
        log("Model masih dimuat, hasil muncul setelah model siap.")
    log("Merekam... (tekan fn sekali untuk berhenti)")


def close_stream(old):
    try:
        old.stop()
        old.close()
    except Exception:
        traceback.print_exc()


def stop_recording():
    global stream
    if stream is None:
        return  # start_recording failed, so there is nothing to stop
    closer = threading.Thread(target=close_stream, args=(stream,), daemon=True)
    stream = None
    closer.start()
    closer.join(STOP_TIMEOUT)
    if closer.is_alive():
        log("Menutup mikrofon macet, lanjut memproses audio yang sudah terekam.")
    play("Pop")
    hud("transcribing")
    log("Berhenti merekam, memproses...")
    audio = np.concatenate(chunks).flatten() if chunks else np.zeros(0, dtype="float32")
    model_jobs.put(partial(transcribe_and_paste, audio))


def normalize(text):
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


def is_hallucination(audio, text):
    """True when quiet audio produced one of Whisper's stock phrases instead of real speech."""
    return rms(audio) < QUIET_RMS and normalize(text) in HALLUCINATIONS


def rms(audio):
    return float(np.sqrt(np.mean(np.square(audio)))) if len(audio) else 0.0


def strip_trailing_hallucination(audio, result):
    """Remove a stock closing phrase Whisper appended after the speech, judged by how quiet its audio is."""
    text = result["text"].strip()
    words = [w for segment in result.get("segments") or [] for w in segment.get("words") or []]
    for count in range(min(len(words) - 1, MAX_CLOSING_WORDS), 0, -1):
        tail = "".join(w["word"] for w in words[-count:])
        if normalize(tail) not in HALLUCINATIONS:
            continue
        start = int(words[-count]["start"] * SAMPLE_RATE)
        speech = rms(audio[:start])
        if rms(audio[start:]) < max(QUIET_RMS, TRAILING_QUIET_RATIO * speech) and text.endswith(tail.strip()):
            log(f"Frasa penutup dibuang: {tail.strip()}")
            return text[: len(text) - len(tail.strip())].strip()
    return text


def transcribe_and_paste(audio):
    try:
        if len(audio) < SAMPLE_RATE * 0.3:
            log("Terlalu pendek, diabaikan.")
            return
        if np.abs(audio).max() < SILENCE_PEAK:
            log("Tidak ada suara terdeteksi.")
            return
        result = mlx_whisper.transcribe(audio, path_or_hf_repo=MODEL, language=LANGUAGE, word_timestamps=True)
        text = result["text"].strip()
        if not text or is_hallucination(audio, text):
            log("Tidak ada suara terdeteksi.")
            return
        text = strip_trailing_hallucination(audio, result)
        log(f"[{result.get('language')}] {text}")
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
    log("fn ditekan")
    with lock:
        if recording:
            recording = False
            jobs.put(stop_recording)
            last_press = 0.0  # a quick second tap must not turn the stop into a double tap that restarts
            return
        elif now - last_press <= DOUBLE_TAP_WINDOW:
            recording = True
            jobs.put(start_recording)
            last_press = 0.0
            return
        last_press = now


def worker(job_queue):
    """Run the jobs of one queue in order on this thread."""
    while True:
        job = job_queue.get()
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
    model_ready.set()
    log("Model siap. Tekan fn 2x untuk mulai merekam.")


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
    log(f"Memuat model {MODEL} ...")
    for job_queue in (jobs, model_jobs):
        threading.Thread(target=worker, args=(job_queue,), daemon=True).start()
    model_jobs.put(warm_up)
    source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
    Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetCurrent(), source, Quartz.kCFRunLoopCommonModes)
    Quartz.CGEventTapEnable(tap, True)
    log("Pemantau tombol fn aktif.")
    Quartz.CFRunLoopRun()


if __name__ == "__main__":
    main()
