import queue
import re
from collections import deque
from threading import Event
from time import monotonic

from PySide6.QtCore import QObject, QRunnable, Signal

from theia import NAME
from theia.audio import encode_wav, speak
from theia.errors import AssistantError, NoSpeechDetected


WAKE_PROMPT = "Hey Theia. Hello Theia. Hi Theia."
WAKE_PATTERN = re.compile(r"^\s*(?:hey|hello|hi)\b[\s,.!?—-]+(?:theia|thea)\b", re.IGNORECASE)
FRAME_MS = 20
LEAD_FRAMES = 15
END_SILENCE_FRAMES = 35
MAX_CLIP_FRAMES = 400


def wake_command(transcript):
    match = WAKE_PATTERN.match(transcript)
    if match is None:
        return None
    if transcript[match.end():].startswith(("'", "’")):
        return None
    return transcript[match.end():].strip(" ,.!?:;—-\t\n")


def listen_for_speech(stop, *, start_timeout=None):
    try:
        import sounddevice as sd
        import webrtcvad
    except (ImportError, OSError) as error:
        raise AssistantError("Hands-free listening needs the project requirements and PortAudio.") from error

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
            return collect_speech(frames, stream, webrtcvad.Vad(2), rate, stop, start_timeout)
    except (sd.PortAudioError, ValueError) as error:
        raise AssistantError("Couldn't open the microphone. Check Fedora Sound settings.") from error


def collect_speech(frames, stream, vad, rate, stop, start_timeout):
    deadline = None if start_timeout is None else monotonic() + start_timeout
    lead = deque(maxlen=LEAD_FRAMES)
    clip = []
    silence = 0
    while not stop.is_set():
        if not clip and deadline is not None and monotonic() >= deadline:
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
            if silence >= END_SILENCE_FRAMES or len(clip) >= MAX_CLIP_FRAMES:
                return encode_wav(b"".join(clip), rate)
        else:
            lead.append((frame, voiced))
            if voiced and sum(is_voiced for _, is_voiced in lead) >= 5:
                clip = [part for part, _ in lead]
    return None


class WakeSignals(QObject):
    status = Signal(str)
    message = Signal(str, str)
    failed = Signal(str)
    finished = Signal()


class WakeJob(QRunnable):
    def __init__(self, assistant, voice):
        super().__init__()
        self.assistant = assistant
        self.transcribe = assistant.whisper.transcribe
        self.voice = voice
        self.stop = Event()
        self.signals = WakeSignals()

    def run(self):
        try:
            self.listen()
        except AssistantError as error:
            if not self.stop.is_set():
                self.signals.failed.emit(str(error))
        except Exception:
            if not self.stop.is_set():
                self.signals.failed.emit("Hands-free listening stopped unexpectedly.")
        finally:
            self.signals.finished.emit()

    def listen(self):
        while not self.stop.is_set():
            transcript = self.hear("Waiting for wake phrase", prompt=WAKE_PROMPT)
            command = wake_command(transcript)
            if command is None or self.stop.is_set():
                continue
            self.signals.message.emit("You", transcript)
            if not command:
                self.respond("I'm listening.")
                command = self.hear("Listening for command", start_timeout=7)
                if not command:
                    continue
                self.signals.message.emit("You", command)

            if not self.stop.is_set():
                self.signals.status.emit("Working")
                self.respond(self.assistant.reply(command, recorded=True))

    def hear(self, status, *, prompt="", start_timeout=None):
        if self.stop.is_set():
            return ""
        self.signals.status.emit(status)
        audio = listen_for_speech(self.stop, start_timeout=start_timeout)
        if audio is None or self.stop.is_set():
            return ""
        self.signals.status.emit("Transcribing")
        try:
            return self.transcribe(audio, prompt=prompt)
        except NoSpeechDetected:
            return ""

    def respond(self, text):
        if self.stop.is_set():
            return
        self.signals.message.emit(NAME, text)
        if self.voice:
            self.signals.status.emit("Speaking")
            try:
                speak(text)
            except AssistantError as error:
                self.signals.message.emit("System", str(error))
            self.stop.wait(0.15)
