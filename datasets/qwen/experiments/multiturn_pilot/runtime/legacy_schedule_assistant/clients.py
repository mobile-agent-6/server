"""Local Ollama and existing Schedule HTTP API adapters."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ScheduleError(Exception):
    code: str
    uncertain: bool = False


class ScheduleClient:
    def __init__(self, base_url: str, timeout: float = 10.0):
        parsed = urllib.parse.urlsplit(base_url)
        if (parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username
                or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            raise ValueError("invalid Schedule API base URL")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None,
                 key: str | None = None) -> Any:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if key is not None:
            headers["Idempotency-Key"] = key
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        had_uncertain_write = False
        for attempt in range(2):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = response.read()
                    try:
                        return json.loads(payload) if payload else None
                    except (ValueError, UnicodeError) as exc:
                        raise ScheduleError("INVALID_RESPONSE", uncertain=method != "GET") from exc
            except urllib.error.HTTPError as exc:
                if exc.code == 503 and attempt == 0:
                    had_uncertain_write = method != "GET"
                    time.sleep(1)
                    continue
                try:
                    problem = json.loads(exc.read().decode("utf-8"))
                    code = problem.get("code", f"HTTP_{exc.code}")
                except (ValueError, UnicodeError):
                    code = f"HTTP_{exc.code}"
                raise ScheduleError(str(code), uncertain=method != "GET" and
                                    (exc.code == 503 or had_uncertain_write)) from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt == 0:
                    had_uncertain_write = method != "GET"
                    time.sleep(1)
                    continue
                raise ScheduleError("CONNECTION_UNCERTAIN", uncertain=method != "GET") from exc
        raise AssertionError("unreachable")

    def list(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        params = {k: (str(v).lower() if isinstance(v, bool) else v) for k, v in filters.items()}
        result = self._request("GET", "/api/v1/schedules?" + urllib.parse.urlencode(params))
        if not isinstance(result, dict) or not isinstance(result.get("items"), list):
            raise ScheduleError("INVALID_RESPONSE")
        return result["items"]

    def get(self, schedule_id: str) -> dict[str, Any]:
        result = self._request("GET", "/api/v1/schedules/" + schedule_id)
        if not isinstance(result, dict) or result.get("id") != schedule_id:
            raise ScheduleError("INVALID_RESPONSE")
        return result

    def create(self, body: dict[str, Any], key: str) -> dict[str, Any]:
        result = self._request("POST", "/api/v1/schedules", body, key)
        if not isinstance(result, dict) or not isinstance(result.get("id"), str):
            raise ScheduleError("INVALID_RESPONSE", uncertain=True)
        return result

    def update(self, schedule_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        result = self._request("PATCH", "/api/v1/schedules/" + schedule_id, changes)
        if not isinstance(result, dict) or result.get("id") != schedule_id:
            raise ScheduleError("INVALID_RESPONSE", uncertain=True)
        return result

    def delete(self, schedule_id: str) -> None:
        self._request("DELETE", "/api/v1/schedules/" + schedule_id)


class OllamaClient:
    def __init__(self, model: str, base_url: str = "http://127.0.0.1:11434", timeout: float = 120.0):
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"} or not model.strip() or timeout <= 0:
            raise ValueError("Ollama must be local and model/timeout valid")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.last_metrics: dict[str, int] = {}

    def generate(self, messages: list[dict[str, str]]) -> str:
        self.last_metrics = {}
        payload = json.dumps({"model": self.model, "messages": messages, "format": "json",
                              "think": False, "stream": False}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.base_url + "/api/chat", data=payload,
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError, UnicodeError) as exc:
            raise RuntimeError("local Qwen request failed") from exc
        content = result.get("message", {}).get("content") if isinstance(result, dict) else None
        if not isinstance(content, str):
            raise RuntimeError("local Qwen response has no content")
        self.last_metrics = {key: result[key] for key in (
            "prompt_eval_count", "eval_count", "total_duration", "load_duration",
            "prompt_eval_duration", "eval_duration")
            if type(result.get(key)) is int}
        return content.strip()
