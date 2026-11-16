from html import escape
from pathlib import Path
from threading import Event

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPlainTextEdit, QProgressBar, QPushButton, QTextBrowser, QVBoxLayout, QWidget,
)

from theia import NAME
from theia.appearance import STYLE, BrandMark, VoiceOrb
from theia.assistant import Assistant
from theia.audio import record, speak
from theia.commands import system_usage
from theia.errors import AssistantError, OperationCancelled
from theia.live_transcript import PreviewDecoder, PreviewTake
from theia.tone import tone_for_message
from theia.wake import FOLLOW_UP_STATUS, WakeJob


VOICE_PREVIEW = "Hi, I'm Theia. I'm ready when you are."


class JobSignals(QObject):
    status = Signal(str)
    message = Signal(str, str)
    heard = Signal(str)
    finished = Signal()


class Job(QRunnable):
    def __init__(self, assistant, message, stop, voice, preview=False, preview_decoder=None):
        super().__init__()
        self.assistant = assistant
        self.message = message
        self.stop = stop
        self.cancel = Event()
        self.voice = voice
        self.preview = preview
        self.preview_decoder = preview_decoder
        self.signals = JobSignals()

    def run(self):
        try:
            if self.cancel.is_set():
                return
            if self.preview:
                speak(VOICE_PREVIEW, cancel=self.cancel)
                return
            message = self.message
            if self.stop is not None:
                wav = self.record_voice()
                if self.cancel.is_set():
                    return
                self.signals.status.emit("Transcribing")
                message = self.assistant.transcribe(wav, cancel=self.cancel)
                if self.cancel.is_set():
                    return
                self.signals.heard.emit(message)
            self.signals.status.emit("Working")
            answer = self.assistant.prepare_reply(
                message, recorded=self.stop is not None, cancel=self.cancel, spoken=self.voice,
            )
            if self.cancel.is_set():
                return
            self.signals.message.emit(NAME, answer.text)
            if self.voice:
                self.signals.status.emit("Speaking")
                speak(answer.speech, cancel=self.cancel, length_scale=tone_for_message(message).length_scale)
        except OperationCancelled:
            pass
        except AssistantError as error:
            self.signals.message.emit("System", str(error))
        except Exception:
            self.signals.message.emit("System", "Something went wrong while processing that request.")
        finally:
            self.signals.finished.emit()

    def record_voice(self):
        if self.preview_decoder is None:
            return record(self.stop)
        take = PreviewTake(self.preview_decoder, self.stop)
        try:
            return record(self.stop, snapshot=take.offer)
        finally:
            take.close()


