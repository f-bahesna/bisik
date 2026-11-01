import runpy
import subprocess
import sys
from unittest import mock

import numpy as np
import pytest

from tests.conftest import (
    COMMAND_MASK,
    FN_MASK,
    TAP_DISABLED_BY_TIMEOUT,
    TAP_DISABLED_BY_USER_INPUT,
    make_quartz,
)


@pytest.fixture(autouse=True)
def fake_recorder(bisik):
    """Replace the microphone child process, tests of the real Recorder restore bisik.RealRecorder."""
    bisik.RealRecorder = bisik.Recorder
    bisik.Recorder = mock.MagicMock(name="Recorder")
    bisik.Recorder.return_value.stop.return_value = False


@pytest.fixture
def hud_on(monkeypatch):
    monkeypatch.setenv("BISIK_HUD", "1")


def run_jobs(bisik):
    """Run every queued job in order, the way the two worker threads would."""
    for job_queue in (bisik.jobs, bisik.model_jobs):
        while not job_queue.empty():
            job_queue.get_nowait()()


class TestPlayAndHud:
    def test_play_runs_afplay_with_the_system_sound(self, bisik):
        with mock.patch.object(bisik.subprocess, "Popen") as popen:
            bisik.play("Tink")
        popen.assert_called_once_with(["afplay", "/System/Library/Sounds/Tink.aiff"])

    def test_hud_prints_state_marker_when_launched_by_the_app(self, bisik, hud_on, capsys):
        bisik.hud("recording")
        assert capsys.readouterr().out == "@state:recording\n"

    def test_hud_is_silent_when_run_manually(self, bisik, capsys):
        bisik.hud("recording")
        assert capsys.readouterr().out == ""


class TestAudioCapture:
    def test_on_audio_stores_a_copy_of_the_buffer(self, bisik):
        buffer = np.ones(4, dtype="float32")
        bisik.on_audio(buffer)
        buffer[:] = 0
        assert bisik.chunks[0].sum() == 4

    def test_start_recording_starts_the_microphone_process_and_waits_for_it(self, bisik, hud_on, capsys):
        with mock.patch.object(bisik, "play") as play:
            bisik.chunks.append(np.ones(3))
            bisik.start_recording()

        bisik.Recorder.assert_called_once_with()
        bisik.Recorder.return_value.wait_until_recording.assert_called_once()
        assert bisik.chunks == []
        assert bisik.recorder is bisik.Recorder.return_value
        play.assert_called_once_with("Tink")
        out = capsys.readouterr().out
        assert "@state:recording" in out
        assert "Merekam..." in out

    def test_the_start_sound_is_not_recorded(self, bisik, monkeypatch):
        """Regression: the Tink played by the speakers was transcribed as "Tick the Tick the ..."."""
        now = [100.0]
        monkeypatch.setattr(bisik.time, "monotonic", lambda: now[0])
        with mock.patch.object(bisik, "play"):
            bisik.start_recording()
        for offset in (0.0, 0.3, 0.59):
            now[0] = 100.0 + offset
            bisik.on_audio(np.ones(4, dtype="float32"))
        assert bisik.chunks == []
        now[0] = 100.61
        bisik.on_audio(np.ones(4, dtype="float32"))
        assert len(bisik.chunks) == 1

    def test_start_recording_failure_resets_the_recording_flag_and_raises(self, bisik):
        bisik.recording = True
        bisik.Recorder.return_value.wait_until_recording.side_effect = RuntimeError("microphone denied")
        with pytest.raises(RuntimeError, match="microphone denied"):
            bisik.start_recording()
        assert bisik.recording is False
        assert bisik.recorder is None

    def test_stop_recording_joins_chunks_and_transcribes(self, bisik, hud_on, capsys):
        bisik.recorder = bisik.Recorder.return_value
        bisik.chunks[:] = [np.ones(2, dtype="float32"), np.full(3, 2, dtype="float32")]

        with mock.patch.object(bisik, "play") as play:
            bisik.stop_recording()

        bisik.Recorder.return_value.stop.assert_called_once()
        assert bisik.recorder is None
        play.assert_called_once_with("Pop")
        job = bisik.model_jobs.get_nowait()
        assert job.func is bisik.transcribe_and_paste
        assert job.args[0].tolist() == [1, 1, 2, 2, 2]
        out = capsys.readouterr().out
        assert "@state:transcribing" in out
        assert "Berhenti merekam" in out

    def test_stop_recording_without_audio_sends_an_empty_array(self, bisik):
        bisik.recorder = bisik.Recorder.return_value
        with mock.patch.object(bisik, "play"):
            bisik.stop_recording()
        assert len(bisik.model_jobs.get_nowait().args[0]) == 0

    def test_a_microphone_process_that_had_to_be_killed_still_gets_its_audio_transcribed(self, bisik, capsys):
        """Regression: CoreAudio deadlocked on close and no later fn press worked."""
        bisik.recorder = bisik.Recorder.return_value
        bisik.Recorder.return_value.stop.return_value = True
        bisik.chunks[:] = [np.ones(16000, dtype="float32")]
        with mock.patch.object(bisik, "play"):
            bisik.stop_recording()
        assert bisik.recorder is None
        assert len(bisik.model_jobs.get_nowait().args[0]) == 16000
        assert "macet" in capsys.readouterr().out

    def test_stop_recording_does_nothing_when_no_recorder_was_started(self, bisik):
        bisik.recorder = None
        with mock.patch.object(bisik, "play") as play:
            bisik.stop_recording()
        assert bisik.model_jobs.empty()
        play.assert_not_called()


