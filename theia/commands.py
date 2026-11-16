import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import psutil

from theia.preferences import DEFAULT_TIME_FORMAT


APPS = {
    "firefox": ("firefox",),
    "files": ("xdg-open", str(Path.home())),
    "terminal": ("ptyxis", "gnome-terminal", "konsole", "xfce4-terminal"),
    "calculator": ("gnome-calculator", "kcalc", "mate-calc"),
}

TIME_FORMAT_REQUEST = re.compile(
    r"(?:please )?(?:(?:can|could|would) you )?(?:always )?"
    r"(?:adjust|change|set|switch|use|say|remember to use)"
    r"(?: it| (?:(?:my|the) )?time(?: format)?)?(?: in| to)? (?:a )?"
    r"(12|24|twelve|twenty[- ]four)[ \-–‑]*(?:hours?|hrs?|h)"
    r"(?: format| time(?: format)?| clock)?"
    r"(?: (?:every ?time|whenever) i ask(?: you)?(?: what time it is| for the time)?| from now on| always)?"
)


def system_usage():
    return psutil.cpu_percent(), psutil.virtual_memory().percent, psutil.disk_usage("/").percent


def launch(name):
    if name not in APPS:
        return "Available apps: " + ", ".join(APPS) + "."
    # Files uses a fixed argument. Other entries are executable fallbacks.
    candidates = APPS[name][:1] if name == "files" else APPS[name]
    for candidate in candidates:
        executable = shutil.which(candidate)
        if executable:
            args = APPS[name][1:] if name == "files" else ()
            env = {key: value for key, value in os.environ.items() if key != "OPENAI_API_KEY"}
            try:
                subprocess.Popen(
                    [executable, *args], env=env, start_new_session=True,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except OSError:
                return f"Could not start {name}."
            return f"Launch requested for {name}."
    return f"{name.capitalize()} is unavailable. Install an approved desktop app and try again."


def normalize_command(message):
    command = " ".join(message.casefold().split())
    command = re.sub(r"^(?:(?:hello|hey|hi)[, ]+)?(?:theia|thea|jarvis)(?: *[,.!:] *| +)", "", command)
    return command.rstrip(".!? ")


def local_reply(message, launcher=launch, *, preferences=None):
    command = normalize_command(message)
    if preferences is not None:
        if command in {"what time format are you using", "what time format do you use"}:
            return f"I'm using {preferences.time_format}-hour time."
        if match := TIME_FORMAT_REQUEST.fullmatch(command):
            time_format = 12 if match[1] in ("12", "twelve") else 24
            preferences.set_time_format(time_format)
            return f"I'll use {time_format}-hour time from now on."
    if command in {"help", "commands"}:
        return (
            "Try check CPU, check RAM, check disk, what time is it, use 12-hour time, "
            "or open firefox, files, terminal, or calculator."
        )
    if command.startswith("open "):
        return launcher(command[5:].strip())
    command = re.sub(r"\bc[. ]+p[. ]+u\b\.?", "cpu", command)
    command = re.sub(r"\br[. ]+a[. ]+m\b\.?", "ram", command)
    command = re.sub(r"^(?:show(?: me)?|what(?:['’]s| is)) ", "check ", command)
    command = re.sub(r"^check (?:my|the|system) ", "check ", command)
    if command in {"check cpu", "check cpu usage", "cpu usage"}:
        return f"CPU usage is {psutil.cpu_percent(interval=0.2):g}%."
    if command in {
        "check ram", "check ram usage", "ram usage",
        "check memory", "check memory usage", "memory usage",
        "check from usage", "check our am usage", "how much ram am i using", "how much memory am i using",
    }:
        memory = psutil.virtual_memory()
        return f"RAM usage is {memory.percent:g}% ({memory.used / 1024**3:.1f} GiB used)."
    if command in {"check disk", "check disk usage", "disk usage"}:
        disk = psutil.disk_usage("/")
        return f"Root disk usage is {disk.percent:g}% ({disk.free / 1024**3:.1f} GiB free)."
    if command in {"what time is it", "check time", "time", "tell me the time"}:
        now = datetime.now()
        time_format = preferences.time_format if preferences is not None else DEFAULT_TIME_FORMAT
        if time_format == 12:
            period = "AM" if now.hour < 12 else "PM"
            return f"It's {now.hour % 12 or 12}:{now.minute:02d} {period} local time."
        return now.strftime("It's %H:%M local time.")
    return None
