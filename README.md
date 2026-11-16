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

### Listen hands-free

Turn on **Hands-free listening** in the **Voice** panel. Say “Hey Theia, check memory,” or say “Hello Theia” or “Hi Theia” on its own, wait for “I'm listening,” then say your command. Theia waits about seven seconds for you to start speaking after the acknowledgment.

Hands-free listening starts off each time you open the app. While enabled, Theia uses the default microphone and checks speech for a wake phrase on your device. Audio stays in memory. Theia pauses the microphone while processing speech and speaking back, then listens for the next wake phrase. Turn off **Hands-free listening** or close the app to stop. You can turn **Speak replies** off if you only want on-screen responses.

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

With **Local AI** displayed, type a question or speak one using **Record** or a wake phrase. Theia tries desktop commands first. The local model handles other questions and keeps up to six recent AI exchanges in memory for follow-ups. Turn on **Speak replies** to hear the response. For a smaller model, pull `qwen2.5:1.5b-instruct`, update `THEIA_OLLAMA_MODEL`, and restart Theia.

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
