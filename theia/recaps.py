import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass

from theia.errors import AssistantError


MAX_RECAP = 3000


@dataclass(frozen=True)
class Recap:
    title: str
    text: str


def title_key(title):
    return " ".join(title.casefold().split()).rstrip(".!? ")


class Recaps:
    def __init__(self, path):
        self.path = path

    @contextmanager
    def database(self):
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path, timeout=0.3)) as db, db:
                db.execute("CREATE TABLE IF NOT EXISTS recaps (key TEXT PRIMARY KEY, title TEXT, text TEXT)")
                yield db
        except sqlite3.IntegrityError as error:
            raise AssistantError("A recap with that title already exists.") from error
        except (OSError, sqlite3.Error) as error:
            raise AssistantError("Theia couldn't access the saved conversations.") from error

    def list(self):
        if not self.path.exists():
            return []
        with self.database() as db:
            return [Recap(*row) for row in db.execute("SELECT title, text FROM recaps ORDER BY key")]

    def get(self, title):
        if not self.path.exists():
            return None
        with self.database() as db:
            record = db.execute("SELECT title, text FROM recaps WHERE key = ?", (title_key(title),)).fetchone()
        return Recap(*record) if record else None

    def save(self, title, text, *, previous_title=None):
        title = " ".join(title.split())
        text = text.strip()
        if not title_key(title) or len(title) > 100 or not text or len(text) > MAX_RECAP:
            raise AssistantError("Use a title of 1–100 characters and a recap of up to 3,000 characters.")
        with self.database() as db:
            if previous_title is None:
                db.execute("INSERT INTO recaps VALUES (?, ?, ?)", (title_key(title), title, text))
            else:
                change = db.execute(
                    "UPDATE recaps SET key = ?, title = ?, text = ? WHERE key = ?",
                    (title_key(title), title, text, title_key(previous_title)),
                )
                if not change.rowcount:
                    raise AssistantError("That recap was deleted. Save it as a new conversation if needed.")

    def delete(self, title):
        with self.database() as db:
            db.execute("DELETE FROM recaps WHERE key = ?", (title_key(title),))