class TestRecorder:
    """The real Recorder against a fake child process."""

    @pytest.fixture
    def process(self, bisik):
        process = mock.MagicMock(name="process")
        process.poll.return_value = None
        with mock.patch.object(bisik.subprocess, "Popen", return_value=process) as popen:
            process.popen = popen
            yield process

    def test_samples_are_rebuilt_even_when_the_pipe_splits_a_float(self, bisik, process):
        data = np.arange(4, dtype="float32").tobytes()
        process.stdout.read1.side_effect = [data[:6], data[6:], b""]
        recorder = bisik.RealRecorder()
        recorder.reader.join(2)
        assert np.concatenate(bisik.chunks).tolist() == [0, 1, 2, 3]

    def test_the_child_records_at_the_whisper_sample_rate(self, bisik, process):
        process.stdout.read1.side_effect = [b""]
        bisik.RealRecorder().reader.join(2)
        command = process.popen.call_args.args[0]
        assert command[0] == bisik.sys.executable
        assert command[-1] == "16000"

    def test_a_child_that_exits_at_once_reports_why(self, bisik, process):
        process.stdout.read1.side_effect = [b""]
        process.poll.return_value = 1
        process.stderr.read.return_value = b"Traceback\nPortAudioError: permission denied\n"
        recorder = bisik.RealRecorder()
        with pytest.raises(RuntimeError, match="permission denied"):
            recorder.wait_until_recording()

    def test_a_child_that_never_delivers_audio_is_killed(self, bisik, process, monkeypatch):
        monkeypatch.setattr(bisik, "START_TIMEOUT", 0.05)
        release = bisik.threading.Event()
        process.stdout.read1.side_effect = lambda size: release.wait(2) and b""
        recorder = bisik.RealRecorder()
        try:
            with pytest.raises(RuntimeError, match="tidak merespons"):
                recorder.wait_until_recording()
            process.kill.assert_called_once()
        finally:
            release.set()

    def test_stop_asks_the_child_to_exit(self, bisik, process):
        process.stdout.read1.side_effect = [b""]
        recorder = bisik.RealRecorder()
        assert recorder.stop() is False
        process.terminate.assert_called_once()
        process.kill.assert_not_called()

    def test_stop_kills_a_child_that_ignores_the_request(self, bisik, process):
        process.stdout.read1.side_effect = [b""]
        process.wait.side_effect = [bisik.subprocess.TimeoutExpired("recorder", 2), 0]
        recorder = bisik.RealRecorder()
        assert recorder.stop() is True
        process.kill.assert_called_once()


class TestTranscribeAndPaste:
    SECOND = np.full(16000, 0.1, dtype="float32")  # one second of audible signal

    def test_audio_shorter_than_300ms_is_ignored(self, bisik, hud_on, capsys):
        bisik.transcribe_and_paste(np.zeros(4000, dtype="float32"))
        bisik.mlx_whisper.transcribe.assert_not_called()
        out = capsys.readouterr().out
        assert "Terlalu pendek" in out
        assert out.rstrip().endswith("@state:idle")

    def test_silence_is_reported_and_nothing_is_pasted(self, bisik, capsys):
        bisik.mlx_whisper.transcribe.return_value = {"text": "   ", "language": "id"}
        with mock.patch.object(bisik, "paste") as paste, mock.patch.object(bisik.subprocess, "run") as run:
            bisik.transcribe_and_paste(self.SECOND)
        paste.assert_not_called()
        run.assert_not_called()
        assert "Tidak ada suara terdeteksi" in capsys.readouterr().out

    def test_text_is_copied_to_the_clipboard_and_pasted(self, bisik, hud_on, capsys):
        bisik.mlx_whisper.transcribe.return_value = {"text": " Halo dunia \n", "language": "id"}
        with mock.patch.object(bisik, "paste") as paste, mock.patch.object(bisik.subprocess, "run") as run:
            bisik.transcribe_and_paste(self.SECOND)

        bisik.mlx_whisper.transcribe.assert_called_once_with(
            self.SECOND, path_or_hf_repo=bisik.MODEL, language=bisik.LANGUAGE
        )
        run.assert_called_once_with(["pbcopy"], input=b"Halo dunia", check=True)
        paste.assert_called_once()
        out = capsys.readouterr().out
        assert "[id] Halo dunia" in out
        assert out.rstrip().endswith("@state:idle")

    def test_overlay_returns_to_idle_when_the_clipboard_fails(self, bisik, hud_on, capsys):
        bisik.mlx_whisper.transcribe.return_value = {"text": "hello", "language": "en"}
        with mock.patch.object(bisik.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "pbcopy")):
            with pytest.raises(subprocess.CalledProcessError):
                bisik.transcribe_and_paste(self.SECOND)
        assert capsys.readouterr().out.rstrip().endswith("@state:idle")

    def test_overlay_returns_to_idle_when_the_model_fails(self, bisik, hud_on, capsys):
        bisik.mlx_whisper.transcribe.side_effect = RuntimeError("no stream")
        with pytest.raises(RuntimeError):
            bisik.transcribe_and_paste(self.SECOND)
        assert capsys.readouterr().out.rstrip().endswith("@state:idle")


