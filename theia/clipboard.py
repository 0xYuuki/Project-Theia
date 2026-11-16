from dataclasses import dataclass, field
from threading import Event
from time import monotonic

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWidgets import QApplication

from theia.errors import AssistantError, OperationCancelled


@dataclass
class ClipboardRequest:
    cancel: object
    ready: Event = field(default_factory=Event)
    abandoned: Event = field(default_factory=Event)
    text: str = ""


class ClipboardReader(QObject):
    requested = Signal(object)

    def __init__(self, parent):
        super().__init__(parent)
        self.requested.connect(self.read_on_ui)

    def read(self, cancel=None):
        request = ClipboardRequest(cancel)
        deadline = monotonic() + 3
        self.requested.emit(request)
        while not request.ready.wait(0.05):
            if cancel is not None and cancel.is_set():
                request.abandoned.set()
                raise OperationCancelled
            if monotonic() >= deadline:
                request.abandoned.set()
                raise AssistantError("Theia couldn't read the clipboard. Try copying the text again.")
        return request.text

    @Slot(object)
    def read_on_ui(self, request):
        if not request.abandoned.is_set() and (request.cancel is None or not request.cancel.is_set()):
            clipboard = QApplication.clipboard()
            if clipboard.mimeData() is not None and clipboard.mimeData().hasText():
                request.text = clipboard.text()
        request.ready.set()
