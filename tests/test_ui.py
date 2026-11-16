import os
import queue
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
from unittest.mock import ANY, Mock, call, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from theia.assistant import Assistant, Reply
from theia.dialogs import SettingsDialog
from theia.errors import AssistantError, OperationCancelled, TranscriptionTimeout
from theia.preferences import Preferences
from theia.tone import BRIGHT, tone_for_message
from theia.ui import VOICE_PREVIEW, Window, style_application


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        style_application(cls.app)

    def setUp(self):
        self.assistant = SimpleNamespace(
            chat_status="Local commands",
            prepare_reply=Mock(return_value=Reply("<b>CPU usage</b>", "Here is your CPU usage.")),
            reminders=SimpleNamespace(take_due=lambda: []), speaking_pace=lambda text: tone_for_message(text).length_scale,
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
        self.assistant.prepare_reply.assert_called_once_with("check CPU", recorded=False, cancel=ANY, spoken=False)

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
            speak.assert_called_once_with("Here is your CPU usage.", cancel=ANY, length_scale=None)
        self.assistant.transcribe.assert_called_once_with(b"wav", cancel=ANY)
        self.assistant.prepare_reply.assert_called_once_with("check RAM", recorded=True, cancel=ANY, spoken=True)
        self.assertEqual(window.heard_text.toPlainText(), "check RAM")
        self.assertTrue(window.mic.isEnabled())

    def test_spoken_replies_use_the_tone_of_the_users_message(self):
        window = self.open_window()
        window.voice.setChecked(True)
        with patch("theia.ui.speak") as speak:
            window.send_message("I am excited!")
            self.wait_for_reply(window)
            speak.assert_called_once_with("Here is your CPU usage.", cancel=ANY, length_scale=BRIGHT.length_scale)

    def test_correcting_final_transcript_keeps_original_and_uses_voice_routing(self):
        window = self.open_window()
        window.add_voice_transcript("Open fire fox.")
        window.edit_heard.click()
        self.assertEqual(window.entry.text(), "Open fire fox.")
        self.assistant.prepare_reply.assert_not_called()
        window.entry.setText("Open Firefox.")
        window.send.click()
        self.wait_for_reply(window)
        self.assistant.prepare_reply.assert_called_once_with("Open Firefox.", recorded=True, cancel=ANY, spoken=False)
        self.assertIn("Open fire fox.", window.transcript.toPlainText())
        self.assertEqual(window.heard_text.toPlainText(), "Open fire fox.")

    def test_stop_shortcut_cancels_manual_playback_without_losing_the_reply(self):
        window = self.open_window()
        window.voice.setChecked(True)
        speaking = Event()

        def play(text, *, cancel, length_scale):
            speaking.set()
            if cancel.wait(3):
                raise OperationCancelled
            raise AssistantError("Playback wasn't cancelled.")

        with patch("theia.ui.speak", side_effect=play):
            window.send_message("check memory")
            self.wait_until(lambda: speaking.is_set() and window.stop_voice.isEnabled())
            QTest.keyClick(window.entry, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
            self.wait_for_reply(window)
        self.assertIn("CPU usage", window.transcript.toPlainText())
        self.assertNotIn("wasn't cancelled", window.transcript.toPlainText())
        self.assertEqual(window.status.text(), "Ready")

    def test_app_presents_overdue_reminder_and_supports_cancelling_pending_items(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        prefs = Preferences(Path(directory.name) / "preferences.json")
        self.assistant = Assistant(preferences=prefs)
        reminder = "Check the oven"
        self.assistant.reminders.add(time.time() - 10, reminder)
        self.assistant.reminders.add(time.time() + 3_600, "Next timer")
        with patch("theia.ui.send_notification") as notify:
            window = self.open_window()
            self.wait_until(lambda: notify.called)
            notify.assert_called_once_with(reminder)
            window.check_reminders()
        self.assertEqual(window.transcript.toPlainText().count(reminder), 1)
        window.show_reminders()
        dialog = window.reminders_dialog
        self.addCleanup(dialog.accept)
        self.assertEqual(dialog.items.count(), 1)
        dialog.items.setCurrentRow(0)
        dialog.cancel_item.click()
        self.assertEqual(dialog.items.count(), 0)
        self.assertEqual(self.assistant.reminders.pending(), [])

    def test_attachment_is_loaded_in_the_worker_and_removed_from_the_window(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        path = root / "schedule.txt"
        path.write_text("Registration is due June 18.", encoding="utf-8")
        self.assistant = Assistant(preferences=Preferences(root / "preferences.json"))
        self.assistant.ollama = Mock()
        window = self.open_window()
        window.change_attachment(str(path))
        self.wait_for_reply(window)
        self.assertEqual(window.file_label.text(), "schedule.txt")
        self.assertIsNotNone(self.assistant.document)
        window.remove_file.click()
        self.wait_for_reply(window)
        self.assertFalse(window.remove_file.isVisible())
        self.assertIsNone(self.assistant.document)

    def test_settings_view_changes_and_resets_each_saved_value(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        prefs = Preferences(Path(directory.name) / "preferences.json")
        window = self.open_window()
        dialog = SettingsDialog(prefs, window)
        self.addCleanup(dialog.deleteLater)
        expected = {"time_format": 12, "answer_length": "short", "speaking_speed": "slower"}
        for name, value in expected.items():
            field = dialog.fields[name]
            field.activated.emit(field.findData(value))
            self.assertEqual(prefs.get(name), value)
        for button in dialog.findChildren(QPushButton):
            if button.text() == "Reset":
                button.click()
        self.assertEqual([prefs.get(name) for name in expected], [24, "default", "normal"])
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
            self.assistant.prepare_reply.assert_not_called()
            self.assertNotIn("check", window.transcript.toPlainText())
            window.mic.click()
            self.wait_for_reply(window)

        self.assistant.transcribe.assert_called_once_with(b"final WAV", cancel=ANY)
        self.assistant.prepare_reply.assert_called_once_with("check RAM", recorded=True, cancel=ANY, spoken=False)
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
        self.assistant.prepare_reply.assert_not_called()

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
            self.assistant.prepare_reply.assert_called_once_with("check RAM", recorded=True, cancel=ANY, spoken=False)
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
            speak.assert_called_once_with(VOICE_PREVIEW, cancel=ANY, length_scale=None)
            record.assert_not_called()
        self.assistant.prepare_reply.assert_not_called()
        self.assertTrue(window.preview.isEnabled())
        self.assertEqual(window.status.text(), "Ready")

    def test_hands_free_wakes_answers_and_rearms(self):
        self.assistant.voice_unavailable_reason = ""

        def transcribe(wav, **kwargs):
            if wav == b"timeout":
                raise TranscriptionTimeout("Transcription took too long.")
            return wav.decode()

        self.assistant.whisper = SimpleNamespace(transcribe=Mock(side_effect=transcribe))
        self.assistant.prepare_reply.side_effect = [
            AssistantError("Ollama is unavailable."), Reply("Memory details", "Memory summary"),
            Reply("Time", "Time"), Reply("CPU", "CPU"),
        ]
        audio = queue.Queue()
        playback_started = Event()
        playback_cancelled = Event()

        def listen(stop, *, start_timeout=None, snapshot=None):
            while not stop.is_set():
                try:
                    return audio.get(timeout=0.02)
                except queue.Empty:
                    continue
            return None

        def play(text, *, cancel, length_scale):
            if text == "Memory summary":
                playback_started.set()
                if cancel.wait(3):
                    playback_cancelled.set()
                    raise OperationCancelled

        window = self.open_window()
        self.addCleanup(window.hands_free.setChecked, False)
        self.assertFalse(window.hands_free.isChecked())
        with (
            patch("theia.wake.listen_for_speech", side_effect=listen) as capture,
            patch("theia.wake.speak", side_effect=play) as speak,
        ):
            window.hands_free.setChecked(True)
            self.assertTrue(window.voice.isChecked())
            for transcript in (
                "Hello there!", "timeout", "Hey Theia, tell me a story.", "Hello, Theia!", "Check memory.",
            ):
                audio.put(transcript.encode())
            self.wait_until(playback_started.is_set)
            self.assertEqual(capture.call_count, 5)
            self.assertEqual(capture.call_args.kwargs["start_timeout"], 12)
            self.wait_until(lambda: window.status.text() == "Speaking")
            window.stop_voice.click()

            self.wait_until(lambda: window.status.text() == "Listening for follow-up")
            self.assertTrue(playback_cancelled.is_set())
            self.assertEqual(window.heard_text.toPlainText(), "Check memory.")
            for transcript in ("What time is it?", "That’s all.", "Ignore after ending.", "Hi Theia, check CPU."):
                audio.put(transcript.encode())
            audio.put(None)  # Speech did not start within the follow-up window.
            audio.put(b"Ignore after silence.")

            self.wait_until(lambda: capture.call_count == 12 and window.status.text() == "Waiting for wake phrase")
            self.assertIsNone(capture.call_args.kwargs["start_timeout"])
            self.assertEqual(self.assistant.prepare_reply.call_args_list, [
                call("tell me a story", recorded=True, cancel=ANY, spoken=True),
                call("Check memory.", recorded=True, cancel=ANY, spoken=True),
                call("What time is it?", recorded=True, cancel=ANY, spoken=True),
                call("check CPU", recorded=True, cancel=ANY, spoken=True),
            ])
            self.assertEqual([args.args[0] for args in speak.call_args_list], [
                "I'm listening.", "Memory summary", "Time", "Talk to you later.", "CPU",
            ])
            chat = window.transcript.toPlainText()
            self.assertIn("Memory details", chat)
            self.assertIn("Ollama is unavailable.", chat)
            self.assertIn("Transcription took too long.", chat)
            self.assertNotIn("Memory summary", chat)
            self.assertNotIn("Hello there!", chat)
            self.assertNotIn("Ignore after", chat)

            window.hands_free.setChecked(False)
            self.wait_until(lambda: window.listener is None)
            self.assertEqual(window.status.text(), "Ready")

    def test_disabling_hands_free_during_follow_up_ignores_late_audio(self):
        self.assistant.voice_unavailable_reason = ""
        self.assistant.whisper = SimpleNamespace(transcribe=Mock(side_effect=lambda wav, **kwargs: wav.decode()))
        window = self.open_window()
        self.addCleanup(window.hands_free.setChecked, False)
        follow_up = Event()

        def listen(stop, *, start_timeout=None, snapshot=None):
            if start_timeout is None:
                return b"Hey Theia, check memory."
            follow_up.set()
            stop.wait(3)
            return b"What time is it?"

        with patch("theia.wake.listen_for_speech", side_effect=listen) as capture, patch("theia.wake.speak"):
            window.hands_free.setChecked(True)
            self.wait_until(follow_up.is_set)
            window.hands_free.setChecked(False)
            self.wait_until(lambda: window.listener is None)
            self.assertEqual(capture.call_count, 2)
            self.assistant.prepare_reply.assert_called_once_with("check memory", recorded=True, cancel=ANY, spoken=True)
            self.assertNotIn("What time is it?", window.transcript.toPlainText())

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
            self.assistant.prepare_reply.assert_not_called()

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
            self.assistant.prepare_reply.assert_not_called()
            inline_done.set()
            self.wait_until(lambda: window.last_heard == "Hey Theia, check memory.")

            self.wait_until(lambda: window.status.text() == "Listening for follow-up")

            follow_up_done = Event()
            drafts.put("What time")
            audio.put(("What time is it?", follow_up_done))
            self.wait_until(lambda: window.heard_text.toPlainText() == "What time")
            follow_up_done.set()
            self.wait_until(lambda: window.last_heard == "What time is it?"
                            and self.assistant.prepare_reply.call_count == 2)
            self.assertEqual(self.assistant.prepare_reply.call_args_list, [
                call("check memory", recorded=True, cancel=ANY, spoken=True),
                call("What time is it?", recorded=True, cancel=ANY, spoken=True),
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
