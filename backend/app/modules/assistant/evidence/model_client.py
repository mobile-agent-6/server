"""Model server client for the evidence-only output.

It sends exactly what the model saw in training: the training system prompt
and the same JSON user message (sorted keys, compact), with fixed decoding.
The server is any OpenAI-compatible ``/v1/chat/completions`` endpoint, such as
``mlx_lm.server`` on a development Mac or a llama.cpp server on the Orange Pi.
The model has no Schedule access; it only returns text.
"""

from __future__ import annotations

import hashlib
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from app.modules.assistant.decisions import DecisionProviderTimeout
from app.modules.assistant.evidence.normalize import SEOUL

PROMPT_PATH = Path(__file__).with_name("prompt.txt")
# Training and evaluation used these values (Issue #32).
TEMPERATURE = 0.0
SEED = 29
MAX_TOKENS = 1024
MAX_RESPONSE_BYTES = 1_000_000
READY_TIMEOUT_SECONDS = 3.0


class ModelServerUnavailable(DecisionProviderTimeout):
    """The model server could not be reached; reported like a timeout (retry later)."""


def render_user_message(
    turns: Sequence[str],
    *,
    reference_time: datetime,
    server_time_trusted: bool,
) -> str:
    """The training user message: sorted-key compact JSON of the task context."""

    payload: dict[str, Any] = {
        "stage": "EVIDENCE",
        "taskReferenceTime": reference_time.astimezone(SEOUL).isoformat(timespec="seconds"),
        "timezone": "Asia/Seoul",
        "serverTimeTrusted": server_time_trusted,
        "priorContext": {"kind": "none"},
        "userTurns": [{"turn": index, "text": text} for index, text in enumerate(turns, 1)],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ChatCompletionsModel:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        model: str | None = None,
        prompt: str | None = None,
    ) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("model base URL must be http or https with a host")
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._model = model
        self._prompt = prompt if prompt is not None else PROMPT_PATH.read_text(encoding="utf-8")
        # Shown in the model call log so a run can be tied to its model and prompt.
        self.name = model or "default"
        self.prompt_sha256 = hashlib.sha256(self._prompt.encode("utf-8")).hexdigest()

    def is_ready(self) -> bool:
        """Quick start-up check; any failure means the Assistant stays unavailable."""

        try:
            with urllib.request.urlopen(self._base_url + "/v1/models", timeout=READY_TIMEOUT_SECONDS) as response:
                return 200 <= response.status < 300
        except (OSError, ValueError):
            return False

    def extract(
        self,
        *,
        turns: Sequence[str],
        reference_time: datetime,
        server_time_trusted: bool,
        timeout_seconds: float | None = None,
    ) -> str:
        # The request deadline may leave less time than the configured limit.
        timeout = self._timeout if timeout_seconds is None else min(self._timeout, timeout_seconds)
        body: dict[str, Any] = {
            "messages": [
                {"role": "system", "content": self._prompt},
                {
                    "role": "user",
                    "content": render_user_message(
                        turns, reference_time=reference_time, server_time_trusted=server_time_trusted
                    ),
                },
            ],
            "temperature": TEMPERATURE,
            "max_tokens": MAX_TOKENS,
            "seed": SEED,
        }
        if self._model is not None:
            body["model"] = self._model
        request = urllib.request.Request(
            self._base_url + "/v1/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            if exc.code in {400, 413}:
                # The server rejected this input; asking again would not help.
                raise ValueError(f"model server rejected the request: HTTP {exc.code}") from exc
            raise ModelServerUnavailable(f"model server error: HTTP {exc.code}") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise DecisionProviderTimeout("model server timed out") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise DecisionProviderTimeout("model server timed out") from exc
            raise ModelServerUnavailable("model server is unreachable") from exc
        except OSError as exc:
            raise ModelServerUnavailable("model server connection failed") from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("model server response is too large")
        try:
            content = json.loads(raw)["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ValueError("model server response has no message content") from exc
        if not isinstance(content, str):
            raise ValueError("model message content is not text")
        return content.strip()