class TestSilenceAndHallucinations:
    def audio(self, level):
        return np.full(16000, level, dtype="float32")

    def speech(self, level):
        """Loud and soft 100 ms frames alternating, the way syllables and pauses vary in real speech."""
        frames = np.where(np.arange(20) % 2 == 0, level, level / 5)
        return np.repeat(frames, 1600).astype("float32")

    def test_a_stock_phrase_from_loud_steady_noise_is_dropped(self, bisik, capsys):
        """Regression: a fan at normal loudness became "Thank you." and was pasted."""
        bisik.mlx_whisper.transcribe.return_value = {"text": "Thank you.", "language": "en"}
        noise = np.random.default_rng(0).normal(0, 0.02, 32000).astype("float32")
        with mock.patch.object(bisik, "paste") as paste, mock.patch.object(bisik.subprocess, "run") as run:
            bisik.transcribe_and_paste(noise)
        run.assert_not_called()
        paste.assert_not_called()

    def test_steady_and_varying_signals_are_told_apart(self, bisik):
        assert bisik.is_stationary(np.random.default_rng(1).normal(0, 0.03, 48000).astype("float32"))
        assert not bisik.is_stationary(self.speech(0.05))
        assert not bisik.is_stationary(np.ones(1000, dtype="float32"))  # too short to judge
        assert not bisik.is_stationary(np.zeros(32000, dtype="float32"))

    def test_a_dead_microphone_never_reaches_the_model(self, bisik, hud_on, capsys):
        bisik.transcribe_and_paste(self.audio(0.0))
        bisik.mlx_whisper.transcribe.assert_not_called()
        out = capsys.readouterr().out
        assert "Tidak ada suara terdeteksi" in out
        assert out.rstrip().endswith("@state:idle")

    def test_the_silence_gate_sits_just_below_a_whisper_of_signal(self, bisik):
        bisik.mlx_whisper.transcribe.return_value = {"text": "ok", "language": "en"}
        with mock.patch.object(bisik.subprocess, "run"), mock.patch.object(bisik, "paste"):
            bisik.transcribe_and_paste(self.audio(0.0019))
            bisik.mlx_whisper.transcribe.assert_not_called()
            bisik.transcribe_and_paste(self.audio(0.0021))
            bisik.mlx_whisper.transcribe.assert_called_once()

    @pytest.mark.parametrize(
        "phrase",
        [
            "Thank you.",
            " THANKS FOR WATCHING! ",
            "Thank you for watching.",
            "you",
            "Bye.",
            "Terima kasih.",
            "Terima kasih telah menonton",
        ],
    )
    def test_stock_phrases_from_quiet_audio_are_dropped(self, bisik, phrase, capsys):
        bisik.mlx_whisper.transcribe.return_value = {"text": phrase, "language": "en"}
        with mock.patch.object(bisik, "paste") as paste, mock.patch.object(bisik.subprocess, "run") as run:
            bisik.transcribe_and_paste(self.audio(0.005))
        run.assert_not_called()
        paste.assert_not_called()
        assert "Tidak ada suara terdeteksi" in capsys.readouterr().out

    def test_the_same_phrase_at_normal_volume_is_real_speech(self, bisik):
        bisik.mlx_whisper.transcribe.return_value = {"text": "Thank you.", "language": "en"}
        with mock.patch.object(bisik, "paste") as paste, mock.patch.object(bisik.subprocess, "run") as run:
            bisik.transcribe_and_paste(self.speech(0.05))
        run.assert_called_once_with(["pbcopy"], input=b"Thank you.", check=True)
        paste.assert_called_once()

    def test_quiet_speech_that_is_not_a_stock_phrase_is_kept(self, bisik):
        bisik.mlx_whisper.transcribe.return_value = {"text": "Buka jendelanya", "language": "id"}
        with mock.patch.object(bisik, "paste") as paste, mock.patch.object(bisik.subprocess, "run"):
            bisik.transcribe_and_paste(self.audio(0.005))
        paste.assert_called_once()

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Thank you.", "thank you"),
            ("  Hello,   World!  ", "hello   world"),
            ("Terima-kasih?", "terimakasih"),
            ("", ""),
        ],
    )
    def test_normalize_ignores_case_and_punctuation(self, bisik, text, expected):
        assert bisik.normalize(text) == expected


