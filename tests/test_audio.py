import queue
import subprocess
import sys
import unittest
import wave
from io import BytesIO
from threading import Event, Timer
from types import SimpleNamespace
from unittest.mock import patch

from theia.audio import collect_speech, speak
from theia.errors import OperationCancelled
from theia.process import run_process


class SpeechTests(unittest.TestCase):
    def test_speech_skips_markdown_formatting_and_keeps_the_words(self):
        reply = "The color is **green**.\n\n- _Leaves_\n- [Grass](https://example.com)\n- `2 * 3`"
        with (
            patch("theia.audio.shutil.which", return_value="/usr/bin/pw-play"),
            patch("theia.audio.synthesize_speech", return_value=b"wav") as synthesize,
            patch("theia.audio.subprocess.run") as play,
        ):
            speak(reply, length_scale=1.08)
        synthesize.assert_called_once_with("The color is green.\nLeaves\nGrass\n2 * 3", length_scale=1.08)
        self.assertEqual(play.call_args.kwargs["input"], b"wav")

    def test_speech_skips_replies_with_only_formatting(self):
        with patch("theia.audio.synthesize_speech") as synthesize:
            speak("---")
        synthesize.assert_not_called()

    def test_cancelling_playback_terminates_the_player(self):
        cancel = Event()

        def play_test_process(args, **kwargs):
            timer = Timer(0.2, cancel.set)
            timer.start()
            try:
                return run_process([sys.executable, "-c", "import time; time.sleep(20)"], **kwargs)
            finally:
                timer.cancel()

        with (
            patch("theia.audio.shutil.which", return_value="/usr/bin/pw-play"),
            patch("theia.audio.synthesize_speech", return_value=b"wav"),
            patch("theia.audio.run_process", side_effect=play_test_process),
            self.assertRaises(OperationCancelled),
        ):
            speak("A spoken reply.", cancel=cancel)

    def test_playback_runner_keeps_all_input_after_timed_poll(self):
        audio = b"0123456789abcdef" * 32000
        result = run_process(
            [sys.executable, "-c", "import sys,time; time.sleep(.2); sys.stdout.buffer.write(sys.stdin.buffer.read())"],
            input=audio, timeout=3, cancel=Event(), stdout=subprocess.PIPE,
        )
        self.assertEqual(result.stdout, audio)


class RecordingTests(unittest.TestCase):
    def test_push_to_talk_ignores_short_pause_and_finishes_after_sustained_silence(self):
        rate = 16000
        silence = bytes(640)
        voiced = b"\x01\x00" * 320
        frames = queue.Queue()
        clip = [voiced] * 10 + [silence] * 25 + [voiced] * 10 + [silence] * 30
        for frame in [*clip, voiced]:
            frames.put(frame)
        stream = SimpleNamespace(active=True)
        vad = SimpleNamespace(is_speech=lambda frame, sample_rate: frame == voiced)

        wav = collect_speech(frames, stream, vad, rate, Event(), None, duration=5)

        with wave.open(BytesIO(wav), "rb") as recording:
            self.assertEqual(recording.readframes(recording.getnframes()), b"".join(clip))
        self.assertEqual(frames.get_nowait(), voiced)
