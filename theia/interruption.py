import json
import os
import re
import select
import shutil
import subprocess
from collections import deque
from contextlib import closing
from dataclasses import dataclass
from threading import Event, Thread
from time import monotonic

from theia.audio import FRAME_MS, encode_wav, play_audio, render_speech, target_args
from theia.errors import AssistantError, NoSpeechDetected, OperationCancelled, TranscriptionTimeout


RATE = 16000
FRAME_BYTES = RATE * FRAME_MS // 1000 * 2
STOP_VOCABULARY = "Theia. Wait. Hello. Good morning."
SOURCE = "theia_echo_source"
SINK = "theia_echo_sink"
SETUP_ERROR = "Theia couldn't find both PipeWire echo-cancellation devices. See the Fedora setup in the README."
ROUTE_ERROR = "The echo-cancelled microphone disconnected or stopped delivering audio."


@dataclass(frozen=True)
class EchoRoute:
    source: str
    sink: str


def pipewire_route():
    executable = shutil.which("pw-dump")
    if not executable or not shutil.which("pw-record"):
        raise AssistantError("Install pipewire-utils for spoken interruption.")
    try:
        result = subprocess.run(
            [executable], capture_output=True, check=True, timeout=2,
            env={key: value for key, value in os.environ.items() if key != "OPENAI_API_KEY"},
        )
        nodes = {}
        for item in json.loads(result.stdout):
            if item.get("type") == "PipeWire:Interface:Node":
                props = item.get("info", {}).get("props", {})
                serial = props.get("object.serial")
                if serial is not None:
                    nodes[(props.get("node.name"), props.get("media.class"))] = str(serial)
        return EchoRoute(nodes[SOURCE, "Audio/Source"], nodes[SINK, "Audio/Sink"])
    except (OSError, subprocess.SubprocessError, ValueError, KeyError) as error:
        raise AssistantError(SETUP_ERROR) from error


def is_stop_phrase(text):
    return re.fullmatch(r"\s*wait(?:\s|[,!.?])+the(?:ia|a)[,.!?\s]*", text, re.IGNORECASE) is not None


def echo_frames(target, stop):
    executable = shutil.which("pw-record")
    if not executable:
        raise AssistantError("Install pipewire-utils for spoken interruption.")
    env = {key: value for key, value in os.environ.items() if key != "OPENAI_API_KEY"}
    try:
        with subprocess.Popen([
            executable, *target_args(target), "--raw", "--rate", str(RATE), "--channels", "1",
            "--format", "s16", "-",
        ], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env) as process:
            pending = bytearray()
            last_audio = monotonic()
            try:
                while not stop.is_set():
                    if not select.select([process.stdout], [], [], 0.05)[0]:
                        if monotonic() - last_audio >= 2:
                            raise AssistantError(ROUTE_ERROR)
                        continue
                    data = os.read(process.stdout.fileno(), 8192)
                    if not data:
                        if stop.is_set():
                            break
                        raise AssistantError(ROUTE_ERROR)
                    last_audio = monotonic()
                    pending.extend(data)
                    while len(pending) >= FRAME_BYTES and not stop.is_set():
                        frame = bytes(pending[:FRAME_BYTES])
                        del pending[:FRAME_BYTES]
                        yield frame
            finally:
                if process.poll() is None:
                    process.kill()
    except OSError as error:
        raise AssistantError(ROUTE_ERROR) from error


def clips(frames, is_voiced, ready):
    lead = deque(maxlen=8)
    clip = []
    silence = 0
    for frame in frames:
        ready.set()
        voiced = is_voiced(frame, RATE)
        if not clip:
            lead.append((frame, voiced))
            if voiced and sum(active for _, active in lead) >= 5:
                clip = [data for data, _ in lead]
            continue
        clip.append(frame)
        silence = 0 if voiced else silence + 1
        if silence >= 16 or len(clip) >= 90:
            yield encode_wav(b"".join(clip), RATE)
            clip.clear()
            lead.clear()
            silence = 0


class EchoInterruption:
    def __init__(self):
        self.route = None

    def disable(self):
        self.route = None

    def check(self, whisper, outer, stop_playing, status):
        self.disable()
        if whisper is None:
            raise AssistantError("Spoken interruption requires offline Whisper.")
        if reason := whisper.unavailable_reason:
            raise AssistantError(reason)
        route = pipewire_route()
        status("Sound check · stay quiet")
        quiet = (
            "This is the first part of the sound check. Please stay quiet as I speak. "
            "Wait, Theia. I will keep talking for a few more seconds."
        )
        if self.play(quiet, whisper, route, outer, stop_playing, quiet=True):
            raise AssistantError("The sound check picked up speech during playback. Check your echo-cancellation setup and try again.")
        if outer.is_set() or stop_playing.is_set():
            raise OperationCancelled
        status("Sound check · say Wait, Theia")
        speaking = (
            "Please say the interruption phrase now. I will keep speaking so the microphone can check "
            "whether it picks up your voice. You can say the phrase while I am still speaking."
        )
        if not self.play(speaking, whisper, route, outer, stop_playing):
            if outer.is_set() or stop_playing.is_set():
                raise OperationCancelled
            raise AssistantError("I didn't hear the stop phrase during the sound check. Try again.")
        if outer.is_set():
            raise OperationCancelled
        self.route = route

    def play(self, text, whisper, route, outer, stop_playing, *, length_scale=None, quiet=False):
        if outer.is_set() or stop_playing.is_set():
            raise OperationCancelled
        if pipewire_route() != route:
            raise AssistantError("The PipeWire audio devices changed. Run the sound check again.")
        audio = render_speech(text, length_scale=length_scale)
        if not audio:
            return False
        if outer.is_set() or stop_playing.is_set():
            raise OperationCancelled
        from webrtcvad import Vad

        finished, ready, playback_started, matched = Event(), Event(), Event(), Event()
        errors = []

        def monitor():
            try:
                with closing(echo_frames(route.source, finished)) as frames:
                    for clip in clips(frames, Vad(2).is_speech, ready):
                        if not playback_started.is_set() or stop_playing.is_set():
                            continue
                        if not quiet:
                            try:
                                heard = whisper.transcribe(
                                    clip, prompt=STOP_VOCABULARY, timeout=3, preview=True,
                                    cancel=finished, command_terms=False,
                                )
                            except (NoSpeechDetected, TranscriptionTimeout):
                                continue
                            if not is_stop_phrase(heard):
                                continue
                        if not finished.is_set() and not outer.is_set() and not stop_playing.is_set():
                            matched.set()
                            stop_playing.set()
                        break
            except OperationCancelled:
                pass
            except (AssistantError, ValueError) as error:
                if not finished.is_set():
                    errors.append(str(error))
                    stop_playing.set()

        listener = Thread(target=monitor, daemon=True)
        listener.start()
        try:
            deadline = monotonic() + 2.2
            while not ready.wait(0.05) and not stop_playing.is_set() and not outer.is_set():
                if monotonic() >= deadline:
                    raise AssistantError(ROUTE_ERROR)
            if not stop_playing.is_set() and not outer.is_set():
                playback_started.set()
                try:
                    play_audio(audio, cancel=stop_playing, target=route.sink)
                except OperationCancelled:
                    pass
        finally:
            finished.set()
            listener.join()
        if errors:
            raise AssistantError(errors[0])
        return matched.is_set()