class TestTrailingHallucination:
    SPEECH = np.full(16000 * 3, 0.1, dtype="float32")
    QUIET_TAIL = np.full(16000 * 2, 0.004, dtype="float32")

    def result(self, closing):
        """A transcription whose last words start at second 3, where the quiet tail begins."""
        spoken = [{"word": " Tulis", "start": 0.0, "end": 1.0}, {"word": " ulang", "start": 1.0, "end": 2.0}]
        extra = [{"word": f" {w}", "start": 3.0 + i * 0.3, "end": 3.3 + i * 0.3} for i, w in enumerate(closing.split())]
        text = "Tulis ulang " + closing
        return {"text": text, "language": "id", "segments": [{"words": spoken + extra}]}

    def run(self, bisik, audio, result):
        bisik.mlx_whisper.transcribe.return_value = result
        with mock.patch.object(bisik, "paste"), mock.patch.object(bisik.subprocess, "run") as run:
            bisik.transcribe_and_paste(audio)
        return run

    @pytest.mark.parametrize("closing", ["Terima kasih telah menonton!", "selamat menikmati", "Thanks for watching."])
    def test_a_quiet_closing_phrase_is_removed(self, bisik, closing):
        run = self.run(bisik, np.concatenate([self.SPEECH, self.QUIET_TAIL]), self.result(closing))
        run.assert_called_once_with(["pbcopy"], input=b"Tulis ulang", check=True)

    def test_a_closing_phrase_spoken_at_the_same_volume_is_kept(self, bisik):
        loud_tail = np.full(16000 * 2, 0.1, dtype="float32")
        run = self.run(bisik, np.concatenate([self.SPEECH, loud_tail]), self.result("terima kasih"))
        run.assert_called_once_with(["pbcopy"], input=b"Tulis ulang terima kasih", check=True)

    def test_text_without_word_timings_is_left_alone(self, bisik):
        result = {"text": "Tulis ulang terima kasih telah menonton", "language": "id"}
        run = self.run(bisik, np.concatenate([self.SPEECH, self.QUIET_TAIL]), result)
        run.assert_called_once_with(["pbcopy"], input=b"Tulis ulang terima kasih telah menonton", check=True)

    def test_word_timings_are_only_requested_when_the_text_ends_with_a_stock_phrase(self, bisik):
        self.run(bisik, self.SPEECH, {"text": "Halo dunia", "language": "id"})
        bisik.mlx_whisper.transcribe.assert_called_once()
        assert "word_timestamps" not in bisik.mlx_whisper.transcribe.call_args.kwargs

        bisik.mlx_whisper.transcribe.reset_mock()
        self.run(bisik, np.concatenate([self.SPEECH, self.QUIET_TAIL]), self.result("terima kasih"))
        assert bisik.mlx_whisper.transcribe.call_count == 2
        assert bisik.mlx_whisper.transcribe.call_args.kwargs["word_timestamps"] is True

    @pytest.mark.parametrize(
        ("text", "expected"), [("Halo, terima kasih.", True), ("tahu", False), ("Bye", True), ("Selamat", False)]
    )
    def test_only_text_ending_in_a_whole_stock_phrase_triggers_the_second_pass(self, bisik, text, expected):
        assert bisik.ends_with_stock_phrase(text) is expected


class TestConfidence:
    SECOND = np.full(16000, 0.1, dtype="float32")

    def segment(self, text, logprob=-0.3, compression=1.0):
        return {"text": text, "avg_logprob": logprob, "compression_ratio": compression}

    def run(self, bisik, result, capsys):
        bisik.mlx_whisper.transcribe.return_value = result
        with mock.patch.object(bisik, "paste"), mock.patch.object(bisik.subprocess, "run") as run:
            bisik.transcribe_and_paste(self.SECOND)
        return run, capsys.readouterr().out

    def test_invented_text_from_noise_is_not_pasted(self, bisik, capsys):
        """Regression: noise became "Would not review today... Learn more from www.start13d..." after 53 seconds."""
        result = {
            "text": " Would not review today",
            "language": "en",
            "segments": [self.segment(" Would not review today", -3.6)],
        }
        run, out = self.run(bisik, result, capsys)
        run.assert_not_called()
        assert "berkeyakinan rendah" in out
        assert "Tidak ada suara terdeteksi" in out

    def test_only_the_unsure_segment_is_dropped(self, bisik, capsys):
        result = {
            "text": " Jalankan tesnya. Ithan.",
            "language": "id",
            "segments": [self.segment(" Jalankan tesnya."), self.segment(" Ithan.", -4.0)],
        }
        run, _ = self.run(bisik, result, capsys)
        run.assert_called_once_with(["pbcopy"], input=b"Jalankan tesnya.", check=True)

    def test_a_decoding_loop_is_dropped(self, bisik, capsys):
        result = {
            "text": " Tick the Tick the",
            "language": "en",
            "segments": [self.segment(" Tick the Tick the", -0.5, 3.1)],
        }
        run, _ = self.run(bisik, result, capsys)
        run.assert_not_called()

    def test_hard_but_real_speech_is_kept(self, bisik, capsys):
        result = {"text": " Halo dunia", "language": "id", "segments": [self.segment(" Halo dunia", -0.9, 2.0)]}
        run, _ = self.run(bisik, result, capsys)
        run.assert_called_once_with(["pbcopy"], input=b"Halo dunia", check=True)

    def test_the_log_reports_how_long_the_model_took(self, bisik, capsys):
        _, out = self.run(bisik, {"text": "Halo", "language": "id"}, capsys)
        assert "Transkripsi " in out and "detik untuk audio 1.0 detik" in out


