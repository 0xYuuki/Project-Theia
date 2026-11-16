import os
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import Mock, patch

from theia.assistant import Assistant
from theia.errors import AssistantError, OperationCancelled
from theia.interruption import FRAME_BYTES, EchoInterruption, EchoRoute, is_stop_phrase
from theia.pacing import ACKNOWLEDGMENT, CHECKING_FILE, FileFeedback
from theia.preferences import Preferences
from theia.reminders import ReminderTurn


class ConversationTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "preferences.json"
        self.assistant = self.local_assistant()

    def local_assistant(self):
        with patch.dict(os.environ, {}, clear=True), patch(
            "theia.assistant.dotenv_values", return_value={"THEIA_CHAT_BACKEND": "ollama"},
        ):
            assistant = Assistant(preferences=Preferences(self.path))
        assistant.ollama = Mock()
        assistant.ollama.reply.return_value = "Local answer."
        return assistant

    def test_reminder_asks_only_for_missing_fields_and_schedules_once(self):
        self.assertIn("AM or PM", self.assistant.reply("Remind me at six"))
        self.assertIn("What should I remind you", self.assistant.reply("PM"))
        self.assertEqual(self.assistant.reminders.pending(), [])
        self.assertIn("check the oven", self.assistant.reply("check the oven", recorded=True))
        [reminder] = self.assistant.reminders.pending()
        self.assertEqual(reminder.label, "check the oven")
        self.assertEqual(datetime.fromtimestamp(reminder.due).hour, 18)
        self.assistant.reply("check the oven")
        self.assertEqual(len(self.assistant.reminders.pending()), 1)

    def test_reminder_drafts_cancel_expire_and_stay_in_their_own_turn(self):
        turn = ReminderTurn()
        self.assistant.prepare_reply("Remind me at six to check mail", reminder_turn=turn)
        self.assistant.reply("PM")
        self.assertEqual(self.assistant.reminders.pending(), [])
        self.assertIn("didn't set", self.assistant.prepare_reply("Never mind", reminder_turn=turn).text)

        store = self.assistant.reminders
        turn.reply("Set a timer", store, 24, now=10)
        self.assertIsNone(turn.reply("10 minutes", store, 24, now=71))
        self.assistant.reply("Set a timer")
        self.assertIn("RAM usage", self.assistant.reply("check memory"))
        self.assistant.reply("10 minutes")
        self.assertEqual(store.pending(), [])

    def test_unclear_period_reprompts_without_guessing_the_time(self):
        self.assertIn("AM or PM", self.assistant.reply("Remind me of SIX.", recorded=True))
        self.assertIn("AM or PM", self.assistant.reply("VM.", recorded=True))
        self.assertEqual(self.assistant.reminders.pending(), [])
        self.assertIn("What should I remind you", self.assistant.reply("In the evening.", recorded=True))

    def test_recap_is_saved_only_after_approval_and_can_resume_rename_and_delete(self):
        self.assistant.reply("Which project should I start?")
        draft = self.assistant.draft_recap()
        self.assertEqual(draft, "Local answer.")
        self.assertEqual(self.assistant.recaps.list(), [])
        self.assistant.recaps.save("My project", "The next step is testing the prototype.")

        restarted = self.local_assistant()
        self.assertIn("testing the prototype", restarted.reply("Let's continue My project.", recorded=True))
        restarted.reply("What's next?")
        call = restarted.ollama.reply.call_args.args[0]
        self.assertIn("testing the prototype", str(call))
        restarted.recaps.save("Home project", "Open question: tooling.", previous_title="My project")
        self.assertIsNone(restarted.recaps.get("My project"))
        self.assertIsNotNone(restarted.recaps.get("home Project"))
        restarted.recaps.delete("Home project")
        self.assertEqual(restarted.recaps.list(), [])
        restarted.ollama = None
        restarted.chat_backend = "openai"
        restarted.client = Mock()
        with self.assertRaisesRegex(AssistantError, "Enable local Ollama"):
            restarted.reply("What's next?")
        restarted.client.responses.create.assert_not_called()

    def test_clipboard_reads_only_on_explicit_request_and_is_not_saved(self):
        clipboard = Mock(return_value="private copied text")
        self.assistant.read_clipboard = clipboard
        self.assistant.reply("Hello.")
        clipboard.assert_not_called()
        self.assistant.reply("Explain the text I copied.", recorded=True)
        clipboard.assert_called_once()
        self.assertIn("private copied text", str(self.assistant.ollama.reply.call_args.args))
        self.assistant.draft_recap()
        self.assertNotIn("private copied text", str(self.assistant.ollama.reply.call_args.args))
        clipboard.return_value = "a" * 4001
        with self.assertRaisesRegex(AssistantError, "shorter excerpt"):
            self.assistant.reply("Explain the text I copied.")
        clipboard.reset_mock()
        self.assistant.ollama = None
        self.assistant.chat_backend = "openai"
        self.assistant.client = Mock()
        with self.assertRaisesRegex(AssistantError, "Enable local Ollama"):
            self.assistant.reply("Explain the text I copied.")
        clipboard.assert_not_called()
        self.assistant.client.responses.create.assert_not_called()

    def test_slow_file_ack_is_cancelled_and_joined_before_final_playback(self):
        started = Event()
        phases = []

        def speak(text, *, cancel):
            phases.append(text)
            started.set()
            while not cancel.is_set():
                Event().wait(0.01)
            phases.append("ack stopped")

        feedback = FileFeedback(phases.append, speak, Event(), voice=True)
        with feedback.checking(delay=0.01):
            self.assertTrue(started.wait(1))
        phases.append("final speech")
        self.assertEqual(phases, [CHECKING_FILE, ACKNOWLEDGMENT, "ack stopped", "final speech"])

        phases.clear()
        with FileFeedback(phases.append, speak, Event(), voice=True).checking(delay=1):
            pass
        self.assertEqual(phases, [CHECKING_FILE])


