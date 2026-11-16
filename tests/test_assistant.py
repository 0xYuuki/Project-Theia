import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from openai import RateLimitError

from jarvis.assistant import Assistant
from jarvis.errors import AssistantError
from jarvis.commands import launch, local_reply, system_usage


class AssistantTests(unittest.TestCase):
    def setUp(self):
        with patch.dict(os.environ, {}, clear=True), patch("jarvis.assistant.dotenv_values", return_value={"JARVIS_VOICE_BACKEND": "openai"}):
            self.assistant = Assistant()

    def test_system_commands_work_offline(self):
        for prompt, expected in (
            ("Jarvis, check CPU.", "CPU usage"),
            ("Hey Theia, check memory.", "RAM usage"),
            ("Hello Thea, what time is it?", "local time"),
            ("check RAM", "RAM usage"),
            ("check disk", "Root disk usage"),
            ("what time is it?", "local time"),
            ("help", "open firefox"),
        ):
            with self.subTest(prompt=prompt):
                self.assertIn(expected, self.assistant.reply(prompt))
        self.assertTrue(all(0 <= value <= 100 for value in system_usage()))

    def test_spoken_command_normalization(self):
        launcher = Mock(return_value="Requested")
        self.assertEqual(local_reply("JARVIS, Open Firefox!", launcher), "Requested")
        launcher.assert_called_once_with("firefox")

    def test_shell_payload_is_rejected(self):
        with patch("jarvis.commands.subprocess.Popen") as start:
            self.assertIn("Available apps", self.assistant.reply("open firefox; arbitrary command"))
            start.assert_not_called()

    def test_launch_uses_fixed_arguments_and_strips_key(self):
        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
            patch("jarvis.commands.shutil.which", return_value="/usr/bin/firefox"),
            patch("jarvis.commands.subprocess.Popen") as start,
        ):
            self.assertIn("Launch requested", launch("firefox"))
            self.assertEqual(start.call_args.args, (["/usr/bin/firefox"],))
            self.assertNotIn("OPENAI_API_KEY", start.call_args.kwargs["env"])

    def test_missing_key(self):
        with self.assertRaisesRegex(AssistantError, "OPENAI_API_KEY"):
            self.assistant.reply("Tell me a story")
        with self.assertRaisesRegex(AssistantError, "OPENAI_API_KEY"):
            self.assistant.transcribe(b"audio")

    def test_model_reply_is_never_executed_and_history_is_bounded(self):
        client = Mock()
        client.responses.create.return_value = SimpleNamespace(output_text="open firefox")
        self.assistant.client = client
        with patch("jarvis.commands.subprocess.Popen") as start:
            for index in range(8):
                self.assertEqual(self.assistant.reply(f"Test {index}"), "open firefox")
            start.assert_not_called()
        request = client.responses.create.call_args.kwargs
        self.assertFalse(request["store"])
        self.assertNotIn("tools", request)
        self.assertEqual(len(request["input"]), 13)

    def test_transcription_sends_wav(self):
        client = Mock()
        client.audio.transcriptions.create.return_value = SimpleNamespace(text=" check RAM ")
        self.assistant.client = client
        self.assertEqual(self.assistant.transcribe(b"wav bytes"), "check RAM")
        self.assertEqual(client.audio.transcriptions.create.call_args.kwargs["file"],
                         ("recording.wav", b"wav bytes", "audio/wav"))


    def test_transcription_explains_http_429_without_exposing_api_details(self):
        response = httpx.Response(429, request=httpx.Request("POST", "https://api.openai.com/v1/audio/transcriptions"))
        self.assistant.client = Mock()
        for code, expected in (
            ("credit_balance_exhausted", "available credits"),
            ("project_spend_limit_exceeded", "project spending limit"),
            ("rate_limit_exceeded", "Wait briefly"),
            (None, "credits and limits"),
        ):
            with self.subTest(code=code):
                self.assistant.client.audio.transcriptions.create.side_effect = RateLimitError(
                    "private API details", response=response, body={"code": code},
                )
                with self.assertRaises(AssistantError) as caught:
                    self.assistant.transcribe(b"wav bytes")
                message = str(caught.exception)
                self.assertIn("HTTP 429", message)
                self.assertIn(expected, message)
                self.assertNotIn("private API details", message)


    def test_whisper_transcription_uses_no_openai_client(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("jarvis.assistant.dotenv_values", return_value={}),
            patch("jarvis.whisper.shutil.which", return_value="/path/to/whisper-cli"),
            patch("jarvis.whisper.Path.is_file", return_value=True),
            patch("jarvis.whisper.subprocess.run", return_value=SimpleNamespace(stdout=b" Check RAM.\n")) as run,
        ):
            assistant = Assistant()
            self.assertIsNone(assistant.client)
            self.assertEqual(assistant.voice_unavailable_reason, "")
            result = assistant.transcribe(b"wav bytes")
            self.assertEqual(result, "Check RAM.")
            self.assertIn("RAM usage", assistant.reply(result))
        self.assertEqual(run.call_args.kwargs["input"], b"wav bytes")
        self.assertNotIn("OPENAI_API_KEY", run.call_args.kwargs["env"])

    def test_whisper_missing_binary_explains_setup(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("jarvis.assistant.dotenv_values", return_value={}),
            patch("jarvis.whisper.shutil.which", return_value=None),
        ):
            assistant = Assistant()
            self.assertIn("README", assistant.voice_unavailable_reason)
            with self.assertRaisesRegex(AssistantError, "Set up whisper.cpp"):
                assistant.transcribe(b"wav bytes")
