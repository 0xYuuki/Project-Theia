import json
import os
from contextlib import suppress
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock

from theia.errors import AssistantError


DEFAULT_TIME_FORMAT = 24
DEFAULTS = {"time_format": DEFAULT_TIME_FORMAT, "answer_length": "default", "speaking_speed": "normal"}
OPTIONS = {"time_format": (12, 24), "answer_length": ("default", "short"),
           "speaking_speed": ("normal", "slower", "faster")}


class Preferences:
    def __init__(self, path=None):
        if path is None:
            config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
            if not config.is_absolute():
                config = Path.home() / ".config"
            path = config / "theia" / "preferences.json"
        self.path = Path(path)
        self.lock = RLock()

    def read(self):
        try:
            values = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return values if isinstance(values, dict) else {}

    def get(self, name):
        default = DEFAULTS[name]
        value = self.read().get(name)
        return value if type(value) is type(default) and value in OPTIONS[name] else default

    @property
    def time_format(self):
        return self.get("time_format")

    def set_time_format(self, value):
        self.set("time_format", value)

    def set(self, name, value):
        if type(value) is not type(DEFAULTS[name]) or value not in OPTIONS[name]:
            raise ValueError(f"Invalid preference {name}")
        with self.lock:
            values = self.read()
            values[name] = value
            self.write(values, name.replace("_", " "))

    def write(self, values, label):
        temporary = None
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent, delete=False,
            ) as file:
                temporary = Path(file.name)
                json.dump(values, file)
                file.write("\n")
            temporary.replace(self.path)
        except OSError as error:
            raise AssistantError(
                f"I couldn't save the {label}. Check that Theia can write to your settings folder."
            ) from error
        finally:
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)