class TestWatchdogAndKeepWarm:
    def test_a_stuck_transcription_restarts_the_engine(self, bisik, hud_on, capsys):
        with mock.patch.object(bisik.os, "_exit") as exit_:
            bisik.abort_stuck_transcription()
        exit_.assert_called_once_with(75)
        out = capsys.readouterr().out
        assert "macet" in out
        assert out.rstrip().endswith("@state:idle")

    def test_the_timeout_grows_with_the_length_of_the_audio(self, bisik):
        with mock.patch.object(bisik.threading, "Timer") as timer:
            bisik.transcribe_and_paste(np.zeros(16000 * 60, dtype="float32"))
        assert timer.call_args.args[0] == bisik.TRANSCRIBE_TIMEOUT + 60
        timer.return_value.start.assert_called_once()
        timer.return_value.cancel.assert_called_once()

    def test_a_finished_transcription_cancels_the_watchdog(self, bisik):
        bisik.mlx_whisper.transcribe.side_effect = RuntimeError("boom")
        with mock.patch.object(bisik.threading, "Timer") as timer, pytest.raises(RuntimeError):
            bisik.transcribe_and_paste(np.full(16000, 0.1, dtype="float32"))
        timer.return_value.cancel.assert_called_once()

    def run_keep_warm(self, bisik, monkeypatch):
        """Run one idle period of keep_warm, then stop the endless loop."""
        monkeypatch.setattr(bisik.time, "sleep", mock.Mock(side_effect=[None, StopIteration]))
        with pytest.raises(StopIteration):
            bisik.keep_warm()

    def test_an_idle_ready_model_is_touched(self, bisik, monkeypatch):
        bisik.model_ready.set()
        self.run_keep_warm(bisik, monkeypatch)
        assert bisik.model_jobs.get_nowait() is bisik.touch_model

    @pytest.mark.parametrize("state", ["loading", "busy", "recording"])
    def test_the_model_is_left_alone_while_it_is_not_idle(self, bisik, monkeypatch, state):
        if state != "loading":
            bisik.model_ready.set()
        if state == "busy":
            bisik.model_jobs.put(lambda: None)
        if state == "recording":
            bisik.recording = True
        self.run_keep_warm(bisik, monkeypatch)
        assert bisik.touch_model not in list(bisik.model_jobs.queue)


class TestMicrophoneLost:
    def test_a_microphone_that_dies_mid_recording_resets_the_overlay(self, bisik, hud_on, capsys):
        lost = bisik.Recorder.return_value
        bisik.recorder, bisik.recording = lost, True
        bisik.on_recorder_lost(lost)
        assert bisik.recording is False
        out = capsys.readouterr().out
        assert "berhenti tiba-tiba" in out
        assert out.rstrip().endswith("@state:idle")

    def test_an_old_recorder_ending_does_not_touch_the_current_recording(self, bisik, capsys):
        bisik.recorder, bisik.recording = bisik.Recorder.return_value, True
        bisik.on_recorder_lost(mock.MagicMock())
        assert bisik.recording is True
        assert capsys.readouterr().out == ""

    def test_the_next_recording_reaps_the_dead_recorder(self, bisik):
        dead = mock.MagicMock()
        bisik.recorder = dead
        with mock.patch.object(bisik, "play"):
            bisik.start_recording()
        dead.stop.assert_called_once()
        assert bisik.recorder is bisik.Recorder.return_value

    def test_the_reader_reports_a_child_that_ends_after_delivering_audio(self, bisik):
        process = mock.MagicMock()
        process.stdout.read1.side_effect = [np.zeros(4, dtype="float32").tobytes(), b""]
        with (
            mock.patch.object(bisik.subprocess, "Popen", return_value=process),
            mock.patch.object(bisik, "on_recorder_lost") as lost,
        ):
            recorder = bisik.RealRecorder()
            recorder.reader.join(2)
        lost.assert_called_once_with(recorder)

    def test_a_denied_microphone_is_not_reported_as_lost(self, bisik):
        process = mock.MagicMock()
        process.stdout.read1.side_effect = [b""]
        with (
            mock.patch.object(bisik.subprocess, "Popen", return_value=process),
            mock.patch.object(bisik, "on_recorder_lost") as lost,
        ):
            bisik.RealRecorder().reader.join(2)
        lost.assert_not_called()


