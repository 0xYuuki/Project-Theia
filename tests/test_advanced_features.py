import json
import os
import time
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from theia.assistant import Assistant, Reply
from theia.commands import (
    local_reply,
    normalize_command,
    pipewire_volume,
    set_pipewire_mute,
    set_pipewire_volume,
)
from theia.notes import Notes
from theia.ollama import OllamaChat, parse_ollama_host, sanitize_reply
from theia.preferences import Preferences
from theia.reminders import Reminders, reminder_reply


class AdvancedFeatureTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config_dir = Path(self.directory.name)
        self.preferences = Preferences(self.config_dir / "preferences.json")
        with patch.dict(os.environ, {}, clear=True), patch(
            "theia.assistant.dotenv_values", return_value={"THEIA_CHAT_BACKEND": "ollama"},
        ):
            self.assistant = Assistant(preferences=self.preferences)
        self.assistant.ollama = Mock()

    def test_polite_command_stripping(self):
        for phrase in ("Please check CPU", "Can you please check CPU", "Could you check CPU"):
            reply = self.assistant.reply(phrase)
            self.assertIn("CPU usage", reply)
        self.assertIn("local time", self.assistant.reply("Please what time is it"))

    def test_date_and_day_queries(self):
        now = datetime(2026, 11, 20, 15, 30)
        with patch("theia.commands.datetime") as mock_dt:
            mock_dt.now.return_value = now
            self.assertEqual(self.assistant.reply("What day is it?"), "Today is Friday, November 20, 2026.")
            self.assertEqual(self.assistant.reply("What is today's date?"), "Today is Friday, November 20, 2026.")
            self.assertEqual(self.assistant.reply("Check date"), "Today is Friday, November 20, 2026.")

    def test_battery_queries(self):
        with patch("theia.commands.psutil.sensors_battery") as mock_battery:
            mock_battery.return_value = SimpleNamespace(percent=85.4, power_plugged=True)
            self.assertEqual(self.assistant.reply("Check battery"), "Battery is at 85% (charging).")

            mock_battery.return_value = SimpleNamespace(percent=42.0, power_plugged=False)
            self.assertEqual(self.assistant.reply("Battery status"), "Battery is at 42% (on battery power).")

            mock_battery.return_value = None
            self.assertIn("No battery detected", self.assistant.reply("Check battery"))

    def test_uptime_queries(self):
        with patch("theia.commands.psutil.boot_time", return_value=1000.0), patch(
            "theia.commands.time.time", return_value=1000.0 + 3600 * 5 + 60 * 25
        ):
            self.assertEqual(self.assistant.reply("Check uptime"), "System uptime is 5 hours and 25 minutes.")

    def test_pipewire_volume_commands(self):
        with patch("theia.commands.subprocess.run") as mock_run:
            mock_run.return_value = SimpleNamespace(stdout="Volume: 0.75\n", returncode=0)
            self.assertEqual(pipewire_volume(), (75, False))

            mock_run.return_value = SimpleNamespace(stdout="Volume: 0.50 [MUTED]\n", returncode=0)
            self.assertEqual(pipewire_volume(), (50, True))

        with patch("theia.commands.pipewire_volume", return_value=(75, False)):
            self.assertEqual(self.assistant.reply("Check volume"), "Volume is 75%.")

        with patch("theia.commands.set_pipewire_volume", return_value="Volume set to 60%.") as mock_set:
            self.assertEqual(self.assistant.reply("Set volume to 60%"), "Volume set to 60%.")
            mock_set.assert_called_with("60%")

        with patch("theia.commands.set_pipewire_mute", return_value="Audio muted.") as mock_mute:
            self.assertEqual(self.assistant.reply("Mute audio"), "Audio muted.")
            mock_mute.assert_called_with(True)

        with patch("theia.commands.set_pipewire_mute", return_value="Audio unmuted.") as mock_mute:
            self.assertEqual(self.assistant.reply("Unmute audio"), "Audio unmuted.")
            mock_mute.assert_called_with(False)

    def test_notes_system(self):
        notes_path = self.config_dir / "test_notes.sqlite3"
        notes = Notes(notes_path)
        self.assertEqual(notes.list(), [])

        notes.add("Buy groceries")
        notes.add("Call dentist")
        items = notes.list()
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].text, "Buy groceries")
        self.assertEqual(items[1].text, "Call dentist")

        cleared = notes.clear()
        self.assertEqual(cleared, 2)
        self.assertEqual(notes.list(), [])

    def test_assistant_notes_integration(self):
        self.assertIn("saved that note", self.assistant.reply("Take a note: Prepare slides for presentation"))
        self.assertIn("Prepare slides for presentation", self.assistant.reply("Read my notes"))
        self.assertIn("Cleared 1 notes", self.assistant.reply("Clear my notes"))
        self.assertEqual(self.assistant.reply("Read my notes"), "You have no saved notes.")

    def test_inverted_reminders(self):
        now = datetime(2026, 11, 20, 10, 0).timestamp()
        store = self.assistant.reminders
        reply = reminder_reply("Remind me to check the oven in 15 minutes", store, 24, now=now)
        self.assertIn("check the oven", reply)
        self.assertIn("10:15", reply)

        pending = store.pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].label, "check the oven")
        self.assertEqual(pending[0].due, now + 15 * 60)

    def test_standalone_24hr_reminders(self):
        now = datetime(2026, 11, 20, 10, 0).timestamp()
        store = self.assistant.reminders
        reply = reminder_reply("Remind me at 14 to read", store, 24, now=now)
        self.assertIn("read", reply)
        self.assertIn("14:00", reply)

        pending = store.pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].label, "read")
        self.assertEqual(datetime.fromtimestamp(pending[0].due).hour, 14)

    def test_conversational_reminder_queries(self):
        now = datetime(2026, 11, 20, 10, 0).timestamp()
        store = self.assistant.reminders
        store.add(now + 600, "Laundry")
        for query in ("What are my reminders?", "Check reminders", "Show timers", "What reminders do I have?"):
            with self.subTest(query=query):
                self.assertIn("Laundry", reminder_reply(query, store, 24, now=now))

    def test_ollama_host_parsing_and_think_sanitization(self):
        self.assertEqual(parse_ollama_host(""), ("127.0.0.1", 11434))
        self.assertEqual(parse_ollama_host("192.168.1.50:11434"), ("192.168.1.50", 11434))
        self.assertEqual(parse_ollama_host("http://localhost:8080"), ("localhost", 8080))

        raw = "<think>Let me ponder this question deeply.\nI should explain clearly.</think>Here is the real answer."
        self.assertEqual(sanitize_reply(raw), "Here is the real answer.")

    def test_clear_conversation(self):
        self.assistant.history = [{"role": "user", "content": "Hello"}]
        reply = self.assistant.reply("Clear chat")
        self.assertEqual(reply, "Conversation history cleared.")
        self.assertEqual(self.assistant.history, [])

    def test_openai_base_url_support(self):
        with patch.dict(os.environ, {}, clear=True), patch(
            "theia.assistant.dotenv_values",
            return_value={
                "THEIA_CHAT_BACKEND": "openai",
                "OPENAI_API_KEY": "test-key",
                "OPENAI_BASE_URL": "http://localhost:8000/v1",
            },
        ), patch("theia.assistant.OpenAI") as mock_openai:
            Assistant()
            mock_openai.assert_called_once_with(
                api_key="test-key", timeout=30, max_retries=0, base_url="http://localhost:8000/v1",
            )

    def test_voice_orb_timer_pausing(self):
        from PySide6.QtWidgets import QApplication
        from theia.appearance import VoiceOrb

        app = QApplication.instance() or QApplication([])
        orb = VoiceOrb()
        orb.show()
        self.assertTrue(orb.timer.isActive())
        orb.hide()
        self.assertFalse(orb.timer.isActive())
        orb.show()
        self.assertTrue(orb.timer.isActive())
        orb.deleteLater()

    def test_ui_shortcuts_and_clear_chat(self):
        from PySide6.QtWidgets import QApplication
        from theia.ui import Window

        app = QApplication.instance() or QApplication([])
        window = Window(self.assistant)
        self.addCleanup(window.close)

        window.transcript.setPlainText("Some text")
        self.assistant.history = [{"role": "user", "content": "Hello"}]
        window.clear_chat()
        self.assertIn("Conversation cleared.", window.transcript.toPlainText())
        self.assertEqual(self.assistant.history, [])

        window.handle_escape()

