import json
import os
import re
from http.client import HTTPConnection, HTTPException
from urllib.parse import urlparse

from theia.errors import AssistantError


DEFAULT_MODEL = "qwen3:1.7b"


def parse_ollama_host(raw):
    raw = (raw or "").strip()
    if not raw:
        return "127.0.0.1", 11434
    if "://" not in raw:
        raw = f"http://{raw}"
    parsed = urlparse(raw)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 11434
    return host, port


def sanitize_reply(text):
    if not isinstance(text, str):
        return ""
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", text).strip()
    return cleaned if cleaned else text.strip()


class OllamaChat:
    def __init__(self, model=DEFAULT_MODEL, host=None):
        self.model = model
        raw_host = host or os.environ.get("THEIA_OLLAMA_HOST") or os.environ.get("OLLAMA_HOST")
        self.host, self.port = parse_ollama_host(raw_host)

    def reply(self, messages, *, schema=None):
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "think": False,
            "keep_alive": "5m",
            "options": {"num_ctx": 4096, "num_predict": 320 if schema is not None else 192},
        }
        if schema is not None:
            payload["format"] = schema
        connection = HTTPConnection(self.host, self.port, timeout=120)
        try:
            connection.request(
                "POST", "/api/chat", body=json.dumps(payload), headers={"Content-Type": "application/json"},
            )
            with connection.getresponse() as response:
                if response.status == 404:
                    raise AssistantError(f"The Ollama model is missing. Run ollama pull {self.model} and try again.")
                if response.status != 200:
                    raise AssistantError(f"Ollama couldn't generate a reply (HTTP {response.status}).")
                try:
                    result = json.load(response)["message"]["content"]
                except (ValueError, KeyError, TypeError) as error:
                    raise AssistantError("Ollama returned an unreadable reply.") from error
        except TimeoutError as error:
            raise AssistantError("The local model took too long to respond. Try again in a moment.") from error
        except (OSError, HTTPException) as error:
            raise AssistantError(
                "Couldn't reach Ollama. Start its service or run OLLAMA_NO_CLOUD=1 ollama serve, then try again."
            ) from error
        finally:
            connection.close()

        result = sanitize_reply(result)
        if not isinstance(result, str) or not result.strip():
            raise AssistantError("The local model returned an empty reply.")
        return result.strip()
