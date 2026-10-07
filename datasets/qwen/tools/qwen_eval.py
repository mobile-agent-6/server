"""Offline, provisional scorer for the archived six-field Qwen experiment.

This does not define the shared AI output contract. It keeps the 2026-09-25
experiment reproducible while reporting output validity separately from values.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

FIELDS = ("intent", "date", "time", "location", "category", "content")
INTENTS = {"create", "read", "update", "delete"}
LEGACY_CATEGORIES = {"반품", "재고", "입고", "피킹", "출고"}
DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
TIME_PATTERN = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d\Z")
DEFAULT_CASES = Path(__file__).resolve().parents[1] / "v0.1_initial" / "qwen_legacy_eval_100.jsonl"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON") from exc
            if not isinstance(item, dict):
                raise ValueError(f"{path}:{line_number}: expected JSON object")
            items.append(item)
    return items


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases = read_jsonl(path)
    if not cases:
        raise ValueError("case file is empty")
    seen: set[str] = set()
    for item in cases:
        identifier = item.get("id")
        expected = item.get("expected")
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            raise ValueError(f"invalid or duplicate case id: {identifier!r}")
        seen.add(identifier)
        if not isinstance(item.get("utterance"), str) or not item["utterance"]:
            raise ValueError(f"{identifier}: utterance is required")
        if not isinstance(item.get("reference_date"), str):
            raise ValueError(f"{identifier}: reference_date is required")
        if not isinstance(expected, dict) or set(expected) != set(FIELDS):
            raise ValueError(f"{identifier}: expected must contain exactly six fields")
        if any(value is not None and not isinstance(value, str) for value in expected.values()):
            raise ValueError(f"{identifier}: expected values must be strings or null")
    return cases


def load_legacy_csv(path: Path, cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"번호", "사용자 발화", "Qwen 결과 원문"}
        required.update(f"정답 {field}" for field in FIELDS)
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError("legacy CSV is missing required columns")
        rows = list(reader)
    if len(rows) != len(cases):
        raise ValueError("legacy CSV and case file have different row counts")
    responses = {}
    for index, (case, row) in enumerate(zip(cases, rows, strict=True), 1):
        if row["번호"] != str(index) or row["사용자 발화"] != case["utterance"]:
            raise ValueError(f"legacy CSV row {index} does not match case file")
        for field in FIELDS:
            if (row[f"정답 {field}"] or None) != case["expected"][field]:
                raise ValueError(f"legacy CSV row {index} has a different {field} label")
        responses[case["id"]] = {"raw_response": row["Qwen 결과 원문"]}
    return responses


def load_responses(path: Path, cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    expected_ids = {case["id"] for case in cases}
    responses: dict[str, dict[str, Any]] = {}
    for item in read_jsonl(path):
        identifier = item.get("id")
        if not isinstance(identifier, str) or identifier not in expected_ids:
            raise ValueError(f"unknown response id: {identifier!r}")
        if identifier in responses:
            raise ValueError(f"duplicate response id: {identifier}")
        if not isinstance(item.get("raw_response"), str):
            raise ValueError(f"{identifier}: raw_response must be a string")
        if "wall_time_ms" in item and not valid_number(item["wall_time_ms"]):
            raise ValueError(f"{identifier}: wall_time_ms must be a nonnegative finite number")
        metrics = item.get("ollama_metrics", {})
        if not isinstance(metrics, dict) or any(not valid_number(value) for value in metrics.values()):
            raise ValueError(f"{identifier}: ollama_metrics must contain nonnegative finite numbers")
        if item.get("error_type") is not None and not isinstance(item["error_type"], str):
            raise ValueError(f"{identifier}: error_type must be a string or null")
        responses[identifier] = item
    missing = expected_ids - responses.keys()
    if missing:
        raise ValueError(f"missing responses: {', '.join(sorted(missing))}")
    return responses


def valid_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def percentile(values: list[float], percent: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(len(ordered) * percent / 100) - 1]


def speed_summary(responses: dict[str, dict[str, Any]]) -> dict[str, Any]:
    completed = [item for item in responses.values() if not item.get("error_type")]
    wall = [item["wall_time_ms"] for item in completed if valid_number(item.get("wall_time_ms"))]
    loads = [item.get("ollama_metrics", {}).get("load_duration") / 1_000_000
             for item in completed if valid_number(item.get("ollama_metrics", {}).get("load_duration"))]
    rates = []
    for item in completed:
        metrics = item.get("ollama_metrics", {})
        duration = metrics.get("eval_duration")
        tokens = metrics.get("eval_count")
        if valid_number(duration) and duration > 0 and valid_number(tokens):
            rates.append(tokens / (duration / 1_000_000_000))
    return {
        "timed_count": len(wall),
        "request_error_count": len(responses) - len(completed),
        "wall_time_ms_median": statistics.median(wall) if wall else None,
        "wall_time_ms_p95_nearest_rank": percentile(wall, 95),
        "load_duration_ms_median": statistics.median(loads) if loads else None,
        "output_tokens_per_second_median": statistics.median(rates) if rates else None,
        "token_rate_count": len(rates),
    }


def format_issues(actual: Any) -> list[str]:
    if not isinstance(actual, dict):
        return ["response_not_object"]
    issues = []
    for field in FIELDS:
        if field not in actual:
            issues.append(f"missing:{field}")
    for field in sorted(set(actual) - set(FIELDS)):
        issues.append(f"extra:{field}")
    for field in FIELDS:
        if field not in actual:
            continue
        value = actual[field]
        if value is not None and not isinstance(value, str):
            issues.append(f"type:{field}")
            continue
        if isinstance(value, str) and value.strip().lower() in {"null", "none"}:
            issues.append(f"string_null:{field}")
        if field == "intent" and value not in INTENTS:
            issues.append("value:intent")
        if field == "category" and value is not None and value not in LEGACY_CATEGORIES:
            issues.append("value:category")
        if field == "date" and value is not None:
            if not DATE_PATTERN.fullmatch(value):
                issues.append("format:date")
            else:
                try:
                    date.fromisoformat(value)
                except ValueError:
                    issues.append("value:date")
        if field == "time" and value is not None and not TIME_PATTERN.fullmatch(value):
            issues.append("format:time")
    return issues


def legacy_normalize(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.lower() in {"", "null", "none"}:
            return None
        return stripped
    return value


def score(cases: list[dict[str, Any]], responses: dict[str, dict[str, Any]]) -> dict[str, Any]:
    field_stats = {field: {"exact": 0, "nonnull_exact": 0, "nonnull_total": 0} for field in FIELDS}
    intent_stats = {intent: {"total": 0, "exact": 0} for intent in sorted(INTENTS)}
    issue_counts: Counter[str] = Counter()
    results = []
    valid_json = valid_format = exact = legacy_full = 0
    string_null_rows = 0
    for case in cases:
        identifier = case["id"]
        expected = case["expected"]
        raw = responses[identifier]["raw_response"]
        try:
            actual = json.loads(raw)
        except json.JSONDecodeError:
            actual = None
            issues = ["invalid_json"]
        else:
            valid_json += 1
            issues = format_issues(actual)
        issue_counts.update(issues)
        string_null_rows += any(issue.startswith("string_null:") for issue in issues)
        valid_format += not issues
        matched = not issues and all(actual[field] == expected[field] for field in FIELDS)
        exact += matched
        normalized = isinstance(actual, dict) and all(
            legacy_normalize(actual.get(field)) == expected[field] for field in FIELDS
        )
        legacy_full += normalized
        intent_stats[expected["intent"]]["total"] += 1
        intent_stats[expected["intent"]]["exact"] += matched
        for field in FIELDS:
            nonnull = expected[field] is not None
            field_stats[field]["nonnull_total"] += nonnull
            field_match = isinstance(actual, dict) and field in actual and actual[field] == expected[field]
            field_stats[field]["exact"] += field_match
            field_stats[field]["nonnull_exact"] += nonnull and field_match
        results.append({"id": identifier, "issues": issues, "exact": bool(matched), "legacy_normalized_full": bool(normalized)})
    return {
        "ruleset": "legacy-six-field-provisional",
        "total": len(cases),
        "valid_json": valid_json,
        "valid_format": valid_format,
        "exact_full": exact,
        "legacy_normalized_full": legacy_full,
        "string_null_rows": string_null_rows,
        "issues": dict(sorted(issue_counts.items())),
        "fields": field_stats,
        "intents": intent_stats,
        "speed": speed_summary(responses),
        "cases": results,
    }


def compare_reports(before: dict[str, Any], after: dict[str, Any],
                    before_responses: dict[str, dict[str, Any]],
                    after_responses: dict[str, dict[str, Any]]) -> dict[str, Any]:
    before_cases = {item["id"]: item for item in before["cases"]}
    after_cases = {item["id"]: item for item in after["cases"]}
    if before_cases.keys() != after_cases.keys():
        raise ValueError("comparison requires the same case IDs")
    gained = lost = faster = slower = tied = 0
    paired_deltas = []
    for identifier in before_cases:
        old = before_cases[identifier]["exact"]
        new = after_cases[identifier]["exact"]
        gained += not old and new
        lost += old and not new
        first = before_responses[identifier]
        second = after_responses[identifier]
        if first.get("error_type") or second.get("error_type"):
            continue
        a, b = first.get("wall_time_ms"), second.get("wall_time_ms")
        if valid_number(a) and valid_number(b):
            paired_deltas.append(b - a)
            faster += b < a
            slower += b > a
            tied += b == a
    return {
        "before_exact_full": before["exact_full"],
        "after_exact_full": after["exact_full"],
        "quality_gained_cases": gained,
        "quality_lost_cases": lost,
        "paired_timing_count": len(paired_deltas),
        "after_minus_before_wall_ms_median": statistics.median(paired_deltas) if paired_deltas else None,
        "faster_cases": faster,
        "slower_cases": slower,
        "tied_cases": tied,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--legacy-results", type=Path)
    source.add_argument("--responses", type=Path)
    parser.add_argument("--compare-responses", type=Path,
                        help="second run of the same cases; requires --responses")
    parser.add_argument("--output", type=Path, help="write the JSON report to this path")
    parser.add_argument("--summary-only", action="store_true", help="omit per-case details")
    args = parser.parse_args()
    if args.compare_responses and not args.responses:
        parser.error("--compare-responses requires --responses")
    cases = load_cases(args.cases)
    responses = (
        load_legacy_csv(args.legacy_results, cases)
        if args.legacy_results else load_responses(args.responses, cases)
    )
    report = score(cases, responses)
    if args.compare_responses:
        after_responses = load_responses(args.compare_responses, cases)
        after = score(cases, after_responses)
        report = {
            "before": report,
            "after": after,
            "comparison": compare_reports(report, after, responses, after_responses),
        }
    if args.summary_only:
        if args.compare_responses:
            report["before"].pop("cases")
            report["after"].pop("cases")
        else:
            report.pop("cases")
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
