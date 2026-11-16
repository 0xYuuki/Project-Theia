from html import escape
from pathlib import Path
from threading import Event

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QProgressBar, QPushButton, QTextBrowser, QVBoxLayout, QWidget,
)

from jarvis import NAME
from jarvis.appearance import STYLE, BrandMark, VoiceOrb
from jarvis.assistant import Assistant
from jarvis.audio import record, speak
from jarvis.commands import system_usage
from jarvis.errors import AssistantError


VOICE_PREVIEW = "Hi, I'm Theia. I'm ready when you are."


class JobSignals(QObject):
    status = Signal(str)
    message = Signal(str, str)
    finished = Signal()


class Job(QRunnable):
    def __init__(self, assistant, message, stop, voice, preview=False):
        super().__init__()
        self.assistant = assistant
        self.message = message
        self.stop = stop
        self.voice = voice
        self.preview = preview
        self.signals = JobSignals()

    def run(self):
        try:
            if self.preview:
                speak(VOICE_PREVIEW)
                return
            message = self.message
            if self.stop is not None:
                wav = record(self.stop)
                self.signals.status.emit("Transcribing")
                message = self.assistant.transcribe(wav)
                self.signals.message.emit("You", message)
            self.signals.status.emit("Working")
            answer = self.assistant.reply(message, recorded=self.stop is not None)
            self.signals.message.emit(NAME, answer)
            if self.voice:
                self.signals.status.emit("Speaking")
                speak(answer)
        except AssistantError as error:
            self.signals.message.emit("System", str(error))
        except Exception:
            self.signals.message.emit("System", "Something went wrong while processing that request.")
        finally:
            self.signals.finished.emit()


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
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.setWindowTitle(NAME)
        self.resize(1100, 700)
        self.setMinimumSize(720, 545)

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
        mode_label = "Cloud chat configured" if assistant.client else "Offline mode"
        header.addWidget(QLabel(mode_label, objectName="chatMode"))
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

        self.add_message(NAME, "Hi, I'm Theia. Send a command or press Record to speak.")
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
        caption = QLabel(voice_mode if available else "Microphone unavailable", objectName="muted")
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        caption.setToolTip(self.assistant.voice_unavailable_reason)
        voice.addWidget(caption)

        self.mic = QPushButton("Record (5 s)", objectName="record")
        self.mic.setEnabled(available)
        self.mic.setToolTip(
            self.assistant.voice_unavailable_reason
            or "Start recording a command. Click again to finish early."
        )
        self.mic.clicked.connect(self.microphone)
        voice.addWidget(self.mic)

        controls = QHBoxLayout()
        self.voice = QCheckBox("Speak replies")
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
        colors = {"You": "#a4e1e0", NAME: "#c7b8ff", "System": "#ffc999"}
        safe_text = escape(text).replace("\n", "<br>")
        self.transcript.append(
            f'<p style="margin:8px 0 14px; line-height:145%">'
            f'<span style="color:{colors[sender]}; font-weight:700">{sender}</span>'
            f'<br>{safe_text}</p>'
        )
        scroll = self.transcript.verticalScrollBar()
        scroll.setValue(scroll.maximum())

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
        self.job = Job(self.assistant, message, stop, self.voice.isChecked(), preview=preview)
        self.job.signals.message.connect(self.add_message)
        self.job.signals.status.connect(self.set_status)
        self.job.signals.finished.connect(self.finish_job)
        self.set_busy(True)
        status = "Recording" if stop is not None else "Working"
        self.set_status("Speaking" if preview else status)
        self.pool.start(self.job)

    def set_busy(self, busy):
        self.core.active = busy
        for button in (self.send, self.preview, *self.shortcuts):
            button.setEnabled(not busy)

    def set_status(self, status):
        self.status.setText(status)
        self.status.setProperty("active", status != "Ready")
        self.status.style().polish(self.status)
        recording = status == "Recording"
        self.mic.setEnabled(recording or (status == "Ready" and not self.assistant.voice_unavailable_reason))
        self.mic.setText("Stop recording" if recording else "Record (5 s)")
        self.mic.setProperty("recording", recording)
        self.mic.style().polish(self.mic)

    def finish_job(self):
        self.job = None
        self.set_busy(False)
        self.set_status("Ready")

    def closeEvent(self, event):
        if self.job is not None:
            if self.job.stop is not None:
                self.job.stop.set()
            event.ignore()
            self.statusBar().showMessage("Wait for the current request to finish before closing.", 4000)
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