def make_card():
    frame = QFrame(objectName="card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(11)
    return frame, layout


def separator():
    line = QFrame(objectName="separator")
    line.setFixedHeight(1)
    return line


class Window(QMainWindow):
    def __init__(self, assistant):
        super().__init__()
        self.assistant = assistant
        self.job = None
        self.listener = None
        self.closing = False
        self.last_heard = ""
        self.preview_decoder = None
        if assistant.whisper is not None:
            self.preview_decoder = PreviewDecoder(assistant.whisper.transcribe, self)
            self.preview_decoder.heard.connect(self.show_interim)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.setWindowTitle(NAME)
        self.resize(1100, 700)
        self.setMinimumSize(720, 650)

        root = QWidget(objectName="root")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(23, 19, 23, 23)
        layout.setSpacing(17)

        header = QHBoxLayout()
        header.setSpacing(10)
        header.addWidget(BrandMark())
        header.addWidget(QLabel(NAME, objectName="brand"))
        header.addStretch()
        header.addWidget(QLabel(assistant.chat_status, objectName="chatMode"))
        layout.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(16)
        body.addWidget(self.conversation_card(), 1)
        sidebar = QVBoxLayout()
        sidebar.setContentsMargins(0, 0, 0, 0)
        sidebar.setSpacing(14)
        sidebar.addWidget(self.voice_card(), 1)
        sidebar.addWidget(self.metrics_card())
        side = QWidget()
        side.setFixedWidth(293)
        side.setLayout(sidebar)
        body.addWidget(side)
        layout.addLayout(body, 1)

        self.add_message(NAME, "Hi, I'm Theia. Type a command, press Record, or turn on hands-free listening.")
        self.monitor = QTimer(self)
        self.monitor.timeout.connect(self.update_metrics)
        self.monitor.start(1000)
        self.update_metrics()
        self.entry.setFocus()

    def conversation_card(self):
        card, chat = make_card()
        chat.addWidget(QLabel("Conversation", objectName="heading"))
        chat.addWidget(separator())

        self.transcript = QTextBrowser(objectName="transcript")
        self.transcript.setAccessibleName("Conversation")
        self.transcript.document().setDocumentMargin(4)
        chat.addWidget(self.transcript, 1)

        quick = QHBoxLayout()
        quick.setSpacing(7)
        self.shortcuts = []
        for label, command in (
            ("Check CPU", "check CPU"), ("Check memory", "check memory"), ("Commands", "help"),
        ):
            button = QPushButton(label)
            button.setProperty("command", command)
            button.clicked.connect(self.send_shortcut)
            self.shortcuts.append(button)
            quick.addWidget(button)
        quick.addStretch()
        chat.addLayout(quick)

        composer = QHBoxLayout()
        self.entry = QLineEdit()
        self.entry.setPlaceholderText(f"Message {NAME}…")
        self.entry.setAccessibleName("Message")
        self.entry.setMaxLength(4000)
        self.entry.returnPressed.connect(self.submit)
        self.send = QPushButton("Send", objectName="send")
        self.send.clicked.connect(self.submit)
        composer.addWidget(self.entry, 1)
        composer.addWidget(self.send)
        chat.addLayout(composer)
        return card

    def voice_card(self):
        card, voice = make_card()
        voice.setSpacing(9)
        heading = QHBoxLayout()
        heading.addWidget(QLabel("Voice", objectName="heading"))
        heading.addStretch()
        self.status = QLabel("Ready", objectName="status", alignment=Qt.AlignmentFlag.AlignRight)
        heading.addWidget(self.status)
        voice.addLayout(heading)

        self.core = VoiceOrb()
        voice.addWidget(self.core, 1)

        available = not self.assistant.voice_unavailable_reason
        voice_mode = "Offline transcription" if self.assistant.voice_backend == "whisper" else "OpenAI transcription"
        self.voice_mode = voice_mode if available else "Microphone unavailable"
        self.voice_caption = QLabel(self.voice_mode, objectName="muted")
        self.voice_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.voice_caption.setToolTip(self.assistant.voice_unavailable_reason)
        voice.addWidget(self.voice_caption)

        self.heard_label = QLabel("Last heard", objectName="muted")
        voice.addWidget(self.heard_label)
        self.heard_text = QPlainTextEdit(objectName="heard")
        self.heard_text.setReadOnly(True)
        self.heard_text.setAccessibleName("Voice transcript")
        self.heard_text.setPlaceholderText("Your speech will appear here.")
        self.heard_text.setFixedHeight(58)
        voice.addWidget(self.heard_text)

        self.mic = QPushButton("Record (5 s)", objectName="record")
        self.mic.setEnabled(available)
        self.mic.setToolTip(
            self.assistant.voice_unavailable_reason
            or "Start recording a command. Click again to finish early."
        )
        self.mic.clicked.connect(self.microphone)
        voice.addWidget(self.mic)

        self.hands_free = QCheckBox("Hands-free listening")
        self.hands_free.setEnabled(available and self.assistant.voice_backend == "whisper")
        self.hands_free.setToolTip(
            "Say Hey Theia, Hello Theia, or Hi Theia. Audio stays on this computer."
            if self.hands_free.isEnabled()
            else "Hands-free listening requires offline Whisper transcription."
        )
        self.hands_free.toggled.connect(self.toggle_listening)
        voice.addWidget(self.hands_free)

        controls = QHBoxLayout()
        self.voice = QCheckBox("Speak replies")
        self.voice.toggled.connect(self.update_listener_voice)
        self.preview = QPushButton("Preview voice")
        self.preview.clicked.connect(self.preview_voice)
        controls.addWidget(self.voice)
        controls.addStretch()
        controls.addWidget(self.preview)
        voice.addLayout(controls)
        return card

    def metrics_card(self):
        card, metrics = make_card()
        metrics.setSpacing(8)
        metrics.addWidget(QLabel("System monitor", objectName="heading"))
        self.metrics = []
        for name, description in (("CPU", "CPU"), ("RAM", "Memory"), ("Disk", "Disk")):
            row = QHBoxLayout()
            row.addWidget(QLabel(description, objectName="metricName"))
            row.addStretch()
            value = QLabel(objectName="metricValue")
            row.addWidget(value)
            metrics.addLayout(row)
            bar = QProgressBar(objectName=name)
            bar.setTextVisible(False)
            bar.setAccessibleName(description + " usage")
            bar.setFixedHeight(6)
            metrics.addWidget(bar)
            self.metrics.append((value, bar))
        return card

    def update_metrics(self):
        for (value, bar), usage in zip(self.metrics, system_usage()):
            value.setText(f"{usage:.0f}%")
            bar.setValue(round(usage))

    def add_message(self, sender, text):
        if self.closing:
            return
        colors = {"You": "#a4e1e0", NAME: "#c7b8ff", "System": "#ffc999"}
        safe_text = escape(text).replace("\n", "<br>")
        self.transcript.append(
            f'<p style="margin:8px 0 14px; line-height:145%">'
            f'<span style="color:{colors[sender]}; font-weight:700">{sender}</span>'
            f'<br>{safe_text}</p>'
        )
        scroll = self.transcript.verticalScrollBar()
        scroll.setValue(scroll.maximum())

    def show_voice_text(self, label, text):
        self.heard_label.setText(label)
        if self.heard_text.toPlainText() != text:
            self.heard_text.setPlainText(text)
            scroll = self.heard_text.verticalScrollBar()
            scroll.setValue(scroll.maximum())

    def show_interim(self, take, text):
        if not self.closing and take.active:
            self.show_voice_text("Hearing you", text)

    def add_voice_transcript(self, text):
        if self.closing:
            return
        self.last_heard = text
        self.show_voice_text("Last heard", text)
        self.add_message("You", text)

    def submit(self):
        message = self.entry.text().strip()
        if message and self.job is None:
            self.entry.clear()
            self.send_message(message)

    def send_message(self, message):
        if self.job is None:
            self.add_message("You", message)
            self.start_job(message, None)

    def send_shortcut(self):
        self.send_message(self.sender().property("command"))

    def microphone(self):
        if self.job is None:
            self.start_job("", Event())
        elif self.job.stop is not None:
            self.job.stop.set()
            self.mic.setEnabled(False)

    def preview_voice(self):
        if self.job is None:
            self.start_job("", None, preview=True)

    def start_job(self, message, stop, *, preview=False):
        if self.listener is not None:
            self.listener.stop.set()
        self.job = Job(
            self.assistant, message, stop, self.voice.isChecked(),
            preview=preview, preview_decoder=self.preview_decoder,
        )
        self.job.signals.message.connect(self.add_message)
        self.job.signals.heard.connect(self.add_voice_transcript)
        self.job.signals.status.connect(self.set_status)
        self.job.signals.finished.connect(self.finish_job)
        self.set_busy(True)
        status = "Recording" if stop is not None else "Working"
        self.set_status("Speaking" if preview else status)
        self.pool.start(self.job)

    def set_busy(self, busy):
        for button in (self.send, self.preview, *self.shortcuts):
            button.setEnabled(not busy)

    def set_status(self, status):
        if self.closing:
            return
        idle = status in ("Ready", "Waiting for wake phrase", FOLLOW_UP_STATUS)
        if status == "Recording" and self.preview_decoder is not None:
            self.show_voice_text("Hearing you", "")
        elif idle or status == "Stopping listener":
            self.show_voice_text("Last heard", self.last_heard)
        elif status == "Transcribing" and self.heard_label.text() == "Hearing you":
            self.heard_label.setText("Finishing transcript…")
        caption = {
            "Waiting for wake phrase": "Say “Hey Theia”",
            FOLLOW_UP_STATUS: "Ask your next question",
        }.get(status, self.voice_mode)
        self.voice_caption.setText(caption)
        self.core.active = status != "Ready"
        self.status.setText(status)
        self.status.setProperty("active", status != "Ready")
        self.status.style().polish(self.status)
        recording = status == "Recording"
        self.mic.setEnabled(recording or (idle and not self.assistant.voice_unavailable_reason))
        self.mic.setText("Stop recording" if recording else "Record (5 s)")
        self.mic.setProperty("recording", recording)
        self.mic.style().polish(self.mic)

    def finish_job(self):
        self.job = None
        if self.closing:
            self.close()
            return
        self.set_busy(False)
        self.set_status("Ready")
        self.start_listening()

    def toggle_listening(self, enabled):
        if enabled:
            self.voice.setChecked(True)
            self.start_listening()
        elif self.listener is not None:
            self.listener.stop.set()
            if self.job is None:
                self.set_status("Stopping listener")

    def update_listener_voice(self, enabled):
        if self.listener is not None:
            self.listener.voice = enabled

    def start_listening(self):
        if not self.hands_free.isChecked() or self.job is not None or self.listener is not None:
            return
        self.listener = WakeJob(self.assistant, self.voice.isChecked(), self.preview_decoder)
        self.listener.signals.status.connect(self.listener_status)
        self.listener.signals.message.connect(self.listener_message)
        self.listener.signals.heard.connect(self.listener_heard)
        self.listener.signals.failed.connect(self.listener_failed)
        self.listener.signals.finished.connect(self.finish_listening)
        self.set_status("Waiting for wake phrase")
        self.pool.start(self.listener)

    def listener_status(self, status):
        if self.listener is not None and not self.listener.stop.is_set():
            self.set_status(status)

    def listener_message(self, sender, text):
        if self.listener is not None and not self.listener.stop.is_set():
            self.add_message(sender, text)

    def listener_heard(self, text):
        if self.listener is not None and not self.listener.stop.is_set():
            self.add_voice_transcript(text)

    def listener_failed(self, message):
        if self.listener is not None and not self.listener.stop.is_set():
            self.add_message("System", message)
            self.hands_free.setChecked(False)

    def finish_listening(self):
        self.listener = None
        if self.closing and self.job is None:
            self.close()
        elif self.job is None:
            self.set_status("Ready")
            self.start_listening()

    def closeEvent(self, event):
        if self.job is not None or self.listener is not None:
            self.closing = True
            self.hands_free.setChecked(False)
            if self.listener is not None:
                self.listener.stop.set()
            if self.job is not None:
                self.job.cancel.set()
                if self.job.stop is not None:
                    self.job.stop.set()
            self.status.setText("Closing…")
            self.centralWidget().setEnabled(False)
            event.ignore()
            return
        super().closeEvent(event)


def style_application(app):
    app.setApplicationName(NAME)
    app.setStyle("Fusion")
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Base, QColor("#20243a"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#e9e9f5"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#a593f5"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#17112d"))
    app.setPalette(palette)
    font_id = QFontDatabase.addApplicationFont(str(Path(__file__).parent / "assets" / "Inter.ttf"))
    families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
    if families:
        app.setFont(QFont(families[0]))
    app.setStyleSheet(STYLE)


def run():
    app = QApplication([])
    style_application(app)
    window = Window(Assistant())
    window.show()
    return app.exec()
