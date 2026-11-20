import os
import re
import shutil
import sqlite3
import subprocess
import time
from contextlib import closing, contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape
from pathlib import Path

from theia.errors import AssistantError


DURATION = re.compile(r"(\d{1,6}|[a-z]+)\s*(seconds?|minutes?|hours?)")
CLOCK = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*(a\.?\s*m\.?|p\.?\s*m\.?)?")
NUMBERS = {
    word: number for number, word in enumerate(
        "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
        "fifteen sixteen seventeen eighteen nineteen twenty".split()
    )
}


@dataclass(frozen=True)
class Reminder:
    id: int
    due: float
    label: str


class Reminders:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def database(self, *, create=False):
        try:
            if create:
                self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            uri = self.path.resolve().as_uri() + ("?mode=rwc" if create else "?mode=rw")
            with closing(sqlite3.connect(uri, timeout=5.0, uri=True)) as connection, connection:
                if create:
                    connection.execute(
                        "CREATE TABLE IF NOT EXISTS reminders("
                        "id INTEGER PRIMARY KEY AUTOINCREMENT, due REAL NOT NULL, label TEXT NOT NULL)"
                    )
                yield connection
        except (OSError, sqlite3.Error) as error:
            raise AssistantError("Theia couldn't access the local reminders. Check the configuration folder.") from error

    def add(self, due, label):
        with self.database(create=True) as db:
            db.execute("INSERT INTO reminders(due, label) VALUES (?, ?)", (due, label))

    def pending(self):
        if not self.path.exists():
            return []
        with self.database() as db:
            rows = db.execute("SELECT id, due, label FROM reminders ORDER BY due, id").fetchall()
        return [Reminder(*row) for row in rows]

    def cancel(self, reminder_id):
        if not self.path.exists():
            return
        with self.database() as db:
            db.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))

    def take_due(self, now=None):
        if not self.path.exists():
            return []
        now = time.time() if now is None else now
        with self.database() as db:
            rows = db.execute("DELETE FROM reminders WHERE due <= ? RETURNING id, due, label", (now,)).fetchall()
        return sorted((Reminder(*row) for row in rows), key=lambda item: (item.due, item.id))


def format_due(timestamp, time_format):
    when = datetime.fromtimestamp(timestamp)
    clock = when.strftime("%I:%M %p").lstrip("0") if time_format == 12 else when.strftime("%H:%M")
    return f"{clock} on {when:%b %d}"


def duration_seconds(text):
    match = DURATION.fullmatch(text)
    if match is None:
        return 0
    value, unit = match.groups()
    amount = int(value) if value.isdigit() else NUMBERS.get(value, 0)
    seconds = amount * {"s": 1, "m": 60, "h": 3600}[unit[0]]
    return seconds if seconds <= 366 * 86400 else 0


def next_clock_time(text, now):
    match = CLOCK.fullmatch(text)
    if match is None:
        return None
    raw_hour, raw_minute, period = match.groups()
    hour, minute = int(raw_hour), int(raw_minute or 0)
    if period:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if period.startswith("p") else 0)
    elif hour > 23:
        return None
    if minute > 59:
        return None

    due = datetime.fromtimestamp(now).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if due.timestamp() <= now:
        due += timedelta(days=1)
    return due.timestamp()


SET_TIMER = re.compile(r"set (?:a )?timer(?: for (.+))?", re.IGNORECASE)
REMIND = re.compile(r"remind me(?: (.+))?", re.IGNORECASE)
WHEN = re.compile(r"(at|in) (.+?)(?: to (.{1,240}))?", re.IGNORECASE)
INVERTED_WHEN = re.compile(r"to (.{1,240}?)\s+(at|in) (.+)", re.IGNORECASE)
PERIOD = r"[ap]\.?\s*m\.?"
NEW_QUESTION = re.compile(r"(?:what|when|where|how|why|who|can|could|is|are|tell|explain|resume|let['’]s continue)\b")
PERIOD_QUESTION = "Did you mean AM or PM? You can also say morning or evening."


@dataclass
class ReminderDraft:
    kind: str
    when: str = ""
    label: str = ""
    missing: str = ""
    expires: float = 0.0


