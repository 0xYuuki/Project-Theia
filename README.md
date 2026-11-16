# Theia for Fedora

Theia is a Python desktop assistant with typed commands, offline voice input, optional local AI chat, and a local Piper voice.

## Start the assistant

From the project folder on Fedora, install the system packages and Python dependencies.

```bash
sudo dnf install python3 python3-pip portaudio pipewire-utils
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python main.py
```

Try `help`, `check CPU`, `check RAM`, `check disk`, `what time is it`, or `open firefox`. The launcher also accepts `files`, `terminal`, and `calculator` when a supported app is installed.

## Use offline voice commands

If the local Whisper model is installed, start Theia and click **Record (5 s)**. Say “Theia, check memory” or “what time is it.” Recording finishes after a short silence, or when you click **Stop recording**. Closing the window cancels a recording in progress. Theia suggests a retry if it can't match a local command.

Watch **Hearing you** in the Voice panel for live text as you speak. The text updates in short bursts and may change as Whisper hears more. **Last heard** shows the final transcript that Theia used. Hands-free previews appear for recognized wake phrases and their follow-up commands. OpenAI recording mode shows the final transcript after recording.

Click **Edit and resend** if Theia mishears you. Fix the transcript in the message box and click **Send**. If Theia suggests an app name, repeat the exact command she gives you to open it.

Say or type **“Use 12-hour time”** to get answers like “4:35 PM” when you ask for the time. **“Use 24-hour time”** switches back. Ask **“What time format are you using?”** to check the saved preference. Theia remembers it after restart in `~/.config/theia/preferences.json`, or in `$XDG_CONFIG_HOME/theia/preferences.json` if that variable is set.

### Listen hands-free

Turn on **Hands-free listening** in the **Voice** panel. Say “Hey Theia, check memory,” or say “Hello Theia” or “Hi Theia” on its own, then wait for “I'm listening.” When you see **Listening for follow-up**, you have 12 seconds to start speaking. You can ask another question after each reply without repeating the wake phrase. Say “That's all,” “Goodbye,” or “End conversation” to go back to waiting for a wake phrase, or let the 12 seconds pass in silence. The **Record** button is for one request at a time.

Hands-free listening starts off each time you open the app. While enabled, Theia uses the default microphone and checks speech for a wake phrase on your device. Audio stays in memory. Theia pauses the microphone while processing speech and playing replies. Turn off **Hands-free listening** or close the app to stop. You can turn **Speak replies** off if you only want on-screen responses.

Use **Stop speaking** or **Ctrl+Shift+S** while Theia is talking. The answer stays in chat, and hands-free listening resumes after playback stops.

Theia waits about one second through a pause before ending a take. The Voice panel shows when she is transcribing, thinking, or checking a file. If a file lookup takes a moment, she says “Give me a second to check” when **Speak replies** is enabled.

### Interrupt with “Wait, Theia”

