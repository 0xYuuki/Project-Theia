import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from jarvis.assistant import Assistant
from jarvis.ui import VOICE_PREVIEW, Window, style_application


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        style_application(cls.app)

    def setUp(self):
        self.assistant = SimpleNamespace(
            client=None, reply=Mock(return_value="<b>CPU usage</b>"),
            transcribe=Mock(return_value="check RAM"),
            voice_backend="whisper", voice_unavailable_reason="Set up whisper.cpp.",
        )

    def open_window(self):
        window = Window(self.assistant)
        self.addCleanup(window.close)
        self.addCleanup(window.pool.waitForDone)
        window.show()
        return window

    def wait_for_reply(self, window):
        deadline = time.monotonic() + 3
        while window.job is not None and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertIsNone(window.job, "Assistant did not return to Ready")

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
        self.assistant.reply.assert_called_once_with("check CPU", recorded=False)

    def test_stopping_recording_routes_transcription_and_speaks(self):
        self.assistant.voice_unavailable_reason = ""
        window = self.open_window()

        def record_until_stop(stop):
            if not stop.wait(2):
                raise AssertionError("Record button did not signal stop")
            return b"wav"

        with patch("jarvis.ui.record", side_effect=record_until_stop), patch("jarvis.ui.speak") as speak:
            window.voice.setChecked(True)
            window.mic.click()
            self.assertEqual(window.status.text(), "Recording")
            window.mic.click()
            self.wait_for_reply(window)
            speak.assert_called_once_with("<b>CPU usage</b>")
        self.assistant.transcribe.assert_called_once_with(b"wav")
        self.assistant.reply.assert_called_once_with("check RAM", recorded=True)
        self.assertTrue(window.mic.isEnabled())

    def test_preview_voice_skips_recording_and_chat(self):
        window = self.open_window()
        with patch("jarvis.ui.record") as record, patch("jarvis.ui.speak") as speak:
            window.preview.click()
            self.wait_for_reply(window)
            speak.assert_called_once_with(VOICE_PREVIEW)
            record.assert_not_called()
        self.assistant.reply.assert_not_called()
        self.assertTrue(window.preview.isEnabled())
        self.assertEqual(window.status.text(), "Ready")

    def test_whisper_recordings_use_offline_commands_even_with_an_api_key(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("jarvis.assistant.dotenv_values", return_value={}),
            patch("jarvis.whisper.shutil.which", return_value="/path/to/whisper-cli"),
            patch("jarvis.whisper.Path.is_file", return_value=True),
        ):
            self.assistant = Assistant()
            self.assistant.client = Mock()
            self.assistant.client.responses.create.return_value = SimpleNamespace(output_text="Cloud reply")
            self.assistant.whisper.transcribe = Mock()
            window = self.open_window()
            self.assertTrue(window.mic.isEnabled())
            with patch("jarvis.ui.record", return_value=b"wav"):
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
