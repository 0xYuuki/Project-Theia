import json
import os
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import httpx
from openai import RateLimitError

from theia.assistant import SPEECH_FALLBACK, VOICE_SCHEMA, Assistant, Reply
from theia.audio import encode_wav
from theia.commands import launch, local_reply, system_usage
from theia.errors import AssistantError, NoSpeechDetected
from theia.ollama import OllamaChat
from theia.tone import BRIGHT, GENTLE, WARM
from theia.whisper import ACCURATE_MODEL, FAST_MODEL, WhisperTranscriber


class AssistantTests(unittest.TestCase):
    def setUp(self):
        with patch.dict(os.environ, {}, clear=True), patch(
            "theia.assistant.dotenv_values",
            return_value={"JARVIS_VOICE_BACKEND": "openai", "THEIA_CHAT_BACKEND": "openai"},
        ):
            self.assistant = Assistant()

    def test_system_commands_work_offline(self):
        for prompt, expected in (
            ("Jarvis, check CPU.", "CPU usage"),
            ("Hey Theia, check memory.", "RAM usage"),
            ("Hello Thea, what time is it?", "local time"),
            ("check RAM", "RAM usage"),
            ("Check my R.A.M. usage.", "RAM usage"),
            ("Check our AM usage.", "RAM usage"),
            ("What's my C P U usage?", "CPU usage"),
            ("How much memory am I using?", "RAM usage"),
            ("check disk", "Root disk usage"),
            ("Show me the disk usage.", "Root disk usage"),
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
        with patch("theia.commands.subprocess.Popen") as start:
            self.assertIn("Available apps", self.assistant.reply("open firefox; arbitrary command"))
            start.assert_not_called()

    def test_launch_uses_fixed_arguments_and_strips_key(self):
        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
            patch("theia.commands.shutil.which", return_value="/usr/bin/firefox"),
            patch("theia.commands.subprocess.Popen") as start,
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
        with patch("theia.commands.subprocess.Popen") as start:
            for index in range(8):
                self.assertEqual(self.assistant.reply(f"Test {index}"), "open firefox")
            start.assert_not_called()
        request = client.responses.create.call_args.kwargs
        self.assertFalse(request["store"])
        self.assertNotIn("tools", request)
        self.assertEqual(len(request["input"]), 13)

    def test_local_chat_answers_typed_and_spoken_questions_with_shared_context(self):
        with patch.dict(os.environ, {}, clear=True), patch(
            "theia.assistant.dotenv_values", return_value={"THEIA_CHAT_BACKEND": "ollama", "OPENAI_API_KEY": "unused"},
        ):
            assistant = Assistant()
        self.assertIsNone(assistant.client)
        self.assertEqual(assistant.chat_status, "Local AI")
        assistant.ollama.reply = Mock(side_effect=[
            "RAM stores active data.",
            json.dumps({"display": "open firefox", "speech": "RAM keeps active data handy."}),
        ])
        with patch("theia.commands.subprocess.Popen") as start:
            self.assertIn("RAM usage is", assistant.reply("check memory", recorded=True))
            assistant.ollama.reply.assert_not_called()
            assistant.reply("What does RAM do?")
            self.assertIsNone(assistant.ollama.reply.call_args.kwargs["schema"])
            self.assertEqual(
                assistant.prepare_reply("Can you make that shorter?", recorded=True, spoken=True),
                Reply("open firefox", "RAM keeps active data handy."),
            )
            start.assert_not_called()
        self.assertEqual(assistant.ollama.reply.call_count, 2)
        self.assertEqual(assistant.ollama.reply.call_args.kwargs["schema"], VOICE_SCHEMA)
        self.assertEqual(assistant.history[-1], {"role": "assistant", "content": "open firefox"})
        messages = assistant.ollama.reply.call_args.args[0]
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1:], [
            {"role": "user", "content": "What does RAM do?"},
            {"role": "assistant", "content": "RAM stores active data."},
            {"role": "user", "content": "Can you make that shorter?"},
        ])

    def test_spoken_summary_falls_back_while_preserving_the_display_answer(self):
        self.assistant.ollama = Mock()
        cases = ({}, {"speech": " "}, {"speech": 3}, {"speech": "word " * 36}, {"speech": "One. Two. Three."})
        for fields in cases:
            with self.subTest(fields=fields):
                self.assistant.ollama.reply.return_value = json.dumps({"display": "Complete answer.", **fields})
                self.assertEqual(
                    self.assistant.prepare_reply("Tell me a story", spoken=True),
                    Reply("Complete answer.", SPEECH_FALLBACK),
                )

    def test_invalid_structured_replies_do_not_enter_chat_history(self):
        self.assistant.ollama = Mock()
        for content in ('{"display":', '["text"]', '{"speech":"Hello!"}'):
            with self.subTest(content=content):
                self.assistant.ollama.reply.return_value = content
                with self.assertRaisesRegex(AssistantError, "unreadable answer"):
                    self.assistant.prepare_reply("Tell me a story", spoken=True)
                self.assertEqual(self.assistant.history, [])

    def test_local_chat_connection_error_preserves_commands_without_cloud_fallback(self):
        with patch.dict(os.environ, {}, clear=True), patch(
            "theia.assistant.dotenv_values", return_value={"THEIA_CHAT_BACKEND": "ollama"},
        ):
            assistant = Assistant()
        assistant.client = Mock()
        with patch("theia.ollama.HTTPConnection") as connect:
            connect.return_value.request.side_effect = ConnectionRefusedError
            with self.assertRaisesRegex(AssistantError, "Couldn't reach Ollama"):
                assistant.reply("What's your name?", recorded=True)
        self.assertIn("local time", assistant.reply("what time is it", recorded=True))
        self.assertEqual(assistant.history, [])
        assistant.client.responses.create.assert_not_called()

    def test_chat_style_tracks_explicit_feelings(self):
        with patch.dict(os.environ, {}, clear=True), patch(
            "theia.assistant.dotenv_values", return_value={"THEIA_CHAT_BACKEND": "ollama"},
        ):
            assistant = Assistant()
        assistant.ollama.reply = Mock(return_value="Reply")
        cases = (
            ("I'm excited about my new job.", BRIGHT),
            ("I feel really frustrated today.", GENTLE),
            ("I'm happy but I'm also worried.", GENTLE),
            ("I'm not upset.", WARM),
            ("What does “I'm sad” mean?", WARM),
        )
        for message, tone in cases:
            with self.subTest(message=message):
                assistant.reply(message)
                system_message = assistant.ollama.reply.call_args.args[0][0]
                self.assertTrue(system_message["content"].endswith(tone.instruction))

    def test_ollama_request_uses_loopback_and_displays_only_answer_content(self):
        response = MagicMock(status=200)
        response.__enter__.return_value = response
        response.read.return_value = b'{"message":{"content":" Hi! ","thinking":"Hidden"}}'
        with patch("theia.ollama.HTTPConnection") as connect:
            connection = connect.return_value
            connection.getresponse.return_value = response
            self.assertEqual(OllamaChat().reply([]), "Hi!")
            self.assertEqual(OllamaChat().reply([], schema=VOICE_SCHEMA), "Hi!")
        self.assertEqual(connect.call_count, 2)
        for args in connect.call_args_list:
            self.assertEqual(args.args, ("127.0.0.1", 11434))
            self.assertEqual(args.kwargs, {"timeout": 120})
        plain, structured = connection.request.call_args_list
        for request in (plain, structured):
            self.assertEqual(request.args, ("POST", "/api/chat"))
            body = json.loads(request.kwargs["body"])
            self.assertEqual(body["model"], "qwen3:1.7b")
            self.assertFalse(body["think"])
            self.assertNotIn("tools", body)
        self.assertNotIn("format", json.loads(plain.kwargs["body"]))
        self.assertEqual(json.loads(structured.kwargs["body"])["format"], VOICE_SCHEMA)

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
        audio = encode_wav(b"\x00\x20\x00\xe0" * 8000, 16000)
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("theia.assistant.dotenv_values", return_value={}),
            patch("theia.whisper.shutil.which", return_value="/path/to/whisper-cli"),
            patch("theia.whisper.Path.is_file", return_value=True),
            patch("theia.whisper.subprocess.run", return_value=SimpleNamespace(stdout=b" Check RAM.\n")) as run,
        ):
            assistant = Assistant()
            self.assertIsNone(assistant.client)
            self.assertEqual(assistant.voice_unavailable_reason, "")
            result = assistant.transcribe(audio)
            self.assertEqual(result, "Check RAM.")
            self.assertIn("RAM usage", assistant.reply(result))
            assistant.whisper.transcribe(audio, preview=True)
        final, preview = run.call_args_list
        self.assertTrue(final.args[0][2].endswith(ACCURATE_MODEL))
        self.assertIn("RAM", final.args[0][final.args[0].index("--prompt") + 1])
        self.assertTrue(preview.args[0][2].endswith(FAST_MODEL))
        self.assertEqual(final.kwargs["input"], audio)
        self.assertNotIn("OPENAI_API_KEY", final.kwargs["env"])

    def test_whisper_keeps_older_and_explicit_model_installations_usable(self):
        with patch("theia.whisper.Path.is_file", autospec=True, side_effect=lambda path: path.name == FAST_MODEL):
            whisper = WhisperTranscriber({})
            custom = WhisperTranscriber({"WHISPER_MODEL_PATH": "/custom/model.bin"})
        self.assertEqual(whisper.model.name, FAST_MODEL)
        self.assertEqual(whisper.model, whisper.preview_model)
        self.assertEqual(str(custom.model), "/custom/model.bin")
        self.assertEqual(custom.model, custom.preview_model)

    def test_whisper_rejects_silence_before_prompted_decoding(self):
        with (
            patch("theia.whisper.shutil.which", return_value="/path/to/whisper-cli"),
            patch("theia.whisper.Path.is_file", return_value=True),
            patch("theia.whisper.subprocess.run") as run,
        ):
            assistant = WhisperTranscriber({})
            for audio in (bytes(32000), b"\x01\x00\x02\x00" * 8000):
                with self.assertRaises(NoSpeechDetected):
                    assistant.transcribe(encode_wav(audio, 16000), prompt="Hey Theia.")
            run.assert_not_called()

    def test_whisper_keeps_previews_on_cpu_and_recovers_from_a_gpu_error(self):
        with (
            patch("theia.whisper.shutil.which", side_effect=lambda path: path),
            patch("theia.whisper.Path.is_file", return_value=True),
            patch("theia.whisper.run_process") as run,
        ):
            whisper = WhisperTranscriber({})
            run.side_effect = [
                SimpleNamespace(stdout=b"preview"),
                subprocess.CalledProcessError(1, "whisper-cli"),
                SimpleNamespace(stdout=b"final"),
                SimpleNamespace(stdout=b"next"),
            ]
            self.assertEqual(whisper.transcribe(b"audio", preview=True), "preview")
            self.assertEqual(whisper.transcribe(b"audio"), "final")
            self.assertEqual(whisper.transcribe(b"audio"), "next")
        preview, gpu, recovery, next_call = [call.args[0] for call in run.call_args_list]
        self.assertIn("--no-gpu", preview)
        self.assertIn("build-vulkan", gpu[0])
        self.assertNotIn("--no-gpu", gpu)
        self.assertIn("--no-gpu", recovery)
        self.assertIn("--no-gpu", next_call)

    def test_whisper_missing_binary_explains_setup(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("theia.assistant.dotenv_values", return_value={}),
            patch("theia.whisper.shutil.which", return_value=None),
        ):
            assistant = Assistant()
            self.assertIn("README", assistant.voice_unavailable_reason)
            with self.assertRaisesRegex(AssistantError, "Set up whisper.cpp"):
                assistant.transcribe(b"wav bytes")
