from functools import partial
from threading import Event

from PySide6.QtCore import QObject, QThreadPool, Signal

from theia.audio import encode_wav
from theia.errors import AssistantError


class PreviewDecoder(QObject):
    heard = Signal(object, str)

    def __init__(self, transcribe, parent):
        super().__init__(parent)
        self.transcribe = transcribe
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)

    def offer(self, take, audio):
        # A busy decoder skips this snapshot instead of queuing stale audio.
        self.pool.tryStart(partial(self.decode, take, audio))

    def decode(self, take, audio):
        if not take.active:
            return
        try:
            text = self.transcribe(audio, prompt=take.prompt, timeout=3, preview=True)
        except AssistantError:
            return
        if take.active and text and (take.accept is None or take.accept(text)):
            self.heard.emit(take, text)


class PreviewTake:
    def __init__(self, decoder, stop, *, prompt="", accept=None):
        self.decoder = decoder
        self.stop = stop
        self.prompt = prompt
        self.accept = accept
        self.closed = Event()
        self.next_size = 0

    @property
    def active(self):
        return not self.closed.is_set() and not self.stop.is_set()

    def offer(self, chunks, size, rate):
        interval = 2 * rate  # One second of 16-bit mono PCM.
        if size >= max(interval, self.next_size) and self.active:
            self.next_size = size + interval
            self.decoder.offer(self, encode_wav(b"".join(chunks), rate))

    def close(self):
        self.closed.set()
