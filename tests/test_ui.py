import os
import queue
import time
import unittest
from threading import Event
from types import SimpleNamespace
from unittest.mock import ANY, Mock, call, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from theia.assistant import Assistant
from theia.errors import AssistantError, TranscriptionTimeout
from theia.tone import BRIGHT
from theia.ui import VOICE_PREVIEW, Window, style_application


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        style_application(cls.app)

    def setUp(self):
        self.assistant = SimpleNamespace(
            chat_status="Local commands", reply=Mock(return_value="<b>CPU usage</b>"),
            transcribe=Mock(return_value="check RAM"), whisper=None,
            voice_backend="whisper", voice_unavailable_reason="Set up whisper.cpp.",
        )

    def open_window(self):
        window = Window(self.assistant)
        self.addCleanup(window.close)
        self.addCleanup(window.pool.waitForDone)
        if window.preview_decoder is not None:
            self.addCleanup(window.preview_decoder.pool.waitForDone)
        window.show()
        return window

    def wait_for_reply(self, window):
        deadline = time.monotonic() + 3
        while window.job is not None and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertIsNone(window.job, "Assistant did not return to Ready")

    def wait_until(self, condition):
        deadline = time.monotonic() + 3
        while not condition() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertTrue(condition())

    def test_typed_command_returns_to_ready(self):
        window = self.open_window()
        self.assertFalse(window.mic.isEnabled())
        window.entry.setText("check CPU")
        QTest.keyClick(window.entry, Qt.Key.Key_Return)
        self.wait_for_reply(window)
        self.assertEqual(window.windowTitle(), "Theia")
        self.assertIn("Theia", window.transcript.toPlainText())
        self.assertIn("<b>CPU usage</b>", window.transcript.toPlainText())
        self.assertEqual(window.status.text(), "Ready")
        self.assistant.reply.assert_called_once_with("check CPU", recorded=False, cancel=ANY)

    def test_stopping_recording_routes_transcription_and_speaks(self):
        self.assistant.voice_unavailable_reason = ""
        self.assistant.voice_backend = "openai"
        window = self.open_window()

        def record_until_stop(stop):
            if not stop.wait(2):
                raise AssertionError("Record button did not signal stop")
            return b"wav"

        with patch("theia.ui.record", side_effect=record_until_stop), patch("theia.ui.speak") as speak:
            window.voice.setChecked(True)
            window.mic.click()
            self.assertEqual(window.status.text(), "Recording")
            window.mic.click()
            self.wait_for_reply(window)
            speak.assert_called_once_with("<b>CPU usage</b>", cancel=ANY, length_scale=None)
        self.assistant.transcribe.assert_called_once_with(b"wav", cancel=ANY)
        self.assistant.reply.assert_called_once_with("check RAM", recorded=True, cancel=ANY)
        self.assertEqual(window.heard_text.toPlainText(), "check RAM")
        self.assertTrue(window.mic.isEnabled())

    def test_spoken_replies_use_the_tone_of_the_users_message(self):
        window = self.open_window()
        window.voice.setChecked(True)
        with patch("theia.ui.speak") as speak:
            window.send_message("I am excited!")
            self.wait_for_reply(window)
            speak.assert_called_once_with("<b>CPU usage</b>", cancel=ANY, length_scale=BRIGHT.length_scale)

    def test_live_preview_displays_draft_and_dispatches_final_transcript_once(self):
        self.assistant.voice_unavailable_reason = ""
        self.assistant.whisper = SimpleNamespace(transcribe=Mock(return_value="check"))
        window = self.open_window()

        def record_until_stop(stop, *, snapshot):
            snapshot([bytes(32000)], 32000, 16000)
            stop.wait(3)
            return b"final WAV"

        with patch("theia.ui.record", side_effect=record_until_stop):
            window.mic.click()
            self.wait_until(lambda: window.heard_text.toPlainText() == "check")
            self.assertEqual(window.heard_label.text(), "Hearing you")
            self.assistant.reply.assert_not_called()
            self.assertNotIn("check", window.transcript.toPlainText())
            window.mic.click()
            self.wait_for_reply(window)

        self.assistant.transcribe.assert_called_once_with(b"final WAV", cancel=ANY)
        self.assistant.reply.assert_called_once_with("check RAM", recorded=True, cancel=ANY)
        self.assertEqual(window.heard_label.text(), "Last heard")
        self.assertEqual(window.heard_text.toPlainText(), "check RAM")
        self.assertEqual(window.transcript.toPlainText().count("check RAM"), 1)

    def test_closing_during_recording_does_not_dispatch_captured_audio(self):
        self.assistant.voice_unavailable_reason = ""
        window = self.open_window()
        recording = Event()

        def record_until_stop(stop):
            recording.set()
            stop.wait(3)
            return b"captured audio"

        with patch("theia.ui.record", side_effect=record_until_stop):
            window.mic.click()
            self.wait_until(recording.is_set)
            window.close()
            self.wait_until(lambda: not window.isVisible())
        self.assistant.transcribe.assert_not_called()
        self.assistant.reply.assert_not_called()

    def test_late_preview_does_not_replace_final_text_or_delay_dispatch(self):
        self.assistant.voice_unavailable_reason = ""
        decoding = Event()
        release = Event()

        def slow_preview(wav, **kwargs):
            decoding.set()
            release.wait(3)
            return "stale partial"

        self.assistant.whisper = SimpleNamespace(transcribe=Mock(side_effect=slow_preview))
        window = self.open_window()
        self.addCleanup(release.set)

        def record_until_stop(stop, *, snapshot):
            snapshot([bytes(32000)], 32000, 16000)
            snapshot([bytes(64000)], 64000, 16000)
            stop.wait(3)
            return b"final WAV"

        with patch("theia.ui.record", side_effect=record_until_stop):
            window.mic.click()
            self.wait_until(decoding.is_set)
            window.mic.click()
            self.wait_for_reply(window)
            self.assistant.reply.assert_called_once_with("check RAM", recorded=True, cancel=ANY)
            release.set()
            self.assertTrue(window.preview_decoder.pool.waitForDone(1000))
            QTest.qWait(10)
        self.assistant.whisper.transcribe.assert_called_once()
        self.assertEqual(window.heard_text.toPlainText(), "check RAM")

    def test_preview_voice_skips_recording_and_chat(self):
        window = self.open_window()
        with patch("theia.ui.record") as record, patch("theia.ui.speak") as speak:
            window.preview.click()
            self.wait_for_reply(window)
            speak.assert_called_once_with(VOICE_PREVIEW, cancel=ANY)
            record.assert_not_called()
        self.assistant.reply.assert_not_called()
        self.assertTrue(window.preview.isEnabled())
        self.assertEqual(window.status.text(), "Ready")

    def test_hands_free_wakes_answers_and_rearms(self):
        self.assistant.voice_unavailable_reason = ""

        def transcribe(wav, **kwargs):
            if wav == b"timeout":
                raise TranscriptionTimeout("Transcription took too long.")
            return wav.decode()

        self.assistant.whisper = SimpleNamespace(transcribe=Mock(side_effect=transcribe))
        self.assistant.reply.side_effect = [AssistantError("Ollama is unavailable."), "Memory", "Time", "CPU"]
        audio = queue.Queue()

        def listen(stop, *, start_timeout=None, snapshot=None):
            while not stop.is_set():
                try:
                    return audio.get(timeout=0.02)
                except queue.Empty:
                    continue
            return None

        window = self.open_window()
        self.assertFalse(window.hands_free.isChecked())
        with patch("theia.wake.listen_for_speech", side_effect=listen), patch("theia.wake.speak") as speak:
            window.hands_free.setChecked(True)
            self.assertTrue(window.voice.isChecked())
            for transcript in (
                "Hello there!", "timeout", "Hey Theia, tell me a story.", "Hey Theia, check memory.", "Hello, Theia!",
                "What time is it?", "Hi Theia, check CPU.",
            ):
                audio.put(transcript.encode())

            deadline = time.monotonic() + 3
            while self.assistant.reply.call_count < 4 and time.monotonic() < deadline:
                QTest.qWait(10)
            self.assertEqual(self.assistant.reply.call_args_list, [
                call("tell me a story", recorded=True, cancel=ANY),
                call("check memory", recorded=True, cancel=ANY),
                call("What time is it?", recorded=True, cancel=ANY),
                call("check CPU", recorded=True, cancel=ANY),
            ])
            self.assertIn(call("I'm listening.", cancel=ANY, length_scale=None), speak.call_args_list)
            self.assertIn("Ollama is unavailable.", window.transcript.toPlainText())
            self.assertIn("Transcription took too long.", window.transcript.toPlainText())
            self.assertNotIn("Hello there!", window.transcript.toPlainText())

            window.hands_free.setChecked(False)
            deadline = time.monotonic() + 3
            while window.listener is not None and time.monotonic() < deadline:
                QTest.qWait(10)
            self.assertIsNone(window.listener)
            self.assertEqual(window.status.text(), "Ready")

    def test_close_stops_the_hands_free_microphone(self):
        self.assistant.voice_unavailable_reason = ""
        decoding = Event()
        release = Event()

        def transcribe(wav, **kwargs):
            decoding.set()
            release.wait(3)
            return "Hey Theia, late result"

        self.assistant.whisper = SimpleNamespace(transcribe=Mock(side_effect=transcribe))
        window = self.open_window()
        self.addCleanup(release.set)

        def listen(stop, *, start_timeout=None, snapshot=None):
            snapshot([bytes(32000)], 32000, 16000)
            stop.wait(2)
            return None

        with patch("theia.wake.listen_for_speech", side_effect=listen):
            window.hands_free.setChecked(True)
            self.wait_until(decoding.is_set)
            window.close()
            self.wait_until(lambda: not window.isVisible())
            self.assertIsNone(window.listener)
            release.set()
            self.assertTrue(window.preview_decoder.pool.waitForDone(1000))
            QTest.qWait(10)
            self.assertEqual(window.heard_text.toPlainText(), "")
            self.assistant.reply.assert_not_called()

    def test_hands_free_filters_background_and_previews_both_command_flows(self):
        self.assistant.voice_unavailable_reason = ""
        audio = queue.Queue()
        drafts = queue.Queue()

        def transcribe(wav, *, timeout=None, prompt="", preview=False, cancel=None):
            return drafts.get_nowait() if preview else wav.decode()

        def listen(stop, *, start_timeout=None, snapshot):
            while not stop.is_set():
                try:
                    phrase, finished = audio.get(timeout=0.02)
                    break
                except queue.Empty:
                    continue
            else:
                return None
            snapshot([bytes(32000)], 32000, 16000)
            while not stop.is_set() and not finished.wait(0.02):
                pass
            return None if stop.is_set() else phrase.encode()

        self.assistant.whisper = SimpleNamespace(transcribe=Mock(side_effect=transcribe))
        window = self.open_window()
        self.addCleanup(window.hands_free.setChecked, False)
        with patch("theia.wake.listen_for_speech", side_effect=listen), patch("theia.wake.speak"):
            window.hands_free.setChecked(True)

            background_done = Event()
            drafts.put("Hello there, this is private.")
            audio.put(("Hello there, this is private.", background_done))
            self.wait_until(drafts.empty)
            self.assertTrue(window.preview_decoder.pool.waitForDone(1000))
            QTest.qWait(10)
            self.assertEqual(window.heard_text.toPlainText(), "")
            background_done.set()

            inline_done = Event()
            drafts.put("Hey Theia, check")
            audio.put(("Hey Theia, check memory.", inline_done))
            self.wait_until(lambda: window.heard_text.toPlainText() == "Hey Theia, check")
            self.assistant.reply.assert_not_called()
            inline_done.set()
            self.wait_until(lambda: window.last_heard == "Hey Theia, check memory.")

            wake_done = Event()
            drafts.put("Hello Theia.")
            audio.put(("Hello Theia.", wake_done))
            self.wait_until(lambda: window.heard_label.text() == "Hearing you"
                            and window.heard_text.toPlainText() == "Hello Theia.")
            wake_done.set()
            self.wait_until(lambda: window.status.text() == "Listening for command")

            follow_up_done = Event()
            drafts.put("What time")
            audio.put(("What time is it?", follow_up_done))
            self.wait_until(lambda: window.heard_text.toPlainText() == "What time")
            follow_up_done.set()
            self.wait_until(lambda: window.last_heard == "What time is it?"
                            and self.assistant.reply.call_count == 2)
            self.assertEqual(self.assistant.reply.call_args_list, [
                call("check memory", recorded=True, cancel=ANY), call("What time is it?", recorded=True, cancel=ANY),
            ])
            self.assertNotIn("private", window.transcript.toPlainText())
            window.hands_free.setChecked(False)
            self.wait_until(lambda: window.listener is None)

    def test_whisper_recordings_use_offline_commands_even_with_an_api_key(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("theia.assistant.dotenv_values", return_value={
                "THEIA_CHAT_BACKEND": "openai",
                "OPENAI_API_KEY": "unused",
            }),
            patch("theia.whisper.shutil.which", return_value="/path/to/whisper-cli"),
            patch("theia.whisper.Path.is_file", return_value=True),
        ):
            self.assistant = Assistant()
            self.assistant.client = Mock()
            self.assistant.client.responses.create.return_value = SimpleNamespace(output_text="Cloud reply")
            self.assistant.whisper.transcribe = Mock()
            window = self.open_window()
            self.assertTrue(window.mic.isEnabled())
            with patch("theia.ui.record", return_value=b"wav"):
                for transcript, expected in (
                    ("Hello Jarvis, check from usage.", "RAM usage is"),
                    ("Hello Jarvis, tell me a story.", "Try saying"),
                ):
                    with self.subTest(transcript=transcript):
                        self.assistant.whisper.transcribe.return_value = transcript
                        window.mic.click()
                        self.wait_for_reply(window)
                        chat = window.transcript.toPlainText()
                        self.assertIn(transcript, chat)
                        self.assertIn(expected, chat)
                        self.assistant.client.responses.create.assert_not_called()