class InterruptionTests(unittest.TestCase):
    def test_speech_match_requires_the_exact_phrase(self):
        self.assertTrue(is_stop_phrase("Wait, Theia!"))
        for text in ("Wait", "Hey, Theia", "I said wait, Theia.", "Wait, Theia, what time is it?"):
            self.assertFalse(is_stop_phrase(text))

    def test_playback_uses_echo_route_and_accepted_phrase_cancels_audio(self):
        route = EchoRoute("23", "24")
        playback = Event()
        silence = bytes(FRAME_BYTES)
        voiced = b"\x10" * FRAME_BYTES
        whisper = Mock()
        whisper.transcribe.return_value = "Wait, Theia."

        def frames(target, finished):
            self.assertEqual(target, route.source)
            yield silence
            if playback.wait(1):
                yield from [voiced] * 10
                yield from [silence] * 16
                finished.wait(1)

        def play(audio, *, cancel, target):
            self.assertEqual(target, route.sink)
            playback.set()
            if cancel.wait(1):
                raise OperationCancelled

        with (
            patch("theia.interruption.pipewire_route", return_value=route),
            patch("theia.interruption.echo_frames", side_effect=frames),
            patch("theia.interruption.render_speech", return_value=b"wav"),
            patch("theia.interruption.play_audio", side_effect=play),
            patch("webrtcvad.Vad", return_value=Mock(is_speech=lambda frame, rate: frame == voiced)),
        ):
            stopped = EchoInterruption().play("Example reply", whisper, route, Event(), Event())
        self.assertTrue(stopped)
        self.assertFalse(whisper.transcribe.call_args.kwargs["command_terms"])

    def test_sound_check_requires_quiet_audio_then_a_spoken_stop(self):
        route = EchoRoute("23", "24")
        whisper = Mock(unavailable_reason="")
        statuses = Mock()
        interruption = EchoInterruption()
        with patch("theia.interruption.pipewire_route", return_value=route):
            for clips, error in (([True], "picked up speech"), ([False, False], "didn't hear")):
                with patch.object(interruption, "play", side_effect=clips), self.assertRaisesRegex(AssistantError, error):
                    interruption.check(whisper, Event(), Event(), statuses)
                self.assertIsNone(interruption.route)
            with patch.object(interruption, "play", side_effect=[False, True]):
                interruption.check(whisper, Event(), Event(), statuses)
        self.assertEqual(interruption.route, route)
        interruption.disable()
        self.assertIsNone(interruption.route)

    def test_replaced_device_requires_another_check_without_opening_a_microphone(self):
        route = EchoRoute("23", "24")
        with (
            patch("theia.interruption.pipewire_route", return_value=EchoRoute("32", "24")),
            patch("theia.interruption.echo_frames") as frames,
            self.assertRaisesRegex(AssistantError, "devices changed"),
        ):
            EchoInterruption().play("Example reply", Mock(), route, Event(), Event())
        frames.assert_not_called()
