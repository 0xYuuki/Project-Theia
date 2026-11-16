class AssistantError(Exception):
    pass


class NoSpeechDetected(AssistantError):
    pass


class TranscriptionTimeout(AssistantError):
    pass


class OperationCancelled(Exception):
    pass
