"""Bisik - tekan fn 2x untuk dikte suara ke teks, lokal dan offline."""

import os
import queue
import re
import subprocess
import sys
import threading
import time
import traceback
from functools import partial

import mlx_whisper
import numpy as np
import Quartz

MODEL = os.environ.get("BISIK_MODEL", "mlx-community/whisper-large-v3-turbo")
LANGUAGE = os.environ.get("BISIK_LANGUAGE") or None  # None = auto-detect (id/en)
SAMPLE_RATE = 16000
DOUBLE_TAP_WINDOW = 0.4
START_SOUND_SECONDS = 0.6  # the Tink that confirms recording plays through the speakers, so the microphone hears it
FN_MASK = Quartz.kCGEventFlagMaskSecondaryFn
KEYCODE_V = 9
START_TIMEOUT = 3.0  # CoreAudio can deadlock while opening or closing a stream, so never wait longer than this
STOP_TIMEOUT = 2.0
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
STATIONARY_CV = 0.2  # measured: fan or hiss varies under 0.11 between 100 ms frames, even whispered speech over 0.3
LOW_CONFIDENCE_LOGPROB = -1.0  # measured: real speech scores above -0.4, invented text from noise below -3
MAX_COMPRESSION_RATIO = 2.4  # a segment this repetitive is a decoding loop such as "Tick the Tick the"
KEEP_WARM_SECONDS = 120  # touch the model while idle so macOS keeps its weights out of swap
TRANSCRIBE_TIMEOUT = 120  # plus one second per second of audio; a stuck model restarts the engine
MAX_CLOSING_WORDS = 8
TRAILING_QUIET_RATIO = 0.5  # a closing phrase this much quieter than the speech before it was not spoken

chunks = []
recorder = None
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


def on_audio(samples):
    if time.monotonic() < ignore_audio_until:
        return
    chunks.append(samples.copy())


# The microphone is read by a child process because CoreAudio sometimes deadlocks inside open or close.
# A thread stuck there can never be stopped and takes every later recording with it, a child can simply be killed.
RECORDER_CODE = """
import os, signal, sys, time
import sounddevice as sd

done = False


def stop(*_):
    global done
    done = True


signal.signal(signal.SIGTERM, stop)
out = sys.stdout.buffer


def write(indata, frames, time_info, status):
    out.write(indata.tobytes())
    out.flush()


stream = sd.InputStream(samplerate=int(sys.argv[1]), channels=1, dtype="float32", callback=write)
stream.start()
while not done and os.getppid() != 1:
    time.sleep(0.05)
stream.stop()
stream.close()
"""


