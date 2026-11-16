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
            with closing(sqlite3.connect(uri, timeout=0.3, uri=True)) as connection, connection:
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
    elif raw_minute is None or hour > 23:
        return None
    if minute > 59:
        return None

    due = datetime.fromtimestamp(now).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if due.timestamp() <= now:
        due += timedelta(days=1)
    return due.timestamp()


def reminder_reply(command, reminders, time_format, *, now=None):
    now = time.time() if now is None else now
    if command.casefold() in {"list reminders", "show reminders", "list timers"}:
        pending = reminders.pending()
        if not pending:
            return "You have no pending timers or reminders."
        lines = [f"{item.label} at {format_due(item.due, time_format)}" for item in pending[:8]]
        if len(pending) > len(lines):
            lines.append(f"See Reminders for all {len(pending)} pending items.")
        return "\n".join(lines)
    if match := re.fullmatch(r"set (?:a )?timer for (.+)", command, re.IGNORECASE):
        if seconds := duration_seconds(match[1].casefold()):
            due = now + seconds
            reminders.add(due, "Timer finished")
            return f"Timer set for {match[1]}. It'll go off at {format_due(due, time_format)}."
        return "Give me a duration like “Set a timer for 10 minutes.”"
    if match := re.fullmatch(r"remind me (in|at) (.+?) to (.{1,240})", command, re.IGNORECASE):
        mode, when, label = match.groups()
        if mode.casefold() == "at":
            due = next_clock_time(when.casefold(), now)
        else:
            seconds = duration_seconds(when.casefold())
            due = now + seconds if seconds else None
        if due is not None:
            reminders.add(due, label)
            return f"I'll remind you to {label} at {format_due(due, time_format)}."
        return "Give me a time like 6 PM or a duration like 20 minutes."
    if command.casefold().startswith("remind me "):
        return "Try “Remind me at 6 PM to check the oven.”"
    return None


def send_notification(label):
    executable = shutil.which("notify-send")
    if executable:
        env = {key: value for key, value in os.environ.items() if key != "OPENAI_API_KEY"}
        with suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                [executable, "--app-name=Theia", "--", "Theia reminder", escape(label)],
                timeout=3, check=True, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
