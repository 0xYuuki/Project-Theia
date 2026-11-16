import os
import shutil
import subprocess
import wave
from array import array
from io import BytesIO
from pathlib import Path

from theia import NAME
from theia.errors import AssistantError, NoSpeechDetected


ROOT = Path(__file__).resolve().parents[1]
LOCAL_INSTALL = ROOT / ".whisper-cpp"
ACCURATE_MODEL = "ggml-small.en-q5_1.bin"
FAST_MODEL = "ggml-base.en.bin"
COMMAND_TERMS = "Theia, CPU, RAM, memory, disk, Firefox, terminal, calculator."


def is_silent_wav(wav):
    try:
        with wave.open(BytesIO(wav), "rb") as audio:
            if audio.getsampwidth() != 2:
                return False
            samples = array("h", audio.readframes(audio.getnframes()))
    except (EOFError, ValueError, wave.Error):
        return False
    # Reject essentially flat audio before a vocabulary prompt can invent speech.
    return max(samples, default=0) - min(samples, default=0) <= 16


class WhisperTranscriber:
    def __init__(self, settings):
        local_cli = LOCAL_INSTALL / "build" / "bin" / "whisper-cli"
        self.executable = (
            settings.get("WHISPER_CLI")
            or shutil.which(str(local_cli))
            or shutil.which("whisper-cli")
        )
        models = LOCAL_INSTALL / "models"
        accurate_model = models / ACCURATE_MODEL
        fast_model = models / FAST_MODEL
        configured_model = settings.get("WHISPER_MODEL_PATH")
        default_model = accurate_model if accurate_model.is_file() else fast_model
        self.model = Path(configured_model or default_model).expanduser()
        if not self.model.is_absolute():
            self.model = ROOT / self.model
        self.preview_model = fast_model if not configured_model and fast_model.is_file() else self.model

    @property
    def unavailable_reason(self):
        if not self.executable or not shutil.which(os.path.expanduser(self.executable)):
            return f"Set up whisper.cpp as described in the README and restart {NAME}."
        if not self.model.is_file():
            return f"Install the Whisper model as described in the README and restart {NAME}."
        return ""

    def transcribe(self, wav, *, prompt="", timeout=45, preview=False):
        if reason := self.unavailable_reason:
            raise AssistantError(reason)
        if is_silent_wav(wav):
            raise NoSpeechDetected("No speech was detected. Try again.")
        model = self.preview_model if preview else self.model
        args = [
            os.path.expanduser(self.executable), "--model", str(model),
            "--file", "-", "--output-txt", "--output-file", "-", "--no-prints", "--no-gpu",
            "--prompt", " ".join(filter(None, (prompt, COMMAND_TERMS))),
        ]
        env = {key: value for key, value in os.environ.items() if key != "OPENAI_API_KEY"}
        try:
            result = subprocess.run(
                args, input=wav, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, check=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired as error:
            raise AssistantError("Offline transcription took too long. Try a shorter recording.") from error
        except (OSError, subprocess.CalledProcessError) as error:
            raise AssistantError("Offline transcription failed. Check the whisper.cpp installation and model.") from error

        text = " ".join(result.stdout.decode("utf-8", errors="replace").split())
        if not text:
            raise NoSpeechDetected("No speech was detected. Try again.")
        return text
