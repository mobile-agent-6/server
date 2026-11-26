"""Deterministic Issue #30 contract and mutation validator (no model/server/DB)."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from contracts import (  # noqa: E402
    CONTRACT_SPECS,
    ContractError,
    candidate_a_context,
    common_backend_gate,
    strict_json_loads,
    validate_candidate_a,
    validate_candidate_b,
    validate_candidate_c,
    validate_rule_intent,
)
from rule_baseline import RULE_BASELINE_SPEC, assess_rule_case  # noqa: E402

LOCKED_PATH = HERE / "locked-cases-v0.1.jsonl"
SCHEMA_PATH = HERE / "semantic-oracle-schema-v0.1.json"
LOCK_MANIFEST_PATH = HERE / "locked-cases-manifest-v0.1.json"
FIXTURE_PATH = HERE / "mutation-fixtures-v0.1.jsonl"
EXPECTED_LOCKED_SHA256 = "17c7d4ef10aff8171d422c248431d3c34faaf03bf7282fab5dc3abcfa2389b57"
EXPECTED_SCHEMA_SHA256 = "cfe0ed947f6bd709e0e3ba60a8daf63fb1f201614cb11d90b823931b060a3b1d"
RESULT_VERSION = "issue30-contract-validation-v0.1"
FIXED_NOW = datetime.fromisoformat("2028-07-01T10:00:00+09:00")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            raise ValueError(f"blank JSONL line: {path}:{number}")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"non-object JSONL line: {path}:{number}")
        rows.append(value)
    return rows


def assert_locked_integrity() -> dict[str, Any]:
    manifest = json.loads(LOCK_MANIFEST_PATH.read_text(encoding="utf-8"))
    locked_hash = sha256(LOCKED_PATH)
    schema_hash = sha256(SCHEMA_PATH)
    cases = load_jsonl(LOCKED_PATH)
    if manifest.get("lockStatus") != "locked":
        raise RuntimeError("LOCK_STATUS_CHANGED")
    if manifest.get("caseCount") != 56 or len(cases) != 56:
        raise RuntimeError("LOCKED_CASE_COUNT_CHANGED")
    if locked_hash != EXPECTED_LOCKED_SHA256:
        raise RuntimeError("LOCKED_JSONL_HASH_CHANGED")
    if schema_hash != EXPECTED_SCHEMA_SHA256:
        raise RuntimeError("SEMANTIC_SCHEMA_HASH_CHANGED")
    return {"lockStatus": "locked", "caseCount": 56, "lockedSha256": locked_hash, "schemaSha256": schema_hash}


def _all_evidence_text(operation: Mapping[str, Any], role: str) -> list[str]:
    result: list[str] = []
    for slot_value in operation["evidence"].get(role, {}).values():
        result.extend(span["text"] for span in slot_value.get("adoptedSpans", []))
    return result


def classify_candidate_a(case: Mapping[str, Any]) -> tuple[str, str]:
    operations = case["semantics"]["operations"]
    if len(operations) > 1:
        return "structurally_unrepresentable", "DATA v2.6 is a single-action state machine"
    if not operations:
        return "safe_reject", "generic ASK can fail closed but cannot execute the unsupported request"
    operation = operations[0]
    evidence_text = " ".join(
        text
        for role in ("target", "change", "filter")
        for text in _all_evidence_text(operation, role)
    )
    date_spans = []
    for role in ("target", "change", "filter"):
        value = operation["evidence"].get(role, {}).get("date", {})
        date_spans.extend(span["text"] for span in value.get("adoptedSpans", []))
    if any(text not in {"오늘", "내일", "모레"} for text in date_spans):
        return "structurally_unrepresentable", "DATA v2.6 only normalizes today/tomorrow/day-after-tomorrow"
    if operation["kind"] in {"list", "update", "delete"}:
        lookup = operation["normalized"].get("targetLookup") or operation["normalized"].get("query") or {}
        if lookup.get("rangeMode") == "unspecified_all_periods" or lookup.get("mode") == "title_search_all_periods":
            return "structurally_unrepresentable", "DATA v2.6 requires a supported day range before lookup"
        if not date_spans:
            return "structurally_unrepresentable", "DATA v2.6 cannot form this lookup without today/tomorrow/day-after-tomorrow evidence"
    if len(date_spans) > 1:
        return "structurally_unrepresentable", "DATA v2.6 cannot preserve separate target and change dates"
    time_spans = []
    for role in ("target", "change", "filter"):
        value = operation["evidence"].get(role, {}).get("time", {})
        time_spans.extend(span["text"] for span in value.get("adoptedSpans", []))
    if any("반" in text for text in time_spans):
        return "safe_reject", "known DATA v2.6 normalizer defect drops the half-hour minute"
    if not case["context"]["serverTimeTrusted"] and operation["normalized"]["timeInterpretation"]["dependsOnReferenceTime"]:
        return "safe_reject", "DATA v2.6 context has no serverTimeTrusted contract"
    return "representable", "existing controller context, dynamic schema and write guard can express the safe path"


def classify_candidate_b(case: Mapping[str, Any]) -> tuple[str, str]:
    if len(case["semantics"]["operations"]) > 1:
        return "structurally_unrepresentable", "B v0.1 has one intent and one normalized extraction call"
    return "representable", "single intent plus one operation-specific normalized object is sufficient"


def classify_candidate_c(case: Mapping[str, Any]) -> tuple[str, str]:
    candidate_c_from_case(case)  # validates transform preconditions without normalizing values
    return "representable", "ordered operation and exact role-span evidence are directly representable"


def _span_rows(value: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for key, status in (
        ("adoptedSpans", "adopted"),
        ("supersededSpans", "superseded"),
        ("correctionMarkerSpans", "correction_marker"),
    ):
        for span in value.get(key, []):
            converted = {"turn": span["turn"], "text": span["text"], "status": status}
            if "occurrence" in span:
                converted["occurrence"] = span["occurrence"]
            result.append(converted)
    return result


def candidate_c_from_case(case: Mapping[str, Any]) -> dict[str, Any]:
    operations = []
    unsupported_by_operation: dict[int, list[dict[str, Any]]] = defaultdict(list)
    top_unsupported: list[dict[str, Any]] = []
    for unsupported in case["semantics"]["unsupportedRequests"]:
        spans = _span_rows(unsupported["cue"])
        top_unsupported.extend(spans)
        for index in unsupported.get("relatedOperationIndexes", []):
            unsupported_by_operation[index].extend(spans)
    all_missing: list[str] = []
    for operation in case["semantics"]["operations"]:
        mapped: dict[str, Any] = {}
        for target_key, source_key in (
            ("targetEvidence", "target"),
            ("changeEvidence", "change"),
            ("filterEvidence", "filter"),
        ):
            mapped[target_key] = {
                slot: _span_rows(value)
                for slot, value in operation["evidence"][source_key].items()
            }
        missing = [item["item"] for item in operation["missingInformation"]]
        all_missing.extend(missing)
        operations.append(
            {
                "index": operation["index"],
                "kind": operation["kind"],
                **mapped,
                "unsupportedEvidence": unsupported_by_operation[operation["index"]],
                "missingInformation": list(dict.fromkeys(missing)),
            }
        )
    return {
        "operations": operations,
        "unsupportedEvidence": top_unsupported,
        "missingInformation": list(dict.fromkeys(all_missing)),
    }


def representation_rows(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        case_id = case["meta"]["caseId"]
        for candidate in ("A", "B", "C", "R"):
            if candidate == "A":
                classification, reason = classify_candidate_a(case)
            elif candidate == "B":
                classification, reason = classify_candidate_b(case)
            elif candidate == "C":
                classification, reason = classify_candidate_c(case)
            else:
                assessment = assess_rule_case(case)
                classification, reason = assessment.classification, assessment.reason
            rows.append(
                {
                    "recordType": "locked_representability",
                    "version": RESULT_VERSION,
                    "caseId": case_id,
                    "candidate": candidate,
                    "classification": classification,
                    "reason": reason,
                    "cause": None,
                    "systemOutcome": "correct" if classification == "representable" else "safe_reject",
                }
            )
    return rows


def minimal_inputs(candidate: str) -> dict[str, Any]:
    if candidate == "A":
        users = ["일정 등록해 줘"]
        context = candidate_a_context(users, FIXED_NOW, intent="create")
        return {
            "raw": json.dumps({"action": "ASK", "message": "어떤 업무를 등록할까요?", "request": None}, ensure_ascii=False),
            "context": context,
            "users": users,
            "now": FIXED_NOW,
        }
    if candidate == "B":
        return {"stage1": '{"intent":"unsupported"}', "stage2": None, "grounded": {}}
    if candidate == "C":
        return {
            "raw": json.dumps(
                {
                    "operations": [],
                    "unsupportedEvidence": [{"turn": 1, "text": "점심 메뉴", "status": "adopted"}],
                    "missingInformation": [],
                },
                ensure_ascii=False,
            ),
            "turns": {1: "점심 메뉴 추천해 줘"},
        }
    return {"raw": '{"intent":"UNSUPPORTED"}'}


def validate_local(candidate: str, local: Mapping[str, Any]) -> dict[str, Any]:
    if candidate == "A":
        return validate_candidate_a(
            local["raw"],
            local["context"],
            users=list(local["users"]),
            now=local["now"],
            current=local.get("current"),
            run_write_guard=local.get("runWriteGuard", True),
        )
    if candidate == "B":
        return validate_candidate_b(local["stage1"], local.get("stage2"), grounded=local.get("grounded"))
    if candidate == "C":
        return validate_candidate_c(local["raw"], user_turns=local["turns"])
    return validate_rule_intent(local["raw"])


def _format_mutation(candidate: str, kind: str) -> dict[str, Any]:
    base = minimal_inputs(candidate)
    if kind == "duplicate_key":
        raw = {
            "A": '{"action":"ASK","action":"ANSWER","message":"질문할까요?","request":null}',
            "B": '{"intent":"unsupported","intent":"unrelated"}',
            "C": '{"operations":[],"operations":[],"unsupportedEvidence":[],"missingInformation":[]}',
            "R": '{"intent":"UNSUPPORTED","intent":"UNRELATED"}',
        }[candidate]
        key = "raw" if candidate != "B" else "stage1"
        base[key] = raw
    elif kind == "extra_field":
        raw = json.loads(base.get("raw", base.get("stage1")))
        raw["extra"] = True
        base["raw" if candidate != "B" else "stage1"] = json.dumps(raw, ensure_ascii=False)
    elif kind == "missing_field":
        raw = json.loads(base.get("raw", base.get("stage1")))
        raw.pop(next(iter(raw)))
        base["raw" if candidate != "B" else "stage1"] = json.dumps(raw, ensure_ascii=False)
    elif kind == "wrong_type":
        raw = json.loads(base.get("raw", base.get("stage1")))
        first = next(iter(raw))
        raw[first] = 7
        base["raw" if candidate != "B" else "stage1"] = json.dumps(raw, ensure_ascii=False)
    elif kind == "invalid_enum":
        raw = json.loads(base.get("raw", base.get("stage1")))
        raw["action" if candidate == "A" else "intent"] = "NOT_AN_ENUM"
        base["raw" if candidate != "B" else "stage1"] = json.dumps(raw, ensure_ascii=False)
    elif kind == "length_overflow":
        if candidate == "A":
            raw = json.loads(base["raw"]); raw["message"] = "가" * 241; base["raw"] = json.dumps(raw, ensure_ascii=False)
        elif candidate == "B":
            base = {
                "stage1": '{"intent":"create"}',
                "stage2": json.dumps({"operation":"create","title":"가"*101,"scheduledAt":None,"category":None,"missingInformation":["date"]}, ensure_ascii=False),
                "grounded": None,
            }
        elif candidate == "C":
            base["turns"] = {1: "가" * 501}
            base["raw"] = json.dumps({"operations":[],"unsupportedEvidence":[{"turn":1,"text":"가"*501,"status":"adopted"}],"missingInformation":[]}, ensure_ascii=False)
        else:
            base["raw"] = json.dumps({"intent": "U" * 300})
    elif kind == "payload_overflow":
        key = "raw" if candidate != "B" else "stage1"
        base[key] = base[key] + (" " * 17_000)
    elif kind == "control_character":
        key = "raw" if candidate != "B" else "stage1"
        raw = json.loads(base[key])
        field = "message" if candidate == "A" else ("intent" if candidate in {"B", "R"} else "missingInformation")
        raw[field] = "bad\u0001" if field != "missingInformation" else ["bad\u0001"]
        base[key] = json.dumps(raw, ensure_ascii=False)
    return base


def _special_local_input(candidate: str, kind: str) -> dict[str, Any]:
    if kind in {"duplicate_key", "extra_field", "missing_field", "wrong_type", "invalid_enum", "length_overflow", "payload_overflow", "control_character"}:
        return _format_mutation(candidate, kind)
    if kind.startswith("c_"):
        if kind == "c_forbidden_datetime":
            turns = {1: "2028-07-03T19:30:00+09:00로 바꿔 줘"}
            raw = {"operations":[{"index":1,"kind":"update","targetEvidence":{},"changeEvidence":{"time":[{"turn":1,"text":"2028-07-03T19:30:00+09:00","status":"adopted"}]},"filterEvidence":{},"unsupportedEvidence":[],"missingInformation":[]}],"unsupportedEvidence":[],"missingInformation":[]}
            return {"raw": json.dumps(raw, ensure_ascii=False), "turns": turns}
        if kind == "c_forbidden_id":
            value = "11111111-1111-4111-8111-111111111111"
            turns = {1: value + " 지워 줘"}
            raw = {"operations":[{"index":1,"kind":"delete","targetEvidence":{"title":[{"turn":1,"text":value,"status":"adopted"}]},"changeEvidence":{},"filterEvidence":{},"unsupportedEvidence":[],"missingInformation":[]}],"unsupportedEvidence":[],"missingInformation":[]}
            return {"raw": json.dumps(raw, ensure_ascii=False), "turns": turns}
        if kind == "c_cross_turn_swapped":
            turns = {
                2: "모레요",
                3: "오전 10시로 하고, 날짜는 모레 말고 글피로 바꿔 줘",
            }
            spans = [
                {"turn": 2, "text": "모레", "status": "adopted"},
                {"turn": 3, "text": "글피", "status": "superseded"},
                {"turn": 3, "text": "말고", "status": "correction_marker"},
            ]
            raw = {"operations":[{"index":1,"kind":"create","targetEvidence":{},"changeEvidence":{"date":spans},"filterEvidence":{},"unsupportedEvidence":[],"missingInformation":[]}],"unsupportedEvidence":[],"missingInformation":[]}
            return {"raw": json.dumps(raw, ensure_ascii=False), "turns": turns}
        turns = {1: "오전 11시 아니 오후 1시로 바꿔 줘"}
        spans = [
            {"turn": 1, "text": "오후 1시", "status": "adopted"},
            {"turn": 1, "text": "오전 11시", "status": "superseded"},
            {"turn": 1, "text": "아니", "status": "correction_marker"},
        ]
        if kind == "c_non_substring": spans[0]["text"] = "오후 4시"
        if kind == "c_wrong_turn": spans[0]["turn"] = 99
        if kind == "c_swapped": spans[0]["status"], spans[1]["status"] = spans[1]["status"], spans[0]["status"]
        raw = {"operations":[{"index":1,"kind":"update","targetEvidence":{},"changeEvidence":{"time":spans},"filterEvidence":{},"unsupportedEvidence":[],"missingInformation":[]}],"unsupportedEvidence":[],"missingInformation":[]}
        return {"raw": json.dumps(raw, ensure_ascii=False), "turns": turns}
    if kind == "a_completed_filter_gap":
        users = ["내일 반품 일정 보여 줘"]
        context = candidate_a_context(users, FIXED_NOW, intent="query", query_from="2028-07-02T00:00:00+09:00", query_to="2028-07-03T00:00:00+09:00", query_category="RETURN")
        return {"raw": json.dumps({"action":"LIST","message":None,"request":{"from":"2028-07-02T00:00:00+09:00","to":"2028-07-03T00:00:00+09:00","category":"RETURN","completed":True}}, ensure_ascii=False),"context":context,"users":users,"now":FIXED_NOW}
    if kind == "a_half_minute_gap":
        users = ["모레 오후 7시 반에 반품 사진 확인 등록해 줘"]
        context = candidate_a_context(users, FIXED_NOW, intent="create")
        return {"raw": json.dumps({"action":"CREATE","message":None,"request":{"title":"반품 사진 확인","scheduledAt":"2028-07-03T19:00:00+09:00","category":"RETURN"}}, ensure_ascii=False),"context":context,"users":users,"now":FIXED_NOW}
    if kind == "b_fabricated_datetime":
        return {"stage1":'{"intent":"create"}',"stage2":json.dumps({"operation":"create","title":"입고 확인","scheduledAt":"2028-07-03T12:00:00+09:00","category":"INBOUND","missingInformation":[]}, ensure_ascii=False),"grounded":{"title":{"입고 확인"},"scheduledAt":{"2028-07-03T10:00:00+09:00"},"category":{"INBOUND"}}}
    return minimal_inputs(candidate)


def mutation_rows(fixtures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for fixture in fixtures:
        for candidate in fixture["candidates"]:
            local = _special_local_input(candidate, fixture["mutation"]["kind"])
            local_result = "accept"
            local_cause = None
            local_code = None
            try:
                validate_local(candidate, local)
            except ContractError as exc:
                local_result = "reject"
                local_cause, local_code = exc.cause, exc.code
            if local_result == "reject":
                gate_result = "not_run"
                gate_cause = None
                gate_code = None
                final = "safe_reject"
            else:
                gate = common_backend_gate(fixture.get("proposal", {}), fixture.get("gateContext", {}))
                gate_result = "accept" if gate.accepted else "reject"
                gate_cause, gate_code = gate.cause, gate.code
                final = "unsafe_accept" if gate.accepted else "safe_reject"
            expected_local = fixture["expectedLocalResult"][candidate]
            expected_gate = fixture["expectedCommonGateResult"][candidate]
            if local_result != expected_local or gate_result != expected_gate:
                raise AssertionError(
                    f"{fixture['fixtureId']}:{candidate} expected {expected_local}/{expected_gate}, got {local_result}/{gate_result} ({local_code}/{gate_code})"
                )
            rows.append(
                {
                    "recordType": "mutation",
                    "version": RESULT_VERSION,
                    "fixtureId": fixture["fixtureId"],
                    "candidate": candidate,
                    "sourceCaseId": fixture["sourceCaseId"],
                    "localValidation": local_result,
                    "localCause": local_cause,
                    "localCode": local_code,
                    "commonGate": gate_result,
                    "commonCause": gate_cause,
                    "commonCode": gate_code,
                    "finalOutcome": final,
                    "unsafeAccept": final == "unsafe_accept",
                    "safetyConditions": fixture["safetyConditions"],
                }
            )
    return rows


def valid_minimum_rows() -> list[dict[str, Any]]:
    rows = []
    for candidate in ("A", "B", "C", "R"):
        validate_local(candidate, minimal_inputs(candidate))
        rows.append({"recordType":"valid_minimum","version":RESULT_VERSION,"candidate":candidate,"localValidation":"accept","commonGate":"not_applicable","finalOutcome":"correct"})
    return rows


def run_validation() -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    integrity = assert_locked_integrity()
    cases = load_jsonl(LOCKED_PATH)
    fixtures = load_jsonl(FIXTURE_PATH)
    fixture_ids = [fixture["fixtureId"] for fixture in fixtures]
    if len(fixture_ids) != len(set(fixture_ids)):
        raise AssertionError("duplicate fixture ID")
    representation = representation_rows(cases)
    # Validate the derived C output for every locked case, including all exact spans.
    for case in cases:
        turns = {turn["turn"]: turn["text"] for turn in case["context"]["turns"] if turn["inputType"] == "text"}
        validate_candidate_c(json.dumps(candidate_c_from_case(case), ensure_ascii=False), user_turns=turns)
    mutations = mutation_rows(fixtures)
    if any(row["unsafeAccept"] for row in mutations):
        raise AssertionError("unsafe mutation accepted")
    rows = valid_minimum_rows() + representation + mutations
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in representation:
        counts[row["candidate"]][row["classification"]] += 1
    summary = {
        "version": RESULT_VERSION,
        "status": "PROPOSED",
        "lockedIntegrity": integrity,
        "representability": {candidate: dict(counts[candidate]) for candidate in ("A", "B", "C", "R")},
        "mutationFixtureCount": len(fixtures),
        "mutationCandidateExecutions": len(mutations),
        "localValidation": dict(Counter(row["localValidation"] for row in mutations)),
        "commonGate": dict(Counter(row["commonGate"] for row in mutations)),
        "finalUnsafeAccept": sum(row["unsafeAccept"] for row in mutations),
        "candidateCSpanCasesValidated": len(cases),
        "modelCalls": 0,
        "serverCalls": 0,
        "databaseCalls": 0,
    }
    manifest = {
        "manifestVersion": RESULT_VERSION,
        "status": "PROPOSED",
        "python": sys.version.split()[0],
        "stdlibOnly": True,
        "externalValidatorInstalledOrUsed": False,
        "contracts": {**CONTRACT_SPECS, "R": RULE_BASELINE_SPEC},
        "inputs": {
            "lockedCases": {"path": str(LOCKED_PATH.relative_to(HERE.parent.parent.parent)), "sha256": integrity["lockedSha256"], "count": 56},
            "semanticOracleSchema": {"path": str(SCHEMA_PATH.relative_to(HERE.parent.parent.parent)), "sha256": integrity["schemaSha256"]},
            "mutationFixtures": {"path": ".omx/research/issue30/mutation-fixtures-v0.1.jsonl", "sha256": sha256(FIXTURE_PATH), "count": len(fixtures)},
        },
        "outputs": {
            "results": ".omx/research/issue30/contract-validation-results-v0.1.jsonl",
            "summary": ".omx/research/issue30/contract-validation-summary-v0.1.md",
        },
        "resultCounts": summary,
        "classificationAxes": {
            "causes": ["FORMAT","MODEL_SEMANTIC","NORMALIZER","MAPPING","ORACLE_ERROR","CONTRACT_GAP"],
            "outcomes": ["correct","safe_reject","over_ask","unsafe_accept"],
            "transportExcluded": True,
        },
        "notExecuted": ["Ollama", "MLX", "Qwen", "model transport", "backend server", "Schedule DB", "Schedule API", "production write"],
        "limitations": [
            "single-review proposed",
            "candidate B v0.1 is single-intent and at most two calls; ordered multi-operation is not invented",
            "rule baseline R v0.1 has exactly one intent; unsupported grammar fails closed",
            "locked semantic schema is hash-checked but not revalidated with a third-party JSON Schema package",
        ],
    }
    return rows, summary, manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="print machine-readable summary")
    args = parser.parse_args()
    _rows, summary, _manifest = run_validation()
    print(json.dumps(summary, ensure_ascii=False, indent=None if args.json else 2, sort_keys=True))


if __name__ == "__main__":
    main()