class ReminderTurn:
    def __init__(self):
        self.draft = None

    @property
    def transcription_prompt(self):
        if self.draft is not None and self.draft.missing == "period" and self.draft.expires > time.time():
            return "AM. PM. In the morning. In the evening."
        return ""

    def reply(self, command, reminders, time_format, *, now=None):
        now = time.time() if now is None else now
        draft, self.draft = self.draft, None
        lower = command.casefold().rstrip(".!? ")
        if lower in {
            "list reminders", "show reminders", "list timers", "show timers",
            "what are my reminders", "what reminders do i have", "check reminders",
            "check timers", "my reminders", "view reminders",
        }:
            pending = reminders.pending()
            if not pending:
                return "You have no pending timers or reminders."
            lines = [f"{item.label} at {format_due(item.due, time_format)}" for item in pending[:8]]
            if len(pending) > len(lines):
                lines.append(f"See Reminders for all {len(pending)} pending items.")
            return "\n".join(lines)
        if timer := SET_TIMER.fullmatch(command):
            draft = ReminderDraft("timer", when=timer[1] or "")
        elif reminder := REMIND.fullmatch(command):
            draft = ReminderDraft("reminder")
            if request := WHEN.fullmatch(reminder[1] or ""):
                draft.when = f"{request[1]} {request[2]}"
                draft.label = request[3] or ""
            elif reminder[1] and (inv := INVERTED_WHEN.fullmatch(reminder[1])):
                draft.label = inv[1].strip()
                draft.when = f"{inv[2]} {inv[3]}".strip()
            elif reminder[1] and reminder[1].casefold().startswith("to "):
                draft.label = reminder[1][3:][:240]
            elif reminder[1] and (near := re.fullmatch(r"of (\w{1,10})", reminder[1], re.IGNORECASE)):
                number = near[1].casefold()
                if number in NUMBERS or re.fullmatch(r"[0-9]{1,2}", number):
                    draft.when = f"at {number}"
        elif draft is None or draft.expires <= now:
            return None
        elif lower in {"cancel that", "never mind", "nevermind"}:
            return "Okay, I didn't set it."
        elif draft.missing == "period" and (period := re.fullmatch(f"({PERIOD})(?: please)?", lower)):
            draft.when = f"{draft.when} {period[1]}"
        elif draft.missing == "period" and (period := re.fullmatch(r"(?:in the )?(morning|evening)", lower)):
            draft.when += " am" if period[1] == "morning" else " pm"
        elif draft.missing == "period" and len(lower.split()) == 1:
            return self.ask(draft, "period", PERIOD_QUESTION, now)
        elif draft.missing == "label" and not NEW_QUESTION.match(lower) and len(command) <= 240:
            draft.label = command[3:] if lower.startswith("to ") else command
        elif draft.missing == "time" and WHEN.fullmatch(command) and draft.kind == "reminder":
            match = WHEN.fullmatch(command)
            draft.when = f"{match[1]} {match[2]}"
            draft.label = match[3] or draft.label
        elif draft.kind == "timer" and duration_seconds(lower):
            draft.when = command
        else:
            return None

        if draft.kind == "timer":
            seconds = duration_seconds(draft.when.casefold())
            if seconds:
                due = now + seconds
                reminders.add(due, "Timer finished")
                return f"Timer set for {draft.when}. It'll go off at {format_due(due, time_format)}."
            return self.ask(draft, "time", "How long? Say a duration like 10 minutes.", now)

        when = draft.when.casefold()
        if when.startswith("at "):
            clock = when[3:]
            hour, sep, suffix = clock.partition(" ")
            if hour in NUMBERS:
                clock = str(NUMBERS[hour]) + sep + suffix
            if re.fullmatch(r"[0-9]{1,2}", clock) and 1 <= int(clock) <= 12:
                return self.ask(draft, "period", PERIOD_QUESTION, now)
            due = next_clock_time(clock, now)
        elif when.startswith("in "):
            seconds = duration_seconds(when[3:])
            due = now + seconds if seconds else None
        else:
            due = None
        if due is None:
            return self.ask(draft, "time", "Give me a time like 6 PM or a duration like 20 minutes.", now)
        if not draft.label:
            return self.ask(draft, "label", "What should I remind you about?", now)
        reminders.add(due, draft.label)
        return f"I'll remind you to {draft.label} at {format_due(due, time_format)}."

    def ask(self, draft, missing, question, now):
        draft.missing = missing
        draft.expires = now + 60
        self.draft = draft
        return question


def reminder_reply(command, reminders, time_format, *, now=None):
    return ReminderTurn().reply(command, reminders, time_format, now=now)


def send_notification(label):
    executable = shutil.which("notify-send")
    if executable:
        env = {key: value for key, value in os.environ.items() if key != "OPENAI_API_KEY"}
        with suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                [executable, "--app-name=Theia", "--", "Theia reminder", escape(label)],
                timeout=3, check=True, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
