import json
import os
from contextlib import suppress
from pathlib import Path
from tempfile import NamedTemporaryFile

from theia.errors import AssistantError


DEFAULT_TIME_FORMAT = 24


class Preferences:
    def __init__(self, path=None):
        if path is None:
            config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
            if not config.is_absolute():
                config = Path.home() / ".config"
            path = config / "theia" / "preferences.json"
        self.path = Path(path)

    @property
    def time_format(self):
        try:
            values = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return DEFAULT_TIME_FORMAT
        value = values.get("time_format") if isinstance(values, dict) else None
        return value if type(value) is int and value in (12, 24) else DEFAULT_TIME_FORMAT

    def set_time_format(self, value):
        temporary = None
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent, delete=False,
            ) as file:
                temporary = Path(file.name)
                json.dump({"time_format": value}, file)
                file.write("\n")
            temporary.replace(self.path)
        except OSError as error:
            raise AssistantError(
                "I couldn't save the time format. Check that Theia can write to your settings folder."
            ) from error
        finally:
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)