class Recorder:
    """Records the microphone in a child process and passes its float32 samples to on_audio."""

    def __init__(self):
        self.process = subprocess.Popen(
            [sys.executable, "-c", RECORDER_CODE, str(SAMPLE_RATE)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.receiving = threading.Event()  # set by the first samples, or by the child exiting early
        self.delivered = False
        self.stopping = False
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()

    def read(self):
        leftover = b""
        while True:
            data = self.process.stdout.read1(65536)
            self.receiving.set()
            if not data:
                if self.delivered and not self.stopping:
                    on_recorder_lost(self)
                return
            self.delivered = True
            data = leftover + data
            usable = len(data) // 4 * 4
            leftover = data[usable:]
            on_audio(np.frombuffer(data[:usable], dtype="float32"))

    def wait_until_recording(self):
        """Raise when the microphone could not be opened, e.g. permission denied or CoreAudio stuck."""
        if not self.receiving.wait(START_TIMEOUT):
            self.kill()
            raise RuntimeError("Mikrofon tidak merespons, rekaman dibatalkan.")
        if self.process.poll() is not None:
            reason = self.process.stderr.read().decode(errors="replace").strip().splitlines()
            raise RuntimeError(f"Mikrofon gagal dibuka: {reason[-1] if reason else self.process.returncode}")

    def kill(self):
        self.process.kill()
        self.process.wait()

    def stop(self):
        """Stop recording and return True when the child had to be killed because it did not exit."""
        self.stopping = True
        self.process.terminate()
        hung = False
        try:
            self.process.wait(STOP_TIMEOUT)
        except subprocess.TimeoutExpired:
            self.kill()
            hung = True
        self.reader.join(STOP_TIMEOUT)
        return hung


def on_recorder_lost(lost):
    """The microphone process died mid-recording, e.g. the device was unplugged."""
    global recording
    with lock:
        if lost is not recorder or not recording:
            return
        recording = False
    log("Mikrofon berhenti tiba-tiba, rekaman dibatalkan.")
    hud("idle")


def start_recording():
    global recorder, recording, ignore_audio_until
    if recorder is not None:
        recorder.stop()  # reap the process of a recording that ended by itself
        recorder = None
    chunks.clear()
    try:
        recorder = Recorder()
        recorder.wait_until_recording()
    except Exception:
        recorder = None
        recording = False
        raise
    ignore_audio_until = time.monotonic() + START_SOUND_SECONDS
    play("Tink")
    hud("recording")
    if not model_ready.is_set():
        log("Model masih dimuat, hasil muncul setelah model siap.")
    log("Merekam... (tekan fn sekali untuk berhenti)")


def stop_recording():
    global recorder
    if recorder is None:
        return  # start_recording failed, so there is nothing to stop
    finished, recorder = recorder, None
    if finished.stop():
        log("Menutup mikrofon macet, rekaman dihentikan paksa dan audio yang sudah terekam diproses.")
    play("Pop")
    hud("transcribing")
    log("Berhenti merekam, memproses...")
    audio = np.concatenate(chunks).flatten() if chunks else np.zeros(0, dtype="float32")
    model_jobs.put(partial(transcribe_and_paste, audio))


def normalize(text):
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


def is_stationary(audio, frame=SAMPLE_RATE // 10):
    """True for a steady hum or hiss, whose loudness hardly changes from one 100 ms frame to the next."""
    count = len(audio) // frame
    if count < 3:
        return False
    levels = np.sqrt(np.mean(np.square(audio[: count * frame].reshape(count, frame)), axis=1))
    return float(levels.mean()) > 0 and float(levels.std() / levels.mean()) < STATIONARY_CV


def is_hallucination(audio, text):
    """True when quiet or steady audio produced one of Whisper's stock phrases instead of real speech."""
    return (rms(audio) < QUIET_RMS or is_stationary(audio)) and normalize(text) in HALLUCINATIONS


def rms(audio):
    return float(np.sqrt(np.mean(np.square(audio)))) if len(audio) else 0.0


def ends_with_stock_phrase(text):
    spoken = normalize(text)
    return any(spoken == phrase or spoken.endswith(f" {phrase}") for phrase in HALLUCINATIONS)


def strip_trailing_hallucination(audio, result, text):
    """Remove a stock closing phrase Whisper appended after the speech, judged by how quiet its audio is."""
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


def confident_text(result):
    """The transcript without segments Whisper itself was unsure about, plus the dropped segments."""
    segments = result.get("segments") or []
    dropped = [
        segment
        for segment in segments
        if segment.get("avg_logprob", 0.0) < LOW_CONFIDENCE_LOGPROB
        or segment.get("compression_ratio", 0.0) > MAX_COMPRESSION_RATIO
    ]
    if not dropped:
        return result["text"].strip(), []
    kept = "".join(segment["text"] for segment in segments if segment not in dropped)
    return kept.strip(), dropped


def abort_stuck_transcription():
    # MLX work cannot be interrupted, so leave and let Bisik.app start a fresh engine.
    log("Transkripsi macet terlalu lama, Bisik dimulai ulang.")
    hud("idle")
    os._exit(75)


def transcribe_and_paste(audio):
    watchdog = threading.Timer(TRANSCRIBE_TIMEOUT + len(audio) / SAMPLE_RATE, abort_stuck_transcription)
    watchdog.daemon = True
    watchdog.start()
    try:
        if len(audio) < SAMPLE_RATE * 0.3:
            log("Terlalu pendek, diabaikan.")
            return
        if np.abs(audio).max() < SILENCE_PEAK:
            log("Tidak ada suara terdeteksi.")
            return
        started = time.monotonic()
        result = mlx_whisper.transcribe(audio, path_or_hf_repo=MODEL, language=LANGUAGE)
        log(f"Transkripsi {time.monotonic() - started:.1f} detik untuk audio {len(audio) / SAMPLE_RATE:.1f} detik.")
        text, dropped = confident_text(result)
        for segment in dropped:
            logprob = segment.get("avg_logprob", 0.0)
            log(f"Segmen berkeyakinan rendah dibuang (logprob {logprob:.1f}): {segment['text'].strip()}")
        if not text or is_hallucination(audio, text):
            log("Tidak ada suara terdeteksi.")
            return
        if ends_with_stock_phrase(text):
            # Word timings cost about 50% more decoding time, so only ask for them when a closing phrase may be fake.
            result = mlx_whisper.transcribe(audio, path_or_hf_repo=MODEL, language=LANGUAGE, word_timestamps=True)
            text = strip_trailing_hallucination(audio, result, text)
        log(f"[{result.get('language')}] {text}")
        subprocess.run(["pbcopy"], input=text.encode(), check=True)
        paste()
    finally:
        watchdog.cancel()
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


def touch_model():
    mlx_whisper.transcribe(np.zeros(SAMPLE_RATE, dtype="float32"), path_or_hf_repo=MODEL)


def keep_warm():
    """Queue a quick transcription of silence whenever the model has been idle for a while."""
    while True:
        time.sleep(KEEP_WARM_SECONDS)
        if model_ready.is_set() and model_jobs.empty() and not recording:
            model_jobs.put(touch_model)


def warm_up():
    touch_model()
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
    threading.Thread(target=keep_warm, daemon=True).start()
    source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
    Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetCurrent(), source, Quartz.kCFRunLoopCommonModes)
    Quartz.CGEventTapEnable(tap, True)
    log("Pemantau tombol fn aktif.")
    Quartz.CFRunLoopRun()


if __name__ == "__main__":
    main()
