"""Gọi model qua API gốc của Ollama (hoặc OpenAI-compatible).

Dùng /api/chat thay vì /v1 vì chỉ /api/chat mới đặt được num_ctx —
qua /v1 Ollama tự kẹp context xuống 8192 và chương dài sẽ bị từ chối.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_NUM_CTX = 16384
DEFAULT_TIMEOUT = 1800

# Đo thực tế với qwen2.5 trên tiếng Việt: ~0.34 token mỗi ký tự
TOKENS_PER_CHAR = 0.35


def count_words(text: str) -> int:
    return len((text or "").split())


class OllamaError(RuntimeError):
    """Ollama trả lỗi hoặc không gọi được."""


class OllamaClient:
    def __init__(
        self,
        model: str,
        num_ctx: int = DEFAULT_NUM_CTX,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self.model = model
        self.num_ctx = num_ctx
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.calls = 0

    def chat(self, system: str, user: str, temperature: float = 0.75) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {"num_ctx": self.num_ctx, "temperature": temperature},
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            if "not found" in detail:
                raise OllamaError(
                    f"Model '{self.model}' chưa tải.\n"
                    f"  Tải bằng:  ollama pull {self.model}\n"
                    f"  Hoặc xem model đang có:  ollama list"
                ) from exc
            raise OllamaError(f"Ollama từ chối request: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise OllamaError(f"Không gọi được Ollama tại {self.base_url}: {exc}") from exc

        self.calls += 1
        return (body.get("message", {}).get("content") or "").strip()

    def tokens_left_after(self, prompt: str, answer_chars: int) -> int:
        """Còn bao nhiêu token trống sau khi nạp prompt + sinh câu trả lời dự kiến."""
        return int(self.num_ctx - (len(prompt) + answer_chars) * TOKENS_PER_CHAR)


def ask(client: OllamaClient, system: str, user: str, temperature: float = 0.75) -> str:
    return client.chat(system, user, temperature=temperature)
