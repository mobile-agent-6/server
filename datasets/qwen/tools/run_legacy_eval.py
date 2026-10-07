"""Run the archived Qwen v0.1 prompts and record quality and timing inputs.

This is a DATA-area experiment, not the shared AI runtime contract.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from qwen_eval import DEFAULT_CASES, load_cases

PROMPT_TEMPLATE = DEFAULT_CASES.with_name("prompt_v01.txt")
REFERENCE_DATE = "2026-09-25"
METRICS = (
    "total_duration", "load_duration", "prompt_eval_duration", "eval_duration",
    "prompt_eval_count", "prompt_eval_cached_count", "eval_count",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def legacy_prompt(utterance: str, reference_date: str) -> str:
    if reference_date != REFERENCE_DATE:
        raise ValueError(f"v0.1 prompt requires reference_date={REFERENCE_DATE}")
    template = PROMPT_TEMPLATE.read_text(encoding="utf-8")
    return template.replace("{reference_date}", reference_date).replace("{utterance}", utterance)


def request_one(
    prompt: str, model: str, base_url: str, timeout: float,
    opener: Any = urllib.request.urlopen, clock: Any = time.perf_counter_ns,
) -> dict[str, Any]:
    payload = json.dumps({
        "model": model, "prompt": prompt, "format": "json", "think": False,
        "stream": False,
    }).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/generate", data=payload,
        headers={"Content-Type": "application/json"},
    )
    start = clock()
    try:
        with opener(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        if not isinstance(body, dict) or not isinstance(body.get("response"), str):
            raise ValueError("Ollama response has no text field")
        metrics = {key: body[key] for key in METRICS if key in body}
        return {
            "raw_response": body["response"].strip(),
            "wall_time_ms": (clock() - start) / 1_000_000,
            "ollama_metrics": metrics,
            "error_type": None,
        }
    except (urllib.error.URLError, TimeoutError, ValueError, UnicodeError) as exc:
        return {
            "raw_response": "",
            "wall_time_ms": (clock() - start) / 1_000_000,
            "ollama_metrics": {},
            "error_type": type(exc).__name__,
        }


def run(
    cases_path: Path, output_dir: Path, model: str, base_url: str,
    timeout: float, model_digest: str | None = None, quantization: str | None = None,
) -> None:
    cases = load_cases(cases_path)
    if any(case["reference_date"] != REFERENCE_DATE for case in cases):
        raise ValueError("v0.1 runner only supports the archived reference date")
    if not model.strip() or timeout <= 0:
        raise ValueError("model must be nonempty and timeout must be positive")
    if not base_url.startswith(("http://127.0.0.1:", "http://localhost:")):
        raise ValueError("only a local Ollama endpoint is supported")
    output_dir.mkdir(parents=True, exist_ok=False)
    manifest = {
        "status": "running",
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "experiment": "qwen-v0.1-legacy-provisional",
        "cases_sha256": sha256(cases_path),
        "prompt_template_sha256": sha256(PROMPT_TEMPLATE),
        "model": model,
        "model_digest": model_digest,
        "quantization": quantization,
        "runtime": "ollama-api-generate",
        "generation": {"format": "json", "think": False, "stream": False},
        "timeout_seconds": timeout,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "case_count": len(cases),
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    completed = 0
    try:
        with (output_dir / "responses.jsonl").open("w", encoding="utf-8") as stream:
            for case in cases:
                prompt = legacy_prompt(case["utterance"], case["reference_date"])
                result = request_one(prompt, model, base_url, timeout)
                row = {"id": case["id"], "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(), **result}
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                stream.flush()
                completed += 1
                print(f"{completed}/{len(cases)} {case['id']} {result['wall_time_ms']:.1f} ms")
    finally:
        manifest["status"] = "complete" if completed == len(cases) else "interrupted"
        manifest["completed_count"] = completed
        manifest["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output-dir", type=Path, required=True, help="new directory for run files")
    parser.add_argument("--model", required=True)
    parser.add_argument("--model-digest", help="record the exact model digest when known")
    parser.add_argument("--quantization", help="record the model quantization when known")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()
    run(args.cases, args.output_dir, args.model, args.base_url, args.timeout,
        args.model_digest, args.quantization)


if __name__ == "__main__":
    main()
