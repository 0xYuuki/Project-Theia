import os
import shutil
import subprocess
from pathlib import Path

from theia import NAME
from theia.errors import AssistantError, NoSpeechDetected


ROOT = Path(__file__).resolve().parents[1]
LOCAL_INSTALL = ROOT / ".whisper-cpp"


class WhisperTranscriber:
    def __init__(self, settings):
        local_cli = LOCAL_INSTALL / "build" / "bin" / "whisper-cli"
        self.executable = (
            settings.get("WHISPER_CLI")
            or shutil.which(str(local_cli))
            or shutil.which("whisper-cli")
        )
        self.model = Path(
            settings.get("WHISPER_MODEL_PATH")
            or LOCAL_INSTALL / "models" / "ggml-base.en.bin"
        ).expanduser()
        if not self.model.is_absolute():
            self.model = ROOT / self.model

    @property
    def unavailable_reason(self):
        if not self.executable or not shutil.which(os.path.expanduser(self.executable)):
            return f"Set up whisper.cpp as described in the README and restart {NAME}."
        if not self.model.is_file():
            return f"Install the Whisper model as described in the README and restart {NAME}."
        return ""

    def transcribe(self, wav, *, prompt=""):
        if reason := self.unavailable_reason:
            raise AssistantError(reason)
        args = [
            os.path.expanduser(self.executable), "--model", str(self.model),
            "--file", "-", "--output-txt", "--output-file", "-", "--no-prints", "--no-gpu",
        ]
        if prompt:
            args.extend(("--prompt", prompt))
        env = {key: value for key, value in os.environ.items() if key != "OPENAI_API_KEY"}
        try:
            result = subprocess.run(
                args, input=wav, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, check=True, timeout=45,
            )
        except subprocess.TimeoutExpired as error:
            raise AssistantError("Offline transcription took too long. Try a shorter recording.") from error
        except (OSError, subprocess.CalledProcessError) as error:
            raise AssistantError("Offline transcription failed. Check the whisper.cpp installation and model.") from error

        text = " ".join(result.stdout.decode("utf-8", errors="replace").split())
        if not text:
            raise NoSpeechDetected("No speech was detected. Try again.")
        return text
