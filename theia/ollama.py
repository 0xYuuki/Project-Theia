import json
from http.client import HTTPConnection, HTTPException

from theia.errors import AssistantError


DEFAULT_MODEL = "qwen3:1.7b"


class OllamaChat:
    def __init__(self, model=DEFAULT_MODEL):
        self.model = model

    def reply(self, messages):
        payload = json.dumps({
            "model": self.model,
            "messages": messages,
            "stream": False,
            "think": False,
            "keep_alive": "5m",
            "options": {"num_ctx": 4096, "num_predict": 192},
        })
        connection = HTTPConnection("127.0.0.1", 11434, timeout=120)
        try:
            connection.request(
                "POST", "/api/chat", body=payload, headers={"Content-Type": "application/json"},
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

        if not isinstance(result, str) or not result.strip():
            raise AssistantError("The local model returned an empty reply.")
        return result.strip()
