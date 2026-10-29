from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.modules.assistant.decisions import DecisionProviderTimeout
from app.modules.assistant.evidence.model_client import (
    PROMPT_PATH,
    ChatCompletionsModel,
    ModelServerUnavailable,
    render_user_message,
)

TRAINING_PROMPT_SHA256 = "6352d687dd9093f783ff1c23e95d6f84433af30c3776a524aaa980a83db5f24b"
REPO_ROOT = Path(__file__).resolve().parents[3]
DATASET_PROMPT = REPO_ROOT / "datasets/qwen/experiments/structured_output_contract_v0.1/candidate-c-prompt-v0.1.txt"
REFERENCE = datetime(2026, 10, 29, 1, 0, tzinfo=timezone.utc)  # 10:00 in Seoul


class FakeServer:
    """A tiny OpenAI-compatible server that records requests."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.status = 200
        self.body: object = {"choices": [{"message": {"content": "  {\"ok\": true}  "}}]}
        self.delay = 0.0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:
                return

            def _send(self, status: int, payload: object) -> None:
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                self._send(200 if self.path == "/v1/models" else 404, {"data": []})

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                outer.requests.append({"path": self.path, "body": json.loads(self.rfile.read(length))})
                if outer.delay:
                    time.sleep(outer.delay)
                self._send(outer.status, outer.body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        # A client that gave up (timeout test) makes the late write fail; that is expected.
        self.httpd.handle_error = lambda *_args: None
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server() -> Iterator[FakeServer]:
    fake = FakeServer()
    yield fake
    fake.close()


def test_backend_prompt_is_the_training_prompt() -> None:
    assert hashlib.sha256(PROMPT_PATH.read_bytes()).hexdigest() == TRAINING_PROMPT_SHA256
    if DATASET_PROMPT.exists():
        assert PROMPT_PATH.read_bytes() == DATASET_PROMPT.read_bytes()


def test_user_message_matches_the_training_rendering() -> None:
    message = render_user_message(
        ["내일 오후 3시 회의 잡아 줘", "오후요"], reference_time=REFERENCE, server_time_trusted=True
    )
    assert message == (
        '{"priorContext":{"kind":"none"},"serverTimeTrusted":true,"stage":"EVIDENCE",'
        '"taskReferenceTime":"2026-10-29T10:00:00+09:00","timezone":"Asia/Seoul",'
        '"userTurns":[{"text":"내일 오후 3시 회의 잡아 줘","turn":1},{"text":"오후요","turn":2}]}'
    )


def test_extract_sends_training_decoding_and_returns_content(server: FakeServer) -> None:
    model = ChatCompletionsModel(base_url=server.url, timeout_seconds=5)
    raw = model.extract(turns=["회의 잡아 줘"], reference_time=REFERENCE, server_time_trusted=False)
    assert raw == '{"ok": true}'
    sent = server.requests[0]
    assert sent["path"] == "/v1/chat/completions"
    body = sent["body"]
    assert "model" not in body
    assert (body["temperature"], body["seed"], body["max_tokens"]) == (0.0, 29, 1024)
    assert body["messages"][0] == {"role": "system", "content": PROMPT_PATH.read_text(encoding="utf-8")}
    assert body["messages"][1]["role"] == "user"
    assert '"serverTimeTrusted":false' in body["messages"][1]["content"]


def test_extract_sends_model_name_when_configured(server: FakeServer) -> None:
    ChatCompletionsModel(base_url=server.url, timeout_seconds=5, model="qwen-evidence").extract(
        turns=["회의 잡아 줘"], reference_time=REFERENCE, server_time_trusted=True
    )
    assert server.requests[0]["body"]["model"] == "qwen-evidence"


def test_is_ready(server: FakeServer) -> None:
    assert ChatCompletionsModel(base_url=server.url, timeout_seconds=5).is_ready() is True
    assert ChatCompletionsModel(base_url="http://127.0.0.1:9", timeout_seconds=5).is_ready() is False


@pytest.mark.parametrize("status", [400, 413])
def test_rejected_input_is_invalid_output(server: FakeServer, status: int) -> None:
    server.status = status
    with pytest.raises(ValueError):
        ChatCompletionsModel(base_url=server.url, timeout_seconds=5).extract(
            turns=["x"], reference_time=REFERENCE, server_time_trusted=True
        )


@pytest.mark.parametrize("status", [404, 500, 503])
def test_server_errors_are_unavailable(server: FakeServer, status: int) -> None:
    server.status = status
    with pytest.raises(ModelServerUnavailable):
        ChatCompletionsModel(base_url=server.url, timeout_seconds=5).extract(
            turns=["x"], reference_time=REFERENCE, server_time_trusted=True
        )


@pytest.mark.parametrize(
    "body", [{"choices": []}, {"choices": [{"message": {}}]}, {"choices": [{"message": {"content": 3}}]}, b"not json"]
)
def test_malformed_responses_are_invalid_output(server: FakeServer, body: object) -> None:
    server.body = body
    with pytest.raises(ValueError):
        ChatCompletionsModel(base_url=server.url, timeout_seconds=5).extract(
            turns=["x"], reference_time=REFERENCE, server_time_trusted=True
        )


def test_slow_server_times_out(server: FakeServer) -> None:
    server.delay = 0.5
    with pytest.raises(DecisionProviderTimeout):
        ChatCompletionsModel(base_url=server.url, timeout_seconds=0.1).extract(
            turns=["x"], reference_time=REFERENCE, server_time_trusted=True
        )


def test_unreachable_server_is_unavailable() -> None:
    with pytest.raises(ModelServerUnavailable):
        ChatCompletionsModel(base_url="http://127.0.0.1:9", timeout_seconds=1).extract(
            turns=["x"], reference_time=REFERENCE, server_time_trusted=True
        )


@pytest.mark.parametrize("url", ["ftp://example", "localhost:8088", "http://"])
def test_base_url_must_be_http(url: str) -> None:
    with pytest.raises(ValueError):
        ChatCompletionsModel(base_url=url, timeout_seconds=5, prompt="p")
