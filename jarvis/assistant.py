import os
from pathlib import Path

from dotenv import dotenv_values
from openai import APIError, OpenAI, RateLimitError

from jarvis import NAME
from jarvis.commands import local_reply
from jarvis.errors import AssistantError
from jarvis.whisper import WhisperTranscriber


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
        key = settings.get("OPENAI_API_KEY")
        self.client = OpenAI(api_key=key, timeout=30, max_retries=0) if key else None
        self.history = []
        backend = settings.get("THEIA_VOICE_BACKEND") or settings.get("JARVIS_VOICE_BACKEND") or "whisper"
        self.voice_backend = backend.strip().casefold()
        self.whisper = WhisperTranscriber(settings) if self.voice_backend == "whisper" else None

    @property
    def voice_unavailable_reason(self):
        if self.whisper is not None:
            return self.whisper.unavailable_reason
        if self.voice_backend != "openai":
            return f"Set THEIA_VOICE_BACKEND to whisper or openai in .env and restart {NAME}."
        if self.client is None:
            return "Set OPENAI_API_KEY in .env and restart to enable OpenAI transcription."
        return ""

    def reply(self, message, *, recorded=False):
        response = local_reply(message)
        if response is not None:
            return response
        if recorded and self.voice_backend == "whisper":
            return "I couldn't match that to a local command. Try saying “check memory” or “help.”"
        if self.client is None:
            raise AssistantError("Set OPENAI_API_KEY in .env and restart to enable AI chat.")
        prompt = {"role": "user", "content": message}
        try:
            result = self.client.responses.create(
                model=self.chat_model,
                instructions=f"You are {NAME}, a concise desktop assistant. You cannot perform actions on this computer.",
                input=[*self.history, prompt],
                store=False,
            )
        except APIError as error:
            raise AssistantError(api_error_message(error, "AI request")) from error
        answer = result.output_text.strip()
        if not answer:
            raise AssistantError("The AI returned an empty reply.")
        self.history = [*self.history, prompt, {"role": "assistant", "content": answer}][-12:]
        return answer

    def transcribe(self, wav):
        if self.whisper is not None:
            return self.whisper.transcribe(wav)
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
