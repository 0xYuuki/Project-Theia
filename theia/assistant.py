import os
from pathlib import Path

from dotenv import dotenv_values
from openai import APIError, OpenAI, RateLimitError

from theia import NAME
from theia.commands import local_reply
from theia.errors import AssistantError, OperationCancelled
from theia.ollama import DEFAULT_MODEL, OllamaChat
from theia.whisper import WhisperTranscriber


CHAT_INSTRUCTIONS = (
    f"You are {NAME}, a concise desktop assistant. Answer in plain text. "
    "You have no direct access to desktop actions or live system readings. "
    "If asked for those, suggest the built-in commands."
)


def api_error_message(error, action):
    if isinstance(error, RateLimitError):
        reason = {
            "credit_balance_exhausted": "Check your OpenAI API billing and available credits.",
            "insufficient_quota": "Check your OpenAI API credits and usage limits.",
            "billing_hard_limit_reached": "Check your OpenAI API spending limit.",
            "organization_spend_limit_exceeded": "Check your OpenAI organization spending limit.",
            "project_spend_limit_exceeded": "Check your OpenAI project spending limit.",
            "organization_usage_limit_exceeded": "Check your OpenAI organization usage limit.",
            "rate_limit_exceeded": "OpenAI is limiting requests. Wait briefly and try again.",
            "slow_down": "OpenAI is limiting requests. Wait briefly and try again.",
        }.get(error.code, "Check your OpenAI API credits and limits, then try again.")
        return f"{action} failed (OpenAI HTTP 429). {reason}"
    return f"{action} failed. Check your connection, API key, and model access."


class Assistant:
    def __init__(self):
        settings = {
            **dotenv_values(Path(__file__).resolve().parents[1] / ".env"),
            **os.environ,
        }
        self.chat_model = settings.get("OPENAI_CHAT_MODEL") or "gpt-4.1-mini"
        self.transcription_model = settings.get("OPENAI_TRANSCRIPTION_MODEL") or "gpt-4o-mini-transcribe"
        self.history = []
        self.chat_backend = (settings.get("THEIA_CHAT_BACKEND") or "off").strip().casefold()
        self.ollama = None
        if self.chat_backend == "ollama":
            model = (settings.get("THEIA_OLLAMA_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
            self.ollama = OllamaChat(model)
        backend = settings.get("THEIA_VOICE_BACKEND") or settings.get("JARVIS_VOICE_BACKEND") or "whisper"
        self.voice_backend = backend.strip().casefold()
        self.whisper = WhisperTranscriber(settings) if self.voice_backend == "whisper" else None
        key = settings.get("OPENAI_API_KEY")
        self.client = None
        if key and "openai" in (self.chat_backend, self.voice_backend):
            self.client = OpenAI(api_key=key, timeout=30, max_retries=0)

    @property
    def chat_status(self):
        if self.ollama is not None:
            return "Local AI"
        if self.chat_backend == "openai" and self.client is not None:
            return "OpenAI chat"
        return "Local commands"

    @property
    def voice_unavailable_reason(self):
        if self.whisper is not None:
            return self.whisper.unavailable_reason
        if self.voice_backend != "openai":
            return f"Set THEIA_VOICE_BACKEND to whisper or openai in .env and restart {NAME}."
        if self.client is None:
            return "Set OPENAI_API_KEY in .env and restart to enable OpenAI transcription."
        return ""

    def reply(self, message, *, recorded=False, cancel=None):
        if cancel is not None and cancel.is_set():
            raise OperationCancelled
        response = local_reply(message)
        if response is not None:
            return response
        prompt = {"role": "user", "content": message}
        messages = [*self.history, prompt]
        if self.ollama is not None:
            answer = self.ollama.reply([{"role": "system", "content": CHAT_INSTRUCTIONS}, *messages])
        elif recorded:
            return "I couldn't match that to a local command. Try saying “check memory” or “help.”"
        elif self.chat_backend == "openai":
            answer = self.openai_reply(messages)
        else:
            raise AssistantError("Set THEIA_CHAT_BACKEND to ollama or openai in .env and restart Theia to enable AI chat.")
        if cancel is not None and cancel.is_set():
            raise OperationCancelled
        self.history = [*messages, {"role": "assistant", "content": answer}][-12:]
        return answer

    def openai_reply(self, messages):
        if self.client is None:
            raise AssistantError("Set OPENAI_API_KEY in .env and restart to enable AI chat.")
        try:
            result = self.client.responses.create(
                model=self.chat_model,
                instructions=CHAT_INSTRUCTIONS,
                input=messages,
                store=False,
            )
        except APIError as error:
            raise AssistantError(api_error_message(error, "AI request")) from error
        answer = result.output_text.strip()
        if not answer:
            raise AssistantError("The AI returned an empty reply.")
        return answer

    def transcribe(self, wav, *, cancel=None):
        if self.whisper is not None:
            return self.whisper.transcribe(wav, cancel=cancel)
        if reason := self.voice_unavailable_reason:
            raise AssistantError(reason)
        try:
            result = self.client.audio.transcriptions.create(
                model=self.transcription_model, file=("recording.wav", wav, "audio/wav")
            )
        except APIError as error:
            raise AssistantError(api_error_message(error, "Transcription")) from error
        if not result.text.strip():
            raise AssistantError("No speech was detected. Try again.")
        return result.text.strip()
