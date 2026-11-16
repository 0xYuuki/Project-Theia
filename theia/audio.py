import io
import os
import queue
import shutil
import subprocess
import time
import wave
from collections import deque
from functools import cache
from pathlib import Path

from PySide6.QtGui import QTextDocument

from theia.errors import AssistantError, NoSpeechDetected, OperationCancelled
from theia.process import run_process


VOICE_MODEL = Path(__file__).resolve().parents[1] / ".piper-voices" / "en_US-kristin-medium.onnx"
FRAME_MS = 20
LEAD_FRAMES = 15
MAX_CLIP_FRAMES = 400


def record(stop, seconds=5, *, snapshot=None):
    wav = listen_for_speech(stop, snapshot=snapshot, duration=seconds)
    if wav is None:
        raise NoSpeechDetected("No speech was detected. Try again.")
    return wav


def listen_for_speech(stop, *, start_timeout=None, snapshot=None, duration=None):
    try:
        import sounddevice as sd
        import webrtcvad
    except (ImportError, OSError) as error:
        raise AssistantError("Voice recording needs the project requirements and PortAudio.") from error

    frames = queue.Queue(maxsize=100)

    def capture(data, frame_count, time_info, status):
        try:
            frames.put_nowait(bytes(data))
        except queue.Full:
            pass

    try:
        default_rate = int(sd.query_devices(kind="input")["default_samplerate"])
        rate = default_rate if default_rate in (8000, 16000, 32000, 48000) else 16000
        with sd.RawInputStream(
            samplerate=rate, channels=1, dtype="int16",
            blocksize=rate * FRAME_MS // 1000, callback=capture,
        ) as stream:
            return collect_speech(frames, stream, webrtcvad.Vad(2), rate, stop, start_timeout, snapshot, duration=duration)
    except (sd.PortAudioError, ValueError) as error:
        raise AssistantError("Couldn't open the microphone. Check Fedora Sound settings.") from error


def collect_speech(frames, stream, vad, rate, stop, start_timeout, snapshot=None, *, duration=None):
    started = time.monotonic()
    deadline = None if start_timeout is None else started + start_timeout
    end = None if duration is None else started + duration
    end_silence_frames = 1000 // FRAME_MS
    lead = deque(maxlen=LEAD_FRAMES)
    clip = []
    silence = 0

    def finish():
        trim = max(0, silence - 10)
        return encode_wav(b"".join(clip[:-trim] if trim else clip), rate)

    while not stop.is_set():
        now = time.monotonic()
        if end is not None and now >= end:
            break
        if not clip and deadline is not None and now >= deadline:
            return None
        try:
            frame = frames.get(timeout=0.1)
        except queue.Empty:
            if not stream.active:
                raise AssistantError("The microphone disconnected.")
            continue

        voiced = vad.is_speech(frame, rate)
        if clip:
            clip.append(frame)
            silence = 0 if voiced else silence + 1
            if silence >= end_silence_frames or len(clip) >= MAX_CLIP_FRAMES:
                return finish()
            if snapshot is not None and voiced:
                snapshot(clip, len(clip) * len(frame), rate)
        else:
            lead.append((frame, voiced))
            if voiced and sum(is_voiced for _, is_voiced in lead) >= 5:
                clip = [part for part, _ in lead]
    if end is not None and clip:
        return finish()
    return None


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


def synthesize_speech(message, *, length_scale=None):
    voice = load_voice()
    synthesis_config = None
    if length_scale is not None:
        from piper import SynthesisConfig

        synthesis_config = SynthesisConfig(length_scale=length_scale)
    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav:
        voice.synthesize_wav(message, wav, syn_config=synthesis_config)
    return audio.getvalue()


def render_speech(message, *, length_scale=None):
    document = QTextDocument()
    document.setLayoutEnabled(False)
    features = QTextDocument.MarkdownFeature
    document.setMarkdown(message, features.MarkdownDialectGitHub | features.MarkdownNoHTML)
    spoken_text = document.toPlainText().replace("\ufffc", " ").strip()
    return synthesize_speech(spoken_text, length_scale=length_scale) if spoken_text else b""


def target_args(target):
    return ["--target", target, "-P", '{"node.dont-fallback":true,"node.dont-reconnect":true}']


def play_audio(audio, *, cancel=None, target=None):
    executable = shutil.which("pw-play")
    if not executable:
        raise AssistantError("Install pipewire-utils to use spoken replies.")
    args = [executable]
    if target is not None:
        args.extend(target_args(target))
    env = {key: value for key, value in os.environ.items() if key != "OPENAI_API_KEY"}
    try:
        run_process(
            [*args, "-"], input=audio, env=env, cancel=cancel,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise AssistantError("Speech playback failed. Check your audio output.") from error


def speak(message, *, cancel=None, length_scale=None):
    if cancel is not None and cancel.is_set():
        raise OperationCancelled
    audio = render_speech(message, length_scale=length_scale)
    if audio:
        play_audio(audio, cancel=cancel)