class TestPaste:
    def test_paste_presses_and_releases_command_v(self, bisik):
        events = [mock.Mock(name="down"), mock.Mock(name="up")]
        bisik.Quartz.CGEventCreateKeyboardEvent.side_effect = events

        bisik.paste()

        assert bisik.Quartz.CGEventCreateKeyboardEvent.call_args_list == [
            mock.call(None, 9, True),
            mock.call(None, 9, False),
        ]
        assert bisik.Quartz.CGEventSetFlags.call_args_list == [mock.call(e, COMMAND_MASK) for e in events]
        assert bisik.Quartz.CGEventPost.call_args_list == [mock.call(bisik.Quartz.kCGHIDEventTap, e) for e in events]


class TestFnDetection:
    def press(self, bisik, at):
        with mock.patch.object(bisik.time, "monotonic", return_value=at):
            bisik.on_fn_press()

    def test_a_single_tap_only_arms_the_double_tap(self, bisik):
        self.press(bisik, 100.0)
        assert bisik.jobs.empty()
        assert bisik.recording is False
        assert bisik.last_press == 100.0

    def test_two_taps_within_the_window_start_recording(self, bisik, capsys):
        self.press(bisik, 100.0)
        self.press(bisik, 100.3)
        assert bisik.recording is True
        assert bisik.jobs.get_nowait() is bisik.start_recording
        assert bisik.last_press == 0.0
        assert capsys.readouterr().out.count("fn ditekan") == 2

    def test_two_taps_just_inside_the_window_still_count(self, bisik):
        self.press(bisik, 100.0)
        self.press(bisik, 100.375)  # exactly representable, unlike 100.4
        assert bisik.recording is True

    def test_slow_taps_do_not_start_recording(self, bisik):
        self.press(bisik, 100.0)
        self.press(bisik, 100.5)
        assert bisik.recording is False
        assert bisik.jobs.empty()
        assert bisik.last_press == 100.5

    def test_a_tap_while_recording_queues_stop(self, bisik):
        bisik.recording = True
        self.press(bisik, 200.0)
        assert bisik.recording is False
        assert bisik.jobs.get_nowait() is bisik.stop_recording

    def test_a_quick_second_tap_after_stopping_does_not_restart(self, bisik):
        """Regression: stopping with a double tap immediately started a new recording."""
        bisik.recording = True
        self.press(bisik, 200.0)
        self.press(bisik, 200.2)
        assert bisik.recording is False
        assert [job.__name__ for job in list(bisik.jobs.queue)] == ["stop_recording"]

    def test_a_double_tap_after_stopping_starts_again(self, bisik):
        bisik.recording = True
        self.press(bisik, 200.0)
        self.press(bisik, 201.0)
        self.press(bisik, 201.2)
        assert bisik.recording is True
        assert [job.__name__ for job in list(bisik.jobs.queue)] == ["stop_recording", "start_recording"]


class TestWorker:
    def test_worker_runs_jobs_until_the_queue_raises(self, bisik):
        ran = []
        job_queue = mock.Mock()
        job_queue.get.side_effect = [lambda: ran.append(1), lambda: ran.append(2), KeyboardInterrupt]
        with pytest.raises(KeyboardInterrupt):
            bisik.worker(job_queue)
        assert ran == [1, 2]

    def test_a_failing_job_is_logged_and_does_not_kill_the_worker(self, bisik, hud_on, capsys):
        ran = []

        def broken():
            raise ValueError("boom")

        job_queue = mock.Mock()
        job_queue.get.side_effect = [broken, lambda: ran.append("after"), KeyboardInterrupt]
        with pytest.raises(KeyboardInterrupt):
            bisik.worker(job_queue)

        captured = capsys.readouterr()
        assert "ValueError: boom" in captured.err
        assert "@state:idle" in captured.out
        assert ran == ["after"]


class TestTapCallback:
    def event_with_flags(self, bisik, flags):
        bisik.Quartz.CGEventGetFlags.return_value = flags
        return mock.Mock(name="event")

    def test_fn_press_is_forwarded_once_until_released(self, bisik):
        with mock.patch.object(bisik, "on_fn_press") as on_press:
            event = self.event_with_flags(bisik, FN_MASK)
            assert bisik.tap_callback(None, 12, event, None) is event
            assert bisik.fn_down is True
            bisik.tap_callback(None, 12, event, None)  # modifier flags repeat while held
            assert on_press.call_count == 1

            bisik.Quartz.CGEventGetFlags.return_value = 0
            bisik.tap_callback(None, 12, event, None)
            assert bisik.fn_down is False

            bisik.Quartz.CGEventGetFlags.return_value = FN_MASK
            bisik.tap_callback(None, 12, event, None)
            assert on_press.call_count == 2

    def test_other_modifier_keys_are_ignored(self, bisik):
        with mock.patch.object(bisik, "on_fn_press") as on_press:
            event = self.event_with_flags(bisik, COMMAND_MASK)
            bisik.tap_callback(None, 12, event, None)
        on_press.assert_not_called()
        assert bisik.fn_down is False

    @pytest.mark.parametrize("reason", [TAP_DISABLED_BY_TIMEOUT, TAP_DISABLED_BY_USER_INPUT])
    def test_a_disabled_tap_is_switched_back_on(self, bisik, reason):
        event = mock.Mock()
        with mock.patch.object(bisik, "on_fn_press") as on_press:
            assert bisik.tap_callback(None, reason, event, None) is event
        bisik.Quartz.CGEventTapEnable.assert_called_once_with(bisik.tap, True)
        on_press.assert_not_called()


