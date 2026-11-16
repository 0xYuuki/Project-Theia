import re
from threading import Event

from PySide6.QtCore import QObject, QRunnable, Signal

from theia import NAME
from theia.audio import listen_for_speech, speak
from theia.errors import AssistantError, NoSpeechDetected, OperationCancelled, TranscriptionTimeout
from theia.live_transcript import PreviewTake
from theia.tone import tone_for_message


WAKE_PROMPT = "Hey Theia. Hello Theia. Hi Theia."
WAKE_PATTERN = re.compile(r"^\s*(?:hey|hello|hi)\b[\s,.!?—-]+(?:theia|thea)\b", re.IGNORECASE)


def wake_command(transcript):
    match = WAKE_PATTERN.match(transcript)
    if match is None:
        return None
    if transcript[match.end():].startswith(("'", "’")):
        return None
    return transcript[match.end():].strip(" ,.!?:;—-\t\n")


class WakeSignals(QObject):
    status = Signal(str)
    message = Signal(str, str)
    heard = Signal(str)
    failed = Signal(str)
    finished = Signal()


class WakeJob(QRunnable):
    def __init__(self, assistant, voice, preview_decoder):
        super().__init__()
        self.assistant = assistant
        self.transcribe = assistant.whisper.transcribe
        self.voice = voice
        self.preview_decoder = preview_decoder
        self.stop = Event()
        self.signals = WakeSignals()

    def run(self):
        try:
            self.listen()
        except OperationCancelled:
            pass
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
            transcript = self.hear("Waiting for wake phrase", wake_only=True)
            command = wake_command(transcript)
            if command is None or self.stop.is_set():
                continue
            self.signals.heard.emit(transcript)
            if not command:
                self.respond("I'm listening.")
                command = self.hear("Listening for command", start_timeout=7)
                if not command or self.stop.is_set():
                    continue
                self.signals.heard.emit(command)

            if not self.stop.is_set():
                self.signals.status.emit("Working")
                try:
                    answer = self.assistant.reply(command, recorded=True, cancel=self.stop)
                except AssistantError as error:
                    self.signals.message.emit("System", str(error))
                else:
                    self.respond(answer, length_scale=tone_for_message(command).length_scale)

    def hear(self, status, *, wake_only=False, start_timeout=None):
        if self.stop.is_set():
            return ""
        self.signals.status.emit(status)
        prompt = WAKE_PROMPT if wake_only else ""
        accept = (lambda text: wake_command(text) is not None) if wake_only else None
        take = PreviewTake(self.preview_decoder, self.stop, prompt=prompt, accept=accept)
        try:
            audio = listen_for_speech(self.stop, start_timeout=start_timeout, snapshot=take.offer)
        finally:
            take.close()
        if audio is None or self.stop.is_set():
            return ""
        self.signals.status.emit("Transcribing")
        try:
            return self.transcribe(audio, prompt=prompt, cancel=self.stop)
        except NoSpeechDetected:
            return ""
        except TranscriptionTimeout as error:
            self.signals.message.emit("System", str(error))
            return ""

    def respond(self, text, *, length_scale=None):
        if self.stop.is_set():
            return
        self.signals.message.emit(NAME, text)
        if self.voice:
            self.signals.status.emit("Speaking")
            try:
                speak(text, cancel=self.stop, length_scale=length_scale)
            except AssistantError as error:
                self.signals.message.emit("System", str(error))
            self.stop.wait(0.15)
