import re
from queue import Empty
from threading import Event

from PySide6.QtCore import QObject, QRunnable, Signal

from theia import NAME
from theia.audio import listen_for_speech, speak
from theia.commands import normalize_command
from theia.errors import AssistantError, NoSpeechDetected, OperationCancelled, TranscriptionTimeout
from theia.live_transcript import PreviewTake
from theia.pacing import FileFeedback
from theia.reminders import ReminderTurn


WAKE_PROMPT = "Hey Theia. Hello Theia. Hi Theia."
WAKE_PATTERN = re.compile(r"^\s*(?:hey|hello|hi)\b[\s,.!?—-]+(?:theia|thea)\b", re.IGNORECASE)
FOLLOW_UP_STATUS = "Listening for follow-up"
END_PHRASES = {"that's all", "goodbye", "end conversation"}


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
    def __init__(self, assistant, voice, preview_decoder, announcements):
        super().__init__()
        self.assistant = assistant
        self.transcribe = assistant.whisper.transcribe
        self.voice = voice
        self.preview_decoder = preview_decoder
        self.announcements = announcements
        self.stop = Event()
        self.capture = None
        self.playback = None
        self.signals = WakeSignals()

    def abort(self):
        self.stop.set()
        self.announcement_ready()
        self.stop_speaking()

    def announcement_ready(self):
        capture = self.capture
        if capture is not None:
            capture.set()

    def stop_speaking(self):
        playback = self.playback
        if playback is not None:
            playback.set()

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
            self.converse(command)

    def converse(self, command):
        reminder_turn = ReminderTurn()
        while not self.stop.is_set():
            if not command:
                self.respond("I'm listening.")
            elif normalize_command(command).replace("’", "'") in END_PHRASES:
                self.respond("Talk to you later.")
                return
            else:
                self.signals.status.emit("Thinking")
                spoken = self.voice
                feedback = FileFeedback(self.signals.status.emit, speak, self.stop, voice=spoken)
                try:
                    answer = self.assistant.prepare_reply(
                        command, recorded=True, cancel=self.stop, spoken=spoken,
                        reminder_turn=reminder_turn, checking=feedback.checking,
                    )
                except AssistantError as error:
                    self.signals.message.emit("System", str(error))
                    return
                self.respond(
                    answer.text, speech=answer.speech if spoken else "", message=command,
                )

            transcript = self.hear(FOLLOW_UP_STATUS, start_timeout=12, prompt=reminder_turn.transcription_prompt)
            if not transcript or self.stop.is_set():
                return
            self.signals.heard.emit(transcript)
            addressed = wake_command(transcript)
            command = transcript if addressed is None else addressed

    def hear(self, status, *, wake_only=False, start_timeout=None, prompt=""):
        prompt = WAKE_PROMPT if wake_only else prompt
        accept = (lambda text: wake_command(text) is not None) if wake_only else None
        while not self.stop.is_set():
            self.capture = capture = Event()
            try:
                self.announce_reminders()
                if self.stop.is_set():
                    return ""
                if capture.is_set():
                    continue
                self.signals.status.emit(status)
                take = PreviewTake(self.preview_decoder, capture, prompt=prompt, accept=accept)
                try:
                    audio = listen_for_speech(capture, start_timeout=start_timeout, snapshot=take.offer)
                finally:
                    take.close()
            finally:
                self.capture = None
            if audio is not None or not capture.is_set():
                break
        else:
            return ""
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

    def announce_reminders(self):
        while not self.stop.is_set():
            try:
                announcement = self.announcements.get_nowait()
            except Empty:
                return
            if not self.say(announcement):
                self.announcements.put(announcement)
                return

    def respond(self, text, *, speech=None, message=""):
        if self.stop.is_set():
            return
        self.signals.message.emit(NAME, text)
        spoken_text = text if speech is None else speech
        if self.voice and spoken_text:
            self.say(spoken_text, message=message)

    def say(self, text, *, message=""):
        self.playback = Event()
        if self.stop.is_set():
            return False
        self.signals.status.emit("Speaking")
        try:
            pace = self.assistant.speaking_pace(message)
            interruption = self.assistant.interruption
            route = interruption.route if message else None
            if route is not None:
                try:
                    interruption.play(text, self.assistant.whisper, route, self.stop, self.playback, length_scale=pace)
                except AssistantError as error:
                    interruption.disable()
                    self.signals.message.emit("System", f"Spoken interruption turned off. {error}")
                    if not self.playback.is_set() and not self.stop.is_set():
                        speak(text, cancel=self.playback, length_scale=pace)
            else:
                speak(text, cancel=self.playback, length_scale=pace)
        except OperationCancelled:
            pass
        except AssistantError as error:
            self.signals.message.emit("System", str(error))
        self.stop.wait(0.15)
        return True