Spoken interruption uses offline Whisper and PipeWire echo cancellation. It is off at startup and only listens for the stop phrase during hands-free reply playback. Set up the named audio devices in your Fedora login session once. Select your usual microphone and speakers first so the virtual devices use that pair. [PipeWire echo cancellation](https://docs.pipewire.org/page_pulse_module_echo_cancel.html)

```bash
sudo dnf install pipewire-utils pulseaudio-utils
theia_mic=$(pactl get-default-source)
theia_speakers=$(pactl get-default-sink)
pactl load-module module-echo-cancel \
  source_name=theia_echo_source sink_name=theia_echo_sink \
  source_master="$theia_mic" sink_master="$theia_speakers"
pactl set-default-source "$theia_mic"
pactl set-default-sink "$theia_speakers"
```

Keep the module number printed by `pactl load-module`. To remove the virtual devices, run `pactl unload-module` followed by that number. The devices also disappear when the PipeWire audio server restarts. If you switch physical devices, reload the module with the new pair.

In Theia, open **Settings → Run interruption sound check**. Stay quiet during the first sample. When the Voice panel says **Sound check · say Wait, Theia**, say “Wait, Theia” over the second sample. After the check passes, turn on **Hands-free listening**. Say “Wait, Theia” to stop a reply, then ask your next question when you see **Listening for follow-up**. Repeat the check after restarting Theia or changing the audio devices. You can turn it off in **Settings**.

If you're updating an existing setup, run `.venv/bin/python -m pip install -r requirements.txt` once to install the speech detector, then restart Theia.

### Set up Whisper on a fresh Fedora install

Run these commands once from the project directory. They build [whisper.cpp v1.9.5](https://github.com/ggml-org/whisper.cpp/releases/tag/v1.9.5) and download the [English models](https://huggingface.co/ggerganov/whisper.cpp) for final recognition and live previews.

```bash
sudo dnf install gcc gcc-c++ cmake ninja-build curl
mkdir -p .whisper-cpp/source .whisper-cpp/models
curl -fL https://codeload.github.com/ggml-org/whisper.cpp/tar.gz/refs/tags/v1.9.5 \
  -o .whisper-cpp/source.tar.gz
tar -xzf .whisper-cpp/source.tar.gz -C .whisper-cpp/source --strip-components=1
cmake -S .whisper-cpp/source -B .whisper-cpp/build -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_SERVER=OFF
cmake --build .whisper-cpp/build --target whisper-cli --parallel 4
bash .whisper-cpp/source/models/download-ggml-model.sh small.en-q5_1 "$PWD/.whisper-cpp/models"
bash .whisper-cpp/source/models/download-ggml-model.sh base.en "$PWD/.whisper-cpp/models"
```

Restart Theia after setup. To upgrade a base-only installation, run the `small.en-q5_1` download command and restart. Theia uses that model for final transcripts and the base model for faster previews when both are installed. If you already have a compatible `whisper-cli` and model, set `WHISPER_CLI` and `WHISPER_MODEL_PATH` in `.env`.

### Accelerate final transcription with Vulkan

With a working Vulkan GPU driver, build the optional GPU executable beside the CPU build.

```bash
sudo dnf install glslc vulkan-loader-devel spirv-headers-devel
cmake -S .whisper-cpp/source -B .whisper-cpp/build-vulkan -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_SERVER=OFF \
  -DGGML_VULKAN=ON
cmake --build .whisper-cpp/build-vulkan --target whisper-cli --parallel 2
```

Restart Theia to pick up the new executable. Final transcription prefers Vulkan and switches to the CPU if the Vulkan process fails. Live previews use the CPU. On a machine with multiple GPUs, use `vulkaninfo --summary` and set `WHISPER_VULKAN_DEVICE` in `.env` to the Vulkan index of the GPU you want. A custom `WHISPER_CLI` selects the CPU transcription path.

## Hear Theia's voice

In the **Voice** panel, click **Preview voice** to hear the offline English voice. Turn on **Speak replies** to hear command and chat responses.

Theia uses a warm chat tone. Clear statements like “I'm excited” or “I feel frustrated” also change her wording and slightly adjust the speaking pace.

Open **Settings** to view, change, or reset the saved time format, answer length, and speaking speed. You can also say “Remember that I prefer short answers,” “Speak a little slower,” or “Use normal speaking speed.”

On a fresh install, download Piper's [Kristin voice model](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/COMMERCIAL_USE.md) once from the project directory.

```bash
mkdir -p .piper-voices
.venv/bin/python -m piper.download_voices --data-dir .piper-voices en_US-kristin-medium
```

## Enable local AI chat

Install [Ollama for Linux](https://docs.ollama.com/linux). Use [local-only mode](https://docs.ollama.com/faq#how-do-i-disable-ollamas-cloud-features) and keep Ollama bound to `127.0.0.1:11434`. If you start it manually, run `OLLAMA_NO_CLOUD=1 ollama serve` in a separate terminal. If it runs as a service, set `OLLAMA_NO_CLOUD=1` for that service and restart it.

Download the [Qwen3 1.7B model](https://ollama.com/library/qwen3:1.7b) once.

```bash
ollama pull qwen3:1.7b
```

Run `cp -n .env.example .env` and `chmod 600 .env`, then set these lines in `.env` and restart Theia.

```dotenv
THEIA_CHAT_BACKEND=ollama
THEIA_OLLAMA_MODEL=qwen3:1.7b
THEIA_VOICE_BACKEND=whisper
```

With **Local AI** displayed, type a question or speak one using **Record** or a wake phrase. Theia tries desktop commands first. The local model handles other questions and keeps up to six recent AI exchanges in memory for follow-ups. With **Speak replies** on, Theia gives you a short spoken answer and leaves the full response in the chat. For a smaller model, pull `qwen2.5:1.5b-instruct`, update `THEIA_OLLAMA_MODEL`, and restart Theia.

### Ask questions about a file

Click **Attach file** or drop one PDF, TXT, or Markdown file into the window. Ask a question about it, then use the page or line references in the answer to check the source. **Remove file** returns to regular chat. Theia uses local Ollama for file questions and local Whisper for voice input while a file is attached.

On Fedora, install `poppler-utils` for PDF support with `sudo dnf install poppler-utils`. PDFs need a searchable text layer. Files can be up to 10 MB, 100 PDF pages, and 500,000 extracted characters. Save TXT and Markdown files as UTF-8.

### Explain copied text or resume a conversation

Copy plain text from another app, then click **Explain copied text** or say “Explain the text I copied.” Theia sends up to 4,000 characters to local Ollama for that answer. The clipboard is only opened on request, and the text stays out of later model requests and recaps.

Open **Saved conversations → Save recap** to draft a recap of the recent local chat. Edit the title, decisions and open questions before saving. Open the same list to review, rename, delete, or resume a recap. You can also say “Let's continue” followed by its title. Recaps are stored locally and used only with Ollama.

## Timers and reminders

Say or type “Set a timer for 10 minutes,” “Remind me in 20 minutes to check the oven,” or “Remind me at 6 PM to check the oven.” A clock time means the next occurrence in your local time. Open **Reminders** to see or cancel pending items.

You can start with “Remind me at six.” When Theia asks, say “PM,” then “Check the oven.” Say “Cancel that” to discard the unfinished reminder. A hands-free draft ends when the follow-up window expires. If you use typed chat or **Record**, respond to each follow-up within 60 seconds.

Theia reads timers and reminders out loud when they are due, even with **Speak replies** off. The alert also appears in chat. Install `libnotify` with `sudo dnf install libnotify` to enable Fedora desktop notifications. Pending reminders persist across restarts. Keep Theia running to receive them on time, or hear overdue reminders when you reopen it.

## Enable optional OpenAI chat

1. Run `cp -n .env.example .env` and `chmod 600 .env`.
2. Set `THEIA_CHAT_BACKEND=openai`, add your key after `OPENAI_API_KEY=`, and restart Theia. Keep that file private.
3. Type a question. When you use OpenAI chat, Theia sends the prompt and up to six previous AI turns to OpenAI. Requests may incur charges.

You can set `THEIA_VOICE_BACKEND=openai` in `.env` to use cloud transcription, which sends the recording to OpenAI. Offline Whisper is the default.

## Work on the code

- `main.py` starts the application.
- `theia/ui.py` contains the PySide6 window and background jobs.
- `theia/appearance.py` defines the theme and voice animation.
- `theia/commands.py` handles local system commands and the app allowlist.
- `theia/assistant.py` handles chat, session context, and voice backend selection.
- `theia/ollama.py` handles local AI requests.
- `theia/whisper.py` sends audio to the local Whisper process.
- `theia/wake.py` detects speech and handles wake phrases on the device.
- `theia/audio.py` records audio and plays speech generated by Piper.

Run the offline tests with `.venv/bin/python -m unittest discover -s tests -v`. They cover local commands, subprocess argument handling, keyless mode, and the typed and recorded UI flows. The OpenAI client and hardware are mocked where needed.
