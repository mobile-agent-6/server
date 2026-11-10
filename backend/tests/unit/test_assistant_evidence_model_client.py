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

from app.modules.assistant.decisions import (
    DecisionProviderInvalidOutput,
    DecisionProviderTimeout,
)
from app.modules.assistant.evidence.model_client import (
    EXPECTED_TRAINING_PROMPT_SHA256,
    PROMPT_PATH,
    ChatCompletionsModel,
    ModelServerUnavailable,
    TrainingPromptIntegrityError,
    load_verified_training_prompt,
    render_user_message,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT_ROOT = REPO_ROOT / "datasets/qwen/experiments/logistics400_v17"
DATASET_PROMPT = EXPERIMENT_ROOT / "runtime/backend/app/modules/assistant/evidence/prompt.txt"
MODEL_INFO = EXPERIMENT_ROOT / "model-info.json"
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
    assert hashlib.sha256(PROMPT_PATH.read_bytes()).hexdigest() == EXPECTED_TRAINING_PROMPT_SHA256
    assert PROMPT_PATH.read_bytes() == DATASET_PROMPT.read_bytes()
    assert json.loads(MODEL_INFO.read_text(encoding="utf-8"))["systemPromptSha256"] == (
        EXPECTED_TRAINING_PROMPT_SHA256
    )


def test_verified_training_prompt_loads_exact_bytes() -> None:
    assert load_verified_training_prompt() == PROMPT_PATH.read_bytes().decode("utf-8")


def test_verified_training_prompt_rejects_hash_mismatch(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_bytes(b"not the training prompt")
    with pytest.raises(TrainingPromptIntegrityError) as captured:
        load_verified_training_prompt(prompt)
    assert captured.value.actual_sha256 == hashlib.sha256(prompt.read_bytes()).hexdigest()


def test_verified_training_prompt_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(TrainingPromptIntegrityError) as captured:
        load_verified_training_prompt(tmp_path / "missing.txt")
    assert captured.value.actual_sha256 is None
    assert isinstance(captured.value.__cause__, FileNotFoundError)


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
    assert body["messages"][0] == {
        "role": "system",
        "content": PROMPT_PATH.read_bytes().decode("utf-8"),
    }
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
    with pytest.raises(DecisionProviderInvalidOutput, match=f"HTTP {status}"):
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
    with pytest.raises(DecisionProviderInvalidOutput):
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


def test_client_exposes_model_name_and_prompt_hash_for_logs() -> None:
    import hashlib

    client = ChatCompletionsModel(base_url="http://127.0.0.1:9", timeout_seconds=1, prompt="P")
    assert client.name == "default"
    assert client.prompt_sha256 == hashlib.sha256(b"P").hexdigest()


def test_deadline_budget_shortens_the_configured_timeout() -> None:
    server = FakeServer()
    server.body = {"choices": [{"message": {"content": "{}"}}]}
    seen: list[float] = []
    import urllib.request

    original = urllib.request.urlopen

    def spy(request, timeout):
        seen.append(timeout)
        return original(request, timeout=timeout)

    urllib.request.urlopen = spy
    try:
        client = ChatCompletionsModel(base_url=server.url, timeout_seconds=60, prompt="P")
        client.extract(turns=["회의"], reference_time=REFERENCE, server_time_trusted=True, timeout_seconds=12.5)
        client.extract(turns=["회의"], reference_time=REFERENCE, server_time_trusted=True, timeout_seconds=90)
        client.extract(turns=["회의"], reference_time=REFERENCE, server_time_trusted=True)
    finally:
        urllib.request.urlopen = original
        server.close()
    assert seen == [12.5, 60, 60]
