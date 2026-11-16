import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import psutil


APPS = {
    "firefox": ("firefox",),
    "files": ("xdg-open", str(Path.home())),
    "terminal": ("ptyxis", "gnome-terminal", "konsole", "xfce4-terminal"),
    "calculator": ("gnome-calculator", "kcalc", "mate-calc"),
}


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


def local_reply(message, launcher=launch):
    command = " ".join(message.casefold().split())
    command = re.sub(r"^(?:(?:hello|hey|hi)[, ]+)?(?:theia|thea|jarvis)(?: *[,!:] *| +)", "", command)
    command = command.rstrip(".!? ")
    if command in {"help", "commands"}:
        return "Try check CPU, check RAM, check disk, what time is it, or open firefox, files, terminal, or calculator."
    if command.startswith("open "):
        return launcher(command[5:].strip())
    if command in {"check cpu", "cpu usage"}:
        return f"CPU usage is {psutil.cpu_percent(interval=0.2):g}%."
    if command in {
        "check ram", "check ram usage", "ram usage",
        "check memory", "check memory usage", "memory usage",
        "check from usage",
    }:
        memory = psutil.virtual_memory()
        return f"RAM usage is {memory.percent:g}% ({memory.used / 1024**3:.1f} GiB used)."
    if command in {"check disk", "disk usage"}:
        disk = psutil.disk_usage("/")
        return f"Root disk usage is {disk.percent:g}% ({disk.free / 1024**3:.1f} GiB free)."
    if command in {"what time is it", "time", "tell me the time"}:
        return datetime.now().strftime("It's %H:%M local time.")
    return None