class TestStartup:
    def test_warm_up_runs_the_model_once_on_silence(self, bisik, capsys):
        bisik.warm_up()
        args, kwargs = bisik.mlx_whisper.transcribe.call_args
        assert args[0].shape == (16000,)
        assert not args[0].any()
        assert kwargs == {"path_or_hf_repo": bisik.MODEL}
        assert "Model siap" in capsys.readouterr().out

    def test_the_event_tap_listens_for_modifier_changes_only(self, bisik):
        quartz = bisik.Quartz
        quartz.CGEventTapCreate.assert_called_once_with(
            quartz.kCGSessionEventTap,
            quartz.kCGHeadInsertEventTap,
            quartz.kCGEventTapOptionListenOnly,
            quartz.CGEventMaskBit.return_value,
            bisik.tap_callback,
            None,
        )
        quartz.CGEventMaskBit.assert_called_once_with(quartz.kCGEventFlagsChanged)

    def test_main_exits_when_the_tap_could_not_be_created(self, bisik):
        bisik.tap = None
        with pytest.raises(SystemExit) as exit_info:
            bisik.main()
        assert "Input Monitoring" in str(exit_info.value)
        bisik.Quartz.CFRunLoopRun.assert_not_called()

    def test_main_exits_when_input_monitoring_is_missing(self, bisik):
        bisik.Quartz.CGPreflightListenEventAccess.return_value = False
        with pytest.raises(SystemExit, match="Gagal memantau tombol fn"):
            bisik.main()
        bisik.Quartz.CFRunLoopRun.assert_not_called()

    def test_main_starts_the_workers_queues_warm_up_and_runs_the_loop(self, bisik, capsys):
        with mock.patch.object(bisik.threading, "Thread") as thread:
            bisik.main()

        assert thread.call_args_list == [
            mock.call(target=bisik.worker, args=(bisik.jobs,), daemon=True),
            mock.call(target=bisik.worker, args=(bisik.model_jobs,), daemon=True),
            mock.call(target=bisik.keep_warm, daemon=True),
        ]
        assert thread.return_value.start.call_count == 3
        assert bisik.model_jobs.get_nowait() is bisik.warm_up
        assert bisik.jobs.empty()
        quartz = bisik.Quartz
        quartz.CGEventTapEnable.assert_called_once_with(bisik.tap, True)
        quartz.CFRunLoopAddSource.assert_called_once()
        quartz.CFRunLoopRun.assert_called_once()
        out = capsys.readouterr().out
        assert out.index("Memuat model") < out.index("Pemantau tombol fn aktif")

    def test_running_the_file_as_a_script_calls_main(self, monkeypatch, capsys):
        quartz = make_quartz()
        for name, module in {
            "Quartz": quartz,
            "mlx_whisper": mock.MagicMock(),
            "sounddevice": mock.MagicMock(),
        }.items():
            monkeypatch.setitem(sys.modules, name, module)
        monkeypatch.setattr("threading.Thread", mock.MagicMock())

        runpy.run_path("bisik.py", run_name="__main__")

        quartz.CFRunLoopRun.assert_called_once()
        assert "Pemantau tombol fn aktif" in capsys.readouterr().out

    def test_language_and_model_can_be_overridden_from_the_environment(self, monkeypatch):
        from tests.conftest import load_bisik

        monkeypatch.setenv("BISIK_MODEL", "custom/model")
        monkeypatch.setenv("BISIK_LANGUAGE", "id")
        module = load_bisik(monkeypatch, mlx_whisper=mock.MagicMock(), sounddevice=mock.MagicMock())
        assert module.MODEL == "custom/model"
        assert module.LANGUAGE == "id"

    def test_an_empty_language_means_auto_detect(self, monkeypatch):
        from tests.conftest import load_bisik

        monkeypatch.setenv("BISIK_LANGUAGE", "")
        module = load_bisik(monkeypatch, mlx_whisper=mock.MagicMock(), sounddevice=mock.MagicMock())
        assert module.LANGUAGE is None


