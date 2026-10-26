"""Ollama evaluator using Issue #30 common backend safety gate v0.2.

The v0.1 evaluator, prompts, candidate schemas, context renderers, oracle and
dry mapping stay immutable.  This module reuses them and replaces only the
common-gate call for candidate C with deterministic evidence-role validation.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from unittest.mock import patch

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import common_gate_v0_2 as gate_v2  # noqa: E402
import contracts  # noqa: E402
import dry_mapping  # noqa: E402
import ollama_eval as v1  # noqa: E402

EVAL_VERSION = "issue30-ollama-eval-v0.2"
PROMPT_REVISIONS = {"A": 0, "B": 2, "C": 2}
EXPECTED_FROZEN_HASHES = {
    "A": {
        "prompt": "a26e06a28830e6f65262856a19d1289cef2977319dae22cbaf5d3f31faf66f70",
        "schemaBuilder": "2bb1ef0fd4e12d8f17665478fb1bd0afc360a98ae78c6177f5144728c86ebc39",
        "contextRenderer": "eab78c729c59c6b6055f1e2f31928e70060946ad0cb1e89814a82e1761b03027",
    },
    "B": {
        "prompt": "8833c4e1e1ace53a5293781437e6094ece4cc15d9c014a16869aa7a9d3d02d8d",
        "schemaBuilder": "3068ce0f12b70353bb72c5c9917dfbbdb9d5de139561e15c7d5dd92970a6d897",
        "contextRenderer": "c158f3e231d046a34a0d061bdfac3e64041516ddeddd9441b7df0bfd330d8337",
    },
    "C": {
        "prompt": "6352d687dd9093f783ff1c23e95d6f84433af30c3776a524aaa980a83db5f24b",
        "schemaBuilder": "e9553c727d593a6c3125306f922b500fa6fdd093ff80e03281bbd32033617e9c",
        "contextRenderer": "c158f3e231d046a34a0d061bdfac3e64041516ddeddd9441b7df0bfd330d8337",
    },
}


def frozen_hashes(candidates: Sequence[str]) -> dict[str, dict[str, str]]:
    observed = {
        candidate: {
            "prompt": v1.prompt_hash(candidate),
            "schemaBuilder": v1.schema_builder_hash(candidate),
            "contextRenderer": v1.context_renderer_hash(candidate),
        }
        for candidate in candidates
    }
    expected = {candidate: EXPECTED_FROZEN_HASHES[candidate] for candidate in candidates}
    if observed != expected:
        raise RuntimeError(f"v0.1 prompt/schema/context hash changed: {observed}")
    return observed


def _trusted_gate_v0_2(
    actual: dry_mapping.OperationView | None,
    expected: dry_mapping.OperationView | None,
    case: Mapping[str, Any],
    *,
    candidate: str,
    candidate_c_output: Mapping[str, Any] | None = None,
    through_turn: int | None = None,
) -> contracts.GateResult:
    if actual is None:
        if candidate_c_output is None:
            return contracts.GateResult(True, None, None)
        return gate_v2.validate_candidate_c_role_semantics(
            candidate_c_output,
            user_turns=v1._visible_text_turns(case, through_turn or 0),
            reference_time=case["context"]["referenceTime"],
        )
    reader = dry_mapping.FakeScheduleReadPort(case["context"]["initialSchedules"])
    actual_candidates = dry_mapping._target_candidates(actual, reader)
    proposal, context = dry_mapping._proposal_and_context(
        actual,
        reader,
        actual_candidates,
        dry_mapping._parse_datetime(case["context"]["referenceTime"]),
    )
    context["serverTimeTrusted"] = bool(case["context"]["serverTimeTrusted"])
    if actual.kind in {"update", "delete"} and actual.target_title is not None:
        proposal["title"] = actual.target_title
    if candidate in {"A", "B"} and expected is not None:
        trusted_candidates = dry_mapping._target_candidates(expected, reader)
        _, trusted_context = dry_mapping._proposal_and_context(
            expected,
            reader,
            trusted_candidates,
            dry_mapping._parse_datetime(case["context"]["referenceTime"]),
        )
        context["grounded"] = trusted_context["grounded"]
        if expected.target_title is not None:
            context["grounded"]["title"] = [expected.target_title]
    return gate_v2.common_backend_gate_v0_2(
        proposal,
        context,
        candidate_c_output=candidate_c_output,
        user_turns=(
            None
            if candidate_c_output is None
            else v1._visible_text_turns(case, through_turn or 0)
        ),
        reference_time=case["context"]["referenceTime"],
        server_time_trusted=bool(case["context"]["serverTimeTrusted"]),
    )


def evaluate_candidate_a(
    case: Mapping[str, Any], client: v1.OllamaClient
) -> tuple[list[v1.CallRecord], dict[str, Any]]:
    with patch.object(v1, "_trusted_gate", _trusted_gate_v0_2):
        calls, details = v1.evaluate_candidate_a(case, client)
    details["evidenceRoleValidation"] = None
    details["evidenceRoleCodes"] = []
    return calls, details


def evaluate_candidate_b(
    case: Mapping[str, Any], client: v1.OllamaClient
) -> tuple[list[v1.CallRecord], dict[str, Any]]:
    with patch.object(v1, "_trusted_gate", _trusted_gate_v0_2):
        calls, details = v1.evaluate_candidate_b(case, client)
    details["evidenceRoleValidation"] = None
    details["evidenceRoleCodes"] = []
    return calls, details


def evaluate_candidate_c(
    case: Mapping[str, Any], client: v1.OllamaClient
) -> tuple[list[v1.CallRecord], dict[str, Any]]:
    calls: list[v1.CallRecord] = []
    semantic_results: list[bool] = []
    intent_results: list[bool] = []
    span_results: list[bool] = []
    role_results: list[bool] = []
    correction_results: list[bool] = []
    gates: list[contracts.GateResult] = []
    role_gates: list[contracts.GateResult] = []
    local_accept = True
    parse_accept = True
    effect_complete = False
    final_kind: str | None = None
    for turn in v1._model_text_turns(case):
        context = v1.render_extraction_context(case, turn["turn"], stage="EVIDENCE")
        call = client.chat(
            [
                {"role": "system", "content": v1.prompt_text("C")},
                {"role": "user", "content": context},
            ],
            v1.candidate_c_schema(),
        )
        calls.append(call)
        if call.raw is None:
            local_accept = False
            parse_accept = False
            continue
        try:
            contracts.strict_json_loads(call.raw)
        except contracts.ContractError:
            parse_accept = False
        try:
            parsed = contracts.validate_candidate_c(
                call.raw, user_turns=v1._visible_text_turns(case, turn["turn"])
            )
        except (contracts.ContractError, KeyError, TypeError, ValueError):
            local_accept = False
            continue
        expected = contracts.validate_candidate_c(
            v1.canonical_json(v1._expected_c(case, turn["turn"])),
            user_turns=v1._visible_text_turns(case, turn["turn"]),
        )
        actual_kinds = [operation["kind"] for operation in parsed["operations"]]
        expected_kinds = [operation["kind"] for operation in expected["operations"]]
        intent_results.append(actual_kinds == expected_kinds)
        span_results.append(v1._span_signature(parsed) == v1._span_signature(expected))
        role_results.append(v1._role_signature(parsed) == v1._role_signature(expected))
        actual_corrections = {item for item in v1._span_signature(parsed) if item[2] != "adopted"}
        expected_corrections = {item for item in v1._span_signature(expected) if item[2] != "adopted"}
        correction_results.append(actual_corrections == expected_corrections)
        actual_views = v1._views_from_c(parsed, case)
        expected_views = v1._views_from_c(expected, case)
        semantic_results.append(
            len(actual_views) == len(expected_views)
            and all(
                v1._views_equal(actual_view, expected_view)
                for actual_view, expected_view in zip(actual_views, expected_views)
            )
        )
        expected_view = expected_views[0] if len(expected_views) == 1 else None
        actual_view = actual_views[0] if len(actual_views) == 1 else None
        if actual_view is not None:
            final_kind = actual_view.kind
        role_gate = gate_v2.validate_candidate_c_role_semantics(
            parsed,
            user_turns=v1._visible_text_turns(case, turn["turn"]),
            reference_time=case["context"]["referenceTime"],
        )
        role_gates.append(role_gate)
        gate = (
            role_gate
            if not role_gate.accepted
            else _trusted_gate_v0_2(
                actual_view,
                expected_view,
                case,
                candidate="C",
                candidate_c_output=parsed,
                through_turn=turn["turn"],
            )
        )
        gates.append(gate)
        effect_complete = v1._view_effect_complete(actual_view, case)
    return calls, {
        "jsonParseSuccess": parse_accept and bool(calls),
        "localValidation": "accept" if local_accept and bool(calls) else "reject",
        "semanticMatch": bool(semantic_results) and all(semantic_results),
        "commonGate": "reject" if any(not gate.accepted for gate in gates) else ("accept" if gates or calls else "not_run"),
        "gateCodes": [gate.code for gate in gates if not gate.accepted],
        "intentMatch": bool(intent_results) and all(intent_results),
        "effectComplete": effect_complete,
        "finalAction": final_kind,
        "groundless": {key: 0 for key in ("datetime", "title", "category", "filter", "changes")},
        "spanExact": bool(span_results) and all(span_results),
        "roleExact": bool(role_results) and all(role_results),
        "correctionExact": bool(correction_results) and all(correction_results),
        "arbitrarySelection": False,
        "evidenceRoleValidation": "reject" if any(not gate.accepted for gate in role_gates) else ("accept" if role_gates else "not_run"),
        "evidenceRoleCodes": [gate.code for gate in role_gates if not gate.accepted],
    }


def evaluate_case(
    case: Mapping[str, Any],
    candidate: str,
    client: v1.OllamaClient,
    *,
    run_id: str,
    phase: str,
    model_digest: str,
    dry_rows: Mapping[tuple[str, str], Mapping[str, Any]],
) -> dict[str, Any]:
    evaluator = {"A": evaluate_candidate_a, "B": evaluate_candidate_b, "C": evaluate_candidate_c}[candidate]
    calls, details = evaluator(case, client)
    case_id = case["meta"]["caseId"]
    dry_row = dry_rows.get((case_id, candidate), {
        "mappingResult": "development_not_scored",
        "systemOutcome": "safe_reject",
        "mismatchCause": None,
        "contractClassification": "development_probe",
    })
    no_model_call_expected = not v1._model_text_turns(case)
    transport_success = all(call.transport_success for call in calls)
    parse_success = bool(details["jsonParseSuccess"]) or no_model_call_expected
    local = "not_run" if no_model_call_expected else str(details["localValidation"])
    semantic = True if no_model_call_expected else bool(details["semanticMatch"])
    gate = "not_run" if no_model_call_expected else str(details["commonGate"])
    expected_final_kind = case["semantics"]["expectedTurns"][-1]["expectedResult"]["kind"]
    outcome = (
        str(dry_row["systemOutcome"])
        if no_model_call_expected
        else v1._final_outcome(
            transport=transport_success,
            parse=parse_success,
            local=local,
            semantic=semantic,
            gate=gate,
            effect_complete=bool(details["effectComplete"]),
            expected_final_kind=expected_final_kind,
            dry_row=dry_row,
        )
    )
    raw_hash, raw_bytes = v1._raw_metadata(calls)
    primary = v1._primary_cause(transport_success, parse_success, local, semantic, gate, dry_row)
    secondary: list[str] = []
    if not details["intentMatch"]:
        secondary.append("intent_mismatch")
    secondary.extend(str(code) for code in details["gateCodes"])
    if dry_row.get("contractClassification") == "structurally_unrepresentable":
        secondary.append("structurally_unrepresentable")
    return {
        "recordType": "ollama_model_eval",
        "version": EVAL_VERSION,
        "phase": phase,
        "runId": run_id,
        "promptRevision": PROMPT_REVISIONS[candidate],
        "caseId": case_id,
        "candidate": candidate,
        "model": v1.MODEL,
        "modelDigest": model_digest,
        "promptHash": v1.prompt_hash(candidate),
        "schemaHash": v1.aggregate_hash([call.schema_hash for call in calls]),
        "schemaBuilderHash": v1.schema_builder_hash(candidate),
        "contextRendererHash": v1.context_renderer_hash(candidate),
        "commonGateVersion": gate_v2.GATE_VERSION,
        "modelCallCount": len(calls),
        "httpStatus": 200 if calls and all(call.http_status == 200 for call in calls) else next((call.http_status for call in reversed(calls) if call.http_status is not None), None),
        "latencyMs": round(sum(call.latency_ms for call in calls), 3),
        "transportSuccess": transport_success,
        "jsonParseSuccess": parse_success,
        "localValidation": local,
        "semanticMatch": semantic,
        "evidenceRoleValidation": details["evidenceRoleValidation"],
        "evidenceRoleCodes": details["evidenceRoleCodes"],
        "commonGate": gate,
        "dryMappingResult": dry_row.get("mappingResult"),
        "contractClassification": dry_row.get("contractClassification"),
        "primaryCause": primary,
        "secondaryCauses": list(dict.fromkeys(secondary)),
        "finalOutcome": outcome,
        "safeReject": outcome == "safe_reject",
        "unsafeAccept": outcome == "unsafe_accept",
        "rawOutputSha256": raw_hash,
        "rawOutputByteLength": raw_bytes,
        "intentMatch": True if no_model_call_expected else bool(details["intentMatch"]),
        "operation": details["finalAction"],
        "groundlessValueCounts": details["groundless"],
        "arbitraryCandidateSelection": bool(details["arbitrarySelection"]),
        "spanExact": details["spanExact"],
        "roleExact": details["roleExact"],
        "correctionExact": details["correctionExact"],
        "transportRetryCount": sum(call.retries for call in calls),
    }


def run_phase(
    *,
    phase: str,
    run_id: str,
    endpoint: str,
    model_digest: str,
    candidates: Sequence[str],
    stop_on_unsafe: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    v1.assert_protected_integrity()
    cases = v1.load_jsonl(v1.DEVELOPMENT_PATH if phase == "development" else v1.LOCKED_PATH)
    client = v1.OllamaClient(endpoint)
    dry_rows = v1._dry_rows()
    rows: list[dict[str, Any]] = []
    started = datetime.now(timezone.utc)
    frozen = frozen_hashes(candidates)
    aborted_unsafe: dict[str, str] | None = None
    for candidate in candidates:
        for case in cases:
            row = evaluate_case(
                case,
                candidate,
                client,
                run_id=run_id,
                phase=phase,
                model_digest=model_digest,
                dry_rows=dry_rows,
            )
            rows.append(row)
            if stop_on_unsafe and row["unsafeAccept"]:
                aborted_unsafe = {"caseId": str(row["caseId"]), "candidate": candidate}
                break
        if aborted_unsafe is not None:
            break
    if frozen != frozen_hashes(candidates):
        raise RuntimeError("prompt/schema/context renderer changed during run")
    ended = datetime.now(timezone.utc)
    return rows, {
        "runId": run_id,
        "phase": phase,
        "startedAt": started.isoformat(),
        "endedAt": ended.isoformat(),
        "durationSeconds": round((ended - started).total_seconds(), 3),
        "retryCount": client.retry_count,
        "frozenHashes": frozen,
        "commonGateVersion": gate_v2.GATE_VERSION,
        "abortedUnsafe": aborted_unsafe,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    environment = sub.add_parser("environment")
    environment.add_argument("--endpoint", default=v1.ENDPOINT)
    run = sub.add_parser("run")
    run.add_argument("--phase", choices=["development", "locked"], required=True)
    run.add_argument("--run-id", required=True)
    run.add_argument("--model-digest", required=True)
    run.add_argument("--candidates", default="A,B,C")
    run.add_argument("--endpoint", default=v1.ENDPOINT)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--meta-output", type=Path, required=True)
    run.add_argument("--stop-on-unsafe", action="store_true")
    args = parser.parse_args()
    if args.command == "environment":
        print(v1.canonical_json(v1.ollama_environment(v1.OllamaClient(args.endpoint))))
        return
    candidates = tuple(item for item in args.candidates.split(",") if item)
    rows, metadata = run_phase(
        phase=args.phase,
        run_id=args.run_id,
        endpoint=args.endpoint,
        model_digest=args.model_digest,
        candidates=candidates,
        stop_on_unsafe=args.stop_on_unsafe,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    v1._write_jsonl(args.output, rows)
    aggregate = v1.aggregate_rows(rows)
    args.meta_output.write_text(
        v1.canonical_json({**metadata, "aggregate": aggregate}) + "\n",
        encoding="utf-8",
    )
    print(v1.canonical_json({**metadata, "aggregate": aggregate}))


if __name__ == "__main__":
    main()
