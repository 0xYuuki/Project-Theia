import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from difflib import get_close_matches
from pathlib import Path

import psutil

from theia.preferences import DEFAULT_TIME_FORMAT


APPS = {
    "firefox": ("firefox",),
    "files": ("xdg-open", str(Path.home())),
    "terminal": ("ptyxis", "gnome-terminal", "konsole", "xfce4-terminal"),
    "calculator": ("gnome-calculator", "kcalc", "mate-calc"),
    "editor": ("gnome-text-editor", "gedit", "kate", "kwrite"),
    "settings": ("gnome-control-center", "systemsettings"),
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


def pipewire_volume():
    executable = shutil.which("wpctl")
    if not executable:
        return None
    try:
        result = subprocess.run(
            [executable, "get-volume", "@DEFAULT_AUDIO_SINK@"],
            capture_output=True, text=True, check=True, timeout=2,
        )
        line = result.stdout.strip()
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "Volume:":
            vol = round(float(parts[1]) * 100)
            muted = "[MUTED]" in line
            return vol, muted
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return None


def set_pipewire_volume(arg):
    executable = shutil.which("wpctl")
    if not executable:
        return "Audio volume control is unavailable."
    try:
        subprocess.run(
            [executable, "set-volume", "@DEFAULT_AUDIO_SINK@", arg],
            check=True, timeout=2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        info = pipewire_volume()
        if info is not None:
            vol, muted = info
            status = " (muted)" if muted else ""
            return f"Volume is now {vol}%{status}."
        return "Volume adjusted."
    except (OSError, subprocess.SubprocessError):
        return "Couldn't adjust volume."


def set_pipewire_mute(mute=True):
    executable = shutil.which("wpctl")
    if not executable:
        return "Audio mute control is unavailable."
    try:
        subprocess.run(
            [executable, "set-mute", "@DEFAULT_AUDIO_SINK@", "1" if mute else "0"],
            check=True, timeout=2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return "Audio muted." if mute else "Audio unmuted."
    except (OSError, subprocess.SubprocessError):
        return "Couldn't change mute status."


def normalize_command(message, *, preserve_case=False):
    command = " ".join((message if preserve_case else message.casefold()).split())
    command = re.sub(r"^(?:(?:hello|hey|hi)[, ]+)?(?:theia|thea|jarvis)(?: *[,.!:] *| +)", "", command,
                     flags=re.IGNORECASE)
    command = re.sub(r"^(?:please\s+|(?:can|could|would)\s+you\s+(?:please\s+)?)", "", command, flags=re.IGNORECASE)
    return command.rstrip(".!? ")


def local_reply(message, launcher=launch, *, preferences=None, recorded=False):
    command = normalize_command(message)
    if preferences is not None:
        if command in {"what time format are you using", "what time format do you use"}:
            return f"I'm using {preferences.time_format}-hour time."
        if match := TIME_FORMAT_REQUEST.fullmatch(command):
            time_format = 12 if match[1] in ("12", "twelve") else 24
            preferences.set_time_format(time_format)
            return f"I'll use {time_format}-hour time from now on."
        if re.fullmatch(r"(?:please )?(?:remember that i prefer|use|give me) short(?:er)? (?:answers|replies)", command):
            preferences.set("answer_length", "short")
            return "I'll keep my answers short."
        if command in {"use default answer length", "forget my answer length preference"}:
            preferences.set("answer_length", "default")
            return "I'll use my usual answer length."
        if match := re.fullmatch(r"(?:please )?speak (?:a little )?(slower|faster)", command):
            speed = match[1]
            preferences.set("speaking_speed", speed)
            return f"I'll speak {speed}."
        if command in {"use normal speaking speed", "speak at your normal speed", "forget my speaking speed preference"}:
            preferences.set("speaking_speed", "normal")
            return "I'll speak at my usual speed."
    if command in {"help", "commands"}:
        return (
            "Try check CPU, check RAM, check disk, what time is it, use 12-hour time, "
            "set a timer for 10 minutes, or open firefox, files, terminal, or calculator."
        )
    if command.startswith("open "):
        app = command[5:].strip()
        if recorded and app not in APPS and re.fullmatch(r"[a-z]+(?: [a-z]+)?", app):
            candidates = get_close_matches(app.replace(" ", ""), APPS, cutoff=0.65)
            if len(candidates) == 1:
                return f"Did you mean {candidates[0]}? Say ‘open {candidates[0]}’ to confirm."
            if candidates:
                choices = ", ".join(f"‘open {name}’" for name in candidates)
                return f"Which app did you mean? Say {choices}."
        return launcher(app)
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
    if command in {
        "what day is it", "what day is today", "what day is it today", "day of the week",
        "what is the date", "what is today's date", "what is the date today", "check date",
        "check today's date", "today's date", "date",
    }:
        now = datetime.now()
        return now.strftime("Today is %A, %B %d, %Y.")
    if command in {
        "check battery", "battery", "battery status", "check battery level",
        "check battery percentage", "how much battery do i have", "battery level",
    }:
        battery = psutil.sensors_battery()
        if battery is None:
            return "No battery detected (running on AC power or battery status unavailable)."
        percent = round(battery.percent)
        status = "charging" if battery.power_plugged else "on battery power"
        return f"Battery is at {percent}% ({status})."
    if command in {
        "check uptime", "uptime", "system uptime",
        "how long has the system been up", "how long has my computer been on",
    }:
        boot = time.time() - psutil.boot_time()
        hours = int(boot // 3600)
        minutes = int((boot % 3600) // 60)
        if hours > 0:
            return f"System uptime is {hours} hours and {minutes} minutes."
        return f"System uptime is {minutes} minutes."
    if command in {"check volume", "volume", "what is the volume", "get volume", "sound volume"}:
        info = pipewire_volume()
        if info is not None:
            vol, muted = info
            return f"Volume is {vol}% (muted)." if muted else f"Volume is {vol}%."
        return "Audio volume information is unavailable."
    if command in {"mute", "mute audio", "mute sound"}:
        return set_pipewire_mute(True)
    if command in {"unmute", "unmute audio", "unmute sound"}:
        return set_pipewire_mute(False)
    if command in {"increase volume", "volume up", "turn up the volume", "turn up volume", "louder"}:
        return set_pipewire_volume("10%+")
    if command in {"decrease volume", "volume down", "turn down the volume", "turn down volume", "quieter", "lower volume"}:
        return set_pipewire_volume("10%-")
    if match := re.fullmatch(r"set (?:the )?volume (?:to )?(\d{1,3})\s*%?", command):
        vol = max(0, min(100, int(match[1])))
        return set_pipewire_volume(f"{vol}%")
    return None