class TestEndToEndFlow:
    """Drive the real state machine through fn taps with the audio and model mocked."""

    def test_double_tap_records_and_a_single_tap_transcribes_and_pastes(self, bisik, monkeypatch, hud_on, capsys):
        monkeypatch.setattr(bisik, "play", lambda name: None)
        bisik.mlx_whisper.transcribe.return_value = {"text": "selamat pagi", "language": "id"}
        ticks = iter([10.0, 10.2, 10.3, 20.0, 20.5, 20.9])  # start sound mute, then fn, then the transcription timer
        monkeypatch.setattr(bisik.time, "monotonic", lambda: next(ticks))

        def fake_start():
            bisik.chunks.append(np.ones(16000, dtype="float32"))

        bisik.Recorder.return_value.wait_until_recording.side_effect = fake_start

        with mock.patch.object(bisik.subprocess, "run") as run:
            bisik.on_fn_press()  # first tap
            bisik.on_fn_press()  # second tap starts recording
            run_jobs(bisik)
            assert bisik.recording is True

            bisik.on_fn_press()  # one tap stops
            run_jobs(bisik)

        assert bisik.recording is False
        run.assert_called_once_with(["pbcopy"], input=b"selamat pagi", check=True)
        bisik.Quartz.CGEventPost.assert_called()
        states = [line for line in capsys.readouterr().out.splitlines() if line.startswith("@state:")]
        assert states == ["@state:recording", "@state:transcribing", "@state:idle"]

    def test_denied_microphone_leaves_the_app_ready_for_another_try(self, bisik, monkeypatch, capsys):
        monkeypatch.setattr(bisik, "play", lambda name: None)
        bisik.Recorder.return_value.wait_until_recording.side_effect = OSError("denied")
        ticks = iter([10.0, 10.1, 30.0, 30.1])
        monkeypatch.setattr(bisik.time, "monotonic", lambda: next(ticks))

        bisik.on_fn_press()
        bisik.on_fn_press()
        job = bisik.jobs.get_nowait()
        with pytest.raises(OSError):
            job()
        assert bisik.recording is False

        bisik.on_fn_press()
        bisik.on_fn_press()
        assert bisik.recording is True
        assert bisik.jobs.get_nowait() is bisik.start_recording


class TestRecordingDoesNotWaitForTheModel:
    """Regression: a double-tap during model warm-up showed nothing for seconds, then replayed taps out of order."""

    def double_tap(self, bisik, start=100.0):
        for moment in (start, start + 0.1):
            with mock.patch.object(bisik.time, "monotonic", return_value=moment):
                bisik.on_fn_press()

    def test_recording_starts_while_the_model_is_still_loading(self, bisik, hud_on, capsys):
        bisik.model_jobs.put(bisik.warm_up)  # the model is still loading, so this job never ran
        with mock.patch.object(bisik, "play"):
            self.double_tap(bisik)
            while not bisik.jobs.empty():  # only the audio worker runs
                bisik.jobs.get_nowait()()

        bisik.Recorder.return_value.wait_until_recording.assert_called_once()
        assert "@state:recording" in capsys.readouterr().out
        assert bisik.model_jobs.qsize() == 1
        bisik.mlx_whisper.transcribe.assert_not_called()

    def test_stopping_while_the_model_loads_queues_transcription_behind_it(self, bisik):
        bisik.model_jobs.put(bisik.warm_up)
        bisik.recorder = bisik.Recorder.return_value
        bisik.chunks.append(np.full(16000, 0.1, dtype="float32"))

        with mock.patch.object(bisik, "play"):
            bisik.stop_recording()

        queued = list(bisik.model_jobs.queue)
        assert queued[0] is bisik.warm_up
        assert queued[1].func is bisik.transcribe_and_paste

    def test_a_slow_model_job_does_not_delay_starting_or_stopping_the_microphone(self, bisik, hud_on, capsys):
        """Drive both real worker threads with a warm-up that blocks until released."""
        import threading

        warm_up_started = threading.Event()
        release_warm_up = threading.Event()
        recorded = threading.Event()

        def slow_warm_up():
            warm_up_started.set()
            release_warm_up.wait(5)

        bisik.Recorder.return_value.wait_until_recording.side_effect = recorded.set
        for queue_ in (bisik.jobs, bisik.model_jobs):
            threading.Thread(target=bisik.worker, args=(queue_,), daemon=True).start()
        with mock.patch.object(bisik, "play"):
            bisik.model_jobs.put(slow_warm_up)
            assert warm_up_started.wait(2)

            self.double_tap(bisik)

            assert recorded.wait(2), "recording must start even though the model is still loading"
        release_warm_up.set()
        assert "@state:recording" in capsys.readouterr().out

    def test_the_log_says_when_the_model_is_not_ready_yet(self, bisik, capsys):
        with mock.patch.object(bisik, "play"):
            bisik.start_recording()
        assert "Model masih dimuat" in capsys.readouterr().out

    def test_the_log_stays_quiet_once_the_model_is_ready(self, bisik, capsys):
        bisik.warm_up()
        assert bisik.model_ready.is_set()
        capsys.readouterr()
        with mock.patch.object(bisik, "play"):
            bisik.start_recording()
        out = capsys.readouterr().out
        assert "Model masih dimuat" not in out
        assert "Merekam..." in out


class TestLogLines:
    def test_log_lines_start_with_a_clock_time(self, bisik, capsys):
        with mock.patch.object(bisik.time, "strftime", return_value="09:08:07") as strftime:
            bisik.log("halo")
        strftime.assert_called_once_with("%H:%M:%S")
        assert capsys.readouterr().out == "09:08:07 halo\n"

    def test_state_markers_stay_unprefixed_so_the_app_can_parse_them(self, bisik, hud_on, capsys):
        bisik.hud("recording")
        assert capsys.readouterr().out == "@state:recording\n"
