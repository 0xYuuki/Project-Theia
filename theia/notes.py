import sqlite3
import time
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

from theia.errors import AssistantError


MAX_NOTE = 1000


@dataclass(frozen=True)
class Note:
    id: int
    created: float
    text: str


class Notes:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def database(self):
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path, timeout=5.0)) as db, db:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL, text TEXT)"
                )
                yield db
        except (OSError, sqlite3.Error) as error:
            raise AssistantError("Theia couldn't access local notes.") from error

    def add(self, text):
        clean_text = text.strip()
        if not clean_text or len(clean_text) > MAX_NOTE:
            raise AssistantError(f"Notes must be between 1 and {MAX_NOTE} characters.")
        with self.database() as db:
            db.execute("INSERT INTO notes (created, text) VALUES (?, ?)", (time.time(), clean_text))

    def list(self, limit=10):
        if not self.path.exists():
            return []
        with self.database() as db:
            rows = db.execute("SELECT id, created, text FROM notes ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [Note(*row) for row in reversed(rows)]

    def clear(self):
        if not self.path.exists():
            return 0
        with self.database() as db:
            count = db.execute("DELETE FROM notes").rowcount
            return count
