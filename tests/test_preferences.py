import os
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from theia.assistant import Assistant
from theia.errors import AssistantError
from theia.preferences import Preferences


class PreferenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config_path = Path(self.directory.name)

    def new_assistant(self):
        with (
            patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.config_path)}, clear=True),
            patch("theia.assistant.dotenv_values", return_value={}),
        ):
            return Assistant()

    def test_spoken_preference_applies_immediately_and_survives_restart(self):
        assistant = self.new_assistant()
        afternoon = datetime(2026, 11, 16, 16, 35)
        with patch("theia.commands.datetime") as clock:
            clock.now.return_value = afternoon
            self.assertEqual(assistant.reply("What time is it?"), "It's 16:35 local time.")
            self.assertIn("use 12-hour time", assistant.reply(
                "Can you adjust in 12 hr format everytime I ask you?", recorded=True,
            ))
            self.assertEqual(assistant.reply("What time is it?"), "It's 4:35 PM local time.")
            restarted = self.new_assistant()
            self.assertEqual(restarted.reply("What time is it?"), "It's 4:35 PM local time.")
            self.assertEqual(restarted.reply("What time format are you using?"), "I'm using 12-hour time.")
            restarted.reply("Use 24-hour time.")
            self.assertEqual(self.new_assistant().reply("What time is it?"), "It's 16:35 local time.")

    def test_noon_and_midnight_in_12_hour_time(self):
        assistant = self.new_assistant()
        confirmation = assistant.reply(
            "Could you change it to twelve-hour format whenever I ask you for the time?", recorded=True,
        )
        self.assertIn("use 12-hour time", confirmation)
        with patch("theia.commands.datetime") as clock:
            clock.now.return_value = datetime(2026, 11, 16, 0, 5)
            self.assertEqual(assistant.reply("What time is it?"), "It's 12:05 AM local time.")
            clock.now.return_value = datetime(2026, 11, 16, 12, 5)
            self.assertEqual(assistant.reply("Hey Theia. What's the time?", recorded=True), "It's 12:05 PM local time.")

    def test_incidental_and_negated_requests_do_not_change_preference(self):
        assistant = self.new_assistant()
        for message in ("What is a 12-hour clock?", "Don't use 12-hour time.", "Use 12-hour time in this example only."):
            with self.subTest(message=message):
                assistant.reply(message, recorded=True)
        self.assertFalse(assistant.preferences.path.exists())
        self.assertEqual(assistant.reply("What time format do you use?"), "I'm using 24-hour time.")

    def test_save_failure_is_reported_instead_of_confirming(self):
        blocked = self.config_path / "blocked"
        blocked.touch()
        assistant = self.new_assistant()
        assistant.preferences = Preferences(blocked / "preferences.json")
        with self.assertRaisesRegex(AssistantError, "couldn't save the time format"):
            assistant.reply("Use 12-hour time.")
