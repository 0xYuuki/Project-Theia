import io
import os
import queue
import shutil
import subprocess
import time
import wave
from functools import cache
from pathlib import Path

from PySide6.QtGui import QTextDocument

from theia.errors import AssistantError


VOICE_MODEL = Path(__file__).resolve().parents[1] / ".piper-voices" / "en_US-kristin-medium.onnx"


def record(stop, seconds=5, *, snapshot=None):
    try:
        import sounddevice as sd
    except (ImportError, OSError) as error:
        raise AssistantError("Microphone support needs PortAudio on Fedora.") from error

    frames = queue.SimpleQueue()
    chunks = []
    size = 0

    def drain_frames():
        nonlocal size
        while not frames.empty():
            chunk = frames.get_nowait()
            chunks.append(chunk)
            size += len(chunk)

    def capture(data, frame_count, time_info, status):
        frames.put(bytes(data))

    try:
        device = sd.query_devices(kind="input")
        rate = int(device["default_samplerate"])
        with sd.RawInputStream(samplerate=rate, channels=1, dtype="int16", callback=capture):
            deadline = time.monotonic() + seconds
            while not stop.wait(0.05) and time.monotonic() < deadline:
                drain_frames()
                if snapshot is not None:
                    snapshot(chunks, size, rate)
    except (sd.PortAudioError, ValueError) as error:
        raise AssistantError("Could not open a microphone. Check the default input in Fedora Sound settings.") from error

    drain_frames()
    if size < 2 * (rate // 5):
        raise AssistantError("The recording was too short. Try again.")
    return encode_wav(b"".join(chunks), rate)


def encode_wav(pcm, rate):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        wav.writeframes(pcm)
    return buffer.getvalue()


@cache
def load_voice():
    if not VOICE_MODEL.is_file() or not VOICE_MODEL.with_suffix(".onnx.json").is_file():
        raise AssistantError("Download the Piper voice from the setup instructions in the README.")
    try:
        from piper import PiperVoice
    except (ImportError, OSError) as error:
        raise AssistantError("Install the project requirements to use Theia's voice.") from error
    try:
        return PiperVoice.load(VOICE_MODEL)
    except (OSError, ValueError, RuntimeError) as error:
        raise AssistantError("Couldn't load the Piper voice. Try downloading the voice model again.") from error


def synthesize_speech(message):
    voice = load_voice()
    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav:
        voice.synthesize_wav(message, wav)
    return audio.getvalue()


def speak(message):
    document = QTextDocument()
    document.setLayoutEnabled(False)
    features = QTextDocument.MarkdownFeature
    document.setMarkdown(message, features.MarkdownDialectGitHub | features.MarkdownNoHTML)
    spoken_text = document.toPlainText().replace("\ufffc", " ").strip()
    if not spoken_text:
        return

    executable = shutil.which("pw-play")
    if not executable:
        raise AssistantError("Install pipewire-utils to use spoken replies.")
    audio = synthesize_speech(spoken_text)
    env = {key: value for key, value in os.environ.items() if key != "OPENAI_API_KEY"}
    try:
        subprocess.run(
            [executable, "-"], input=audio, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90, check=True,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise AssistantError("Speech playback failed. Check your audio output.") from error
