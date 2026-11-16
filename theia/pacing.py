from contextlib import contextmanager
from threading import Event, Timer

from theia.errors import AssistantError, OperationCancelled


CHECKING_FILE = "Checking file"
ACKNOWLEDGMENT = "Give me a second to check."


class FileFeedback:
    def __init__(self, status, speak, cancel, *, voice):
        self.status = status
        self.speak = speak
        self.cancel = cancel
        self.voice = voice
        self.answered = Event()

    def is_set(self):
        return self.cancel.is_set() or self.answered.is_set()

    def acknowledge(self):
        if not self.is_set():
            try:
                self.speak(ACKNOWLEDGMENT, cancel=self)
            except (AssistantError, OperationCancelled):
                pass

    @contextmanager
    def checking(self, *, delay=2.5):
        self.status(CHECKING_FILE)
        timer = None
        if self.voice:
            timer = Timer(delay, self.acknowledge)
            timer.start()
        try:
            yield
        finally:
            self.answered.set()
            if timer is not None:
                timer.cancel()
                timer.join()
