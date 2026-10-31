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
        buffer = np.ones((4, 1), dtype="float32")
        bisik.on_audio(buffer, 4, None, None)
        buffer[:] = 0
        assert bisik.chunks[0].sum() == 4

    def test_start_recording_opens_a_mono_16k_stream(self, bisik, hud_on, capsys):
        with mock.patch.object(bisik, "play") as play:
            bisik.chunks.append(np.ones(3))
            bisik.start_recording()

        bisik.sd.InputStream.assert_called_once_with(
            samplerate=16000, channels=1, dtype="float32", callback=bisik.on_audio
        )
        bisik.sd.InputStream.return_value.start.assert_called_once()
        assert bisik.chunks == []
        assert bisik.stream is bisik.sd.InputStream.return_value
        play.assert_called_once_with("Tink")
        out = capsys.readouterr().out
        assert "@state:recording" in out
        assert "Merekam..." in out

    def test_start_recording_failure_resets_the_recording_flag_and_raises(self, bisik):
        bisik.recording = True
        bisik.sd.InputStream.side_effect = OSError("microphone denied")
        with pytest.raises(OSError, match="microphone denied"):
            bisik.start_recording()
        assert bisik.recording is False

    def test_stop_recording_joins_chunks_and_transcribes(self, bisik, hud_on, capsys):
        bisik.stream = mock.MagicMock()
        stream = bisik.stream
        bisik.chunks[:] = [np.ones((2, 1), dtype="float32"), np.full((3, 1), 2, dtype="float32")]

        with mock.patch.object(bisik, "play") as play:
            bisik.stop_recording()

        stream.stop.assert_called_once()
        stream.close.assert_called_once()
        assert bisik.stream is None
        play.assert_called_once_with("Pop")
        job = bisik.model_jobs.get_nowait()
        assert job.func is bisik.transcribe_and_paste
        assert job.args[0].tolist() == [1, 1, 2, 2, 2]
        out = capsys.readouterr().out
        assert "@state:transcribing" in out
        assert "Berhenti merekam" in out

    def test_stop_recording_without_audio_sends_an_empty_array(self, bisik):
        bisik.stream = mock.MagicMock()
        with mock.patch.object(bisik, "play"):
            bisik.stop_recording()
        assert len(bisik.model_jobs.get_nowait().args[0]) == 0

    def test_a_microphone_that_hangs_on_close_does_not_block_transcription(self, bisik, monkeypatch, capsys):
        monkeypatch.setattr(bisik, "STOP_TIMEOUT", 0.05)
        release = bisik.threading.Event()
        bisik.stream = mock.MagicMock()
        bisik.stream.stop.side_effect = lambda: release.wait(5)
        bisik.chunks[:] = [np.ones((16000, 1), dtype="float32")]
        try:
            bisik.stop_recording()
            assert bisik.stream is None
            assert not bisik.model_jobs.empty()
            assert "macet" in capsys.readouterr().out
        finally:
            release.set()

    def test_stop_recording_does_nothing_when_no_stream_was_opened(self, bisik):
        bisik.stream = None
        with mock.patch.object(bisik, "play") as play:
            bisik.stop_recording()
        assert bisik.model_jobs.empty()
        play.assert_not_called()


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
            bisik.transcribe_and_paste(self.audio(0.05))
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

    def test_the_stop_tap_counts_as_the_first_tap_of_a_new_double_tap(self, bisik):
        # Current behavior, pinned on purpose: stopping and tapping again within 0.4s restarts.
        bisik.recording = True
        self.press(bisik, 200.0)
        self.press(bisik, 200.2)
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

    def test_main_starts_both_workers_queues_warm_up_and_runs_the_loop(self, bisik, capsys):
        with mock.patch.object(bisik.threading, "Thread") as thread:
            bisik.main()

        assert thread.call_args_list == [
            mock.call(target=bisik.worker, args=(bisik.jobs,), daemon=True),
            mock.call(target=bisik.worker, args=(bisik.model_jobs,), daemon=True),
        ]
        assert thread.return_value.start.call_count == 2
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
        ticks = iter([10.0, 10.2, 20.0])
        monkeypatch.setattr(bisik.time, "monotonic", lambda: next(ticks))
        stream = bisik.sd.InputStream.return_value

        def fake_start():
            bisik.chunks.append(np.ones((16000, 1), dtype="float32"))

        stream.start.side_effect = fake_start

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
        bisik.sd.InputStream.side_effect = OSError("denied")
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

        bisik.sd.InputStream.return_value.start.assert_called_once()
        assert "@state:recording" in capsys.readouterr().out
        assert bisik.model_jobs.qsize() == 1
        bisik.mlx_whisper.transcribe.assert_not_called()

    def test_stopping_while_the_model_loads_queues_transcription_behind_it(self, bisik):
        bisik.model_jobs.put(bisik.warm_up)
        bisik.stream = mock.MagicMock()
        bisik.chunks.append(np.full((16000, 1), 0.1, dtype="float32"))

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

        bisik.sd.InputStream.return_value.start.side_effect = recorded.set
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
