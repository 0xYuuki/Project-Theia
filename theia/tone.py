import re
from dataclasses import dataclass

from theia.commands import normalize_command


@dataclass(frozen=True)
class ReplyTone:
    instruction: str
    length_scale: float | None = None


WARM = ReplyTone("Use a warm, natural tone.")
BRIGHT = ReplyTone("Share the user's enthusiasm in a short, upbeat reply.", 0.94)
GENTLE = ReplyTone("Respond gently. Briefly acknowledge the user's feelings and offer support.", 1.08)

FEELING = re.compile(
    r"(?:^|[.!?]\s*|\bbut\s+)i(?:['’]m| am| feel) (?:feeling )?"
    r"(?:(?:also|very|really|so|quite|a little|a bit) )*(\w+)"
)


def tone_for_message(message):
    feelings = set(FEELING.findall(normalize_command(message)))
    if feelings & {"sad", "upset", "frustrated", "worried", "overwhelmed", "stressed", "scared", "unhappy"}:
        return GENTLE
    if feelings & {"happy", "excited", "proud", "thrilled", "delighted"}:
        return BRIGHT
    return WARM
