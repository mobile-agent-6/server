"""Ollama evaluator using Issue #30 common backend safety gate v0.3.

Prompts, candidate schemas, context renderers, the DATA v2.6 candidate-A
controller, the semantic oracle and dry mapping stay identical to v0.1/v0.2.
Only the common-gate call changes: a candidate-neutral trusted policy context
(unsupported feature + consent, D5) is derived from backend conversation state
and enforced before the unchanged v0.2 gate for all three candidates.
"""

from __future__ import annotations

import argparse
import contextvars
import hashlib
import inspect
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

import common_gate_v0_3 as gate_v3  # noqa: E402
import contracts  # noqa: E402
import dry_mapping  # noqa: E402
import ollama_eval as v1  # noqa: E402
import ollama_eval_v0_2 as v2  # noqa: E402

EVAL_VERSION = "issue30-ollama-eval-v0.3"
PROMPT_REVISIONS = dict(v2.PROMPT_REVISIONS)
EXPECTED_FROZEN_HASHES = v2.EXPECTED_FROZEN_HASHES
V02_PROTECTED = {
    "common_gate_v0_2.py": "82252c72f8c20bb4103b427e65c272d878a8b885393fbfbc987cb63519470f7a",
    "test_common_gate_v0_2.py": "f3d41817c4270e6a71f3c177ba29f24f03674fb8d63f4e0beba1fd48a592e4a3",
    "validate_contracts_v0_2.py": "a1ad50d77f8d655c75a105ce7aac9b7405e57ac6cc145184f43263faf35c8932",
    "mutation-fixtures-v0.2.jsonl": "542106cf47a965cb0807e38a76832e9e6b7a61a842571763762288d53b6b8ada",
    "contract-validation-results-v0.2.jsonl": "57462b983492b7e6a35666aab821d15d3c7ec1bc59166ab6618840ff4adf4ec1",
    "contract-validation-summary-v0.2.md": "bf3f76ba51f45c029b4a786fb5f216d535e8de9ad744b4e95dbffb15a31e9414",
}

_CURRENT_TURN: contextvars.ContextVar[int | None] = contextvars.ContextVar("issue30_v03_turn", default=None)
_POLICY_LOG: contextvars.ContextVar[list[dict[str, Any]] | None] = contextvars.ContextVar("issue30_v03_policy_log", default=None)
_ORIGINAL_EXPECTED_VIEW = v1._expected_view
_ORIGINAL_A_PROPOSAL_VIEW = v1._a_proposal_view
_ORIGINAL_V02_GATE = v2._trusted_gate_v0_2


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def gate_hashes() -> dict[str, str]:
    analyzer_source = "".join(
        inspect.getsource(item)
        for item in (
            gate_v3.analyze_text,
            gate_v3.classify_consent_answer,
            gate_v3.analyze_policy,
            gate_v3._has_verb_token,
        )
    )
    return {
        "commonGateV03FileSha256": sha256_file(HERE / "common_gate_v0_3.py"),
        "policyAnalyzerSourceSha256": hashlib.sha256(analyzer_source.encode("utf-8")).hexdigest(),
        "policyGateSourceSha256": hashlib.sha256(
            (inspect.getsource(gate_v3.policy_gate) + inspect.getsource(gate_v3.validate_policy_context)).encode("utf-8")
        ).hexdigest(),
        "commonGateV02FileSha256": sha256_file(HERE / "common_gate_v0_2.py"),
        "evaluatorAdapterSha256": hashlib.sha256(
            (inspect.getsource(_trusted_gate_v0_3) + inspect.getsource(_a_proposal_view_v0_3) + inspect.getsource(executable_effect)).encode("utf-8")
        ).hexdigest(),
    }


def assert_v02_protected() -> dict[str, str]:
    observed = {name: sha256_file(HERE / name) for name in V02_PROTECTED}
    if observed != V02_PROTECTED:
        raise RuntimeError(f"v0.2 protected artifact changed: {observed}")
    return observed


def _recording_expected_view(case: Mapping[str, Any], through_turn: int) -> dry_mapping.OperationView | None:
    # The evaluation loops call this immediately before the gate for each model
    # text turn; the gate needs the turn to read backend conversation state.
    _CURRENT_TURN.set(int(through_turn))
    return _ORIGINAL_EXPECTED_VIEW(case, through_turn)


def _a_proposal_view_v0_3(action: Mapping[str, Any]) -> dry_mapping.OperationView | None:
    """Candidate-A SELECT_TARGET advances update/delete; expose it to the policy gate."""

    if action.get("action") == "SELECT_TARGET":
        return dry_mapping.OperationView(kind="select_target")
    return _ORIGINAL_A_PROPOSAL_VIEW(action)


def executable_effect(view: dry_mapping.OperationView | None) -> bool:
    """Whether a proposal could advance to a backend effect at this turn.

    Complete create (pending create), bounded list (read), update/delete and
    candidate selection (next step toward a write) are effects.  A model-marked
    missing or unsupported operation or an incomplete create/list maps to a
    question, so it is not an effect; such marks can only stop execution.
    """

    if view is None or view.missing or view.unsupported:
        return False
    if view.kind == "create":
        return view.title is not None and view.scheduled_at is not None and view.category is not None
    if view.kind == "list":
        return view.query_from is not None and view.query_to is not None
    return view.kind in {"update", "delete", "select_target"}


def _log(entry: Mapping[str, Any]) -> None:
    log = _POLICY_LOG.get()
    if log is not None:
        log.append(dict(entry))


def _trusted_gate_v0_3(
    actual: dry_mapping.OperationView | None,
    expected: dry_mapping.OperationView | None,
    case: Mapping[str, Any],
    *,
    candidate: str,
    candidate_c_output: Mapping[str, Any] | None = None,
    through_turn: int | None = None,
) -> contracts.GateResult:
    turn = through_turn if through_turn is not None else _CURRENT_TURN.get()
    trusted_turns = case["context"]["turns"]
    executable = executable_effect(actual)
    policy = gate_v3.analyze_policy(trusted_turns, turn) if turn is not None else None
    policy_result = gate_v3.policy_gate(
        None if actual is None else {"operation": actual.kind},
        policy,
        turns=trusted_turns,
        through_turn=turn,
        executable=executable,
    )
    _log({
        "turn": turn,
        "executable": executable,
        "consentState": None if policy is None else policy.consentState,
        "unsupportedOnly": None if policy is None else policy.unsupportedOnly,
        "unknownCue": None if policy is None else bool(policy.unknownCueExpressions),
        "policyGate": "accept" if policy_result.accepted else "reject",
        "policyCode": policy_result.code,
    })
    if not policy_result.accepted:
        return policy_result
    if actual is not None and actual.kind == "select_target":
        return contracts.GateResult(True, None, None)
    return _ORIGINAL_V02_GATE(
        actual,
        expected,
        case,
        candidate=candidate,
        candidate_c_output=candidate_c_output,
        through_turn=turn,
    )


def _with_policy_log(run):
    token = _POLICY_LOG.set([])
    turn_token = _CURRENT_TURN.set(None)
    try:
        calls, details = run()
        details["policyLog"] = list(_POLICY_LOG.get() or [])
        return calls, details
    finally:
        _POLICY_LOG.reset(token)
        _CURRENT_TURN.reset(turn_token)


def evaluate_candidate_a(case: Mapping[str, Any], client: v1.OllamaClient) -> tuple[list[v1.CallRecord], dict[str, Any]]:
    def run():
        with patch.object(v1, "_trusted_gate", _trusted_gate_v0_3), \
             patch.object(v1, "_expected_view", _recording_expected_view), \
             patch.object(v1, "_a_proposal_view", _a_proposal_view_v0_3):
            calls, details = v1.evaluate_candidate_a(case, client)
        details["evidenceRoleValidation"] = None
        details["evidenceRoleCodes"] = []
        return calls, details
    return _with_policy_log(run)


def evaluate_candidate_b(case: Mapping[str, Any], client: v1.OllamaClient) -> tuple[list[v1.CallRecord], dict[str, Any]]:
    def run():
        with patch.object(v1, "_trusted_gate", _trusted_gate_v0_3), \
             patch.object(v1, "_expected_view", _recording_expected_view):
            calls, details = v1.evaluate_candidate_b(case, client)
        details["evidenceRoleValidation"] = None
        details["evidenceRoleCodes"] = []
        return calls, details
    return _with_policy_log(run)


def evaluate_candidate_c(case: Mapping[str, Any], client: v1.OllamaClient) -> tuple[list[v1.CallRecord], dict[str, Any]]:
    def run():
        with patch.object(v2, "_trusted_gate_v0_2", _trusted_gate_v0_3):
            return v2.evaluate_candidate_c(case, client)
    return _with_policy_log(run)


POLICY_CODES = {
    "POLICY_CONTEXT_MISSING", "POLICY_CONTEXT_INVALID", "POLICY_CONTEXT_TAMPERED", "POLICY_INPUTS_MISSING",
    "POLICY_INPUTS_INVALID", "CANDIDATE_POLICY_CLAIM", "UNSUPPORTED_EXPRESSION_UNKNOWN",
    "UNSUPPORTED_ONLY_NO_SCHEDULE_EFFECT", "CONSENT_REQUIRED_BEFORE_EFFECT", "CONSENT_DECLINED",
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
    policy_log = details.get("policyLog", [])
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
        "commonGateVersion": gate_v3.GATE_VERSION,
        "policyAnalyzerVersion": gate_v3.POLICY_ANALYZER_VERSION,
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
        "policyGateTurns": policy_log,
        "policyGateCodes": [code for code in details["gateCodes"] if code in POLICY_CODES],
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
    assert_v02_protected()
    cases = v1.load_jsonl(v1.DEVELOPMENT_PATH if phase == "development" else v1.LOCKED_PATH)
    client = v1.OllamaClient(endpoint)
    dry_rows = v1._dry_rows()
    rows: list[dict[str, Any]] = []
    started = datetime.now(timezone.utc)
    frozen = v2.frozen_hashes(candidates)
    gates = gate_hashes()
    aborted_unsafe: dict[str, str] | None = None
    for candidate in candidates:
        for case in cases:
            row = evaluate_case(case, candidate, client, run_id=run_id, phase=phase, model_digest=model_digest, dry_rows=dry_rows)
            rows.append(row)
            if stop_on_unsafe and row["unsafeAccept"]:
                aborted_unsafe = {"caseId": str(row["caseId"]), "candidate": candidate}
                break
        if aborted_unsafe is not None:
            break
    if frozen != v2.frozen_hashes(candidates):
        raise RuntimeError("prompt/schema/context renderer changed during run")
    if gates != gate_hashes():
        raise RuntimeError("common gate or policy analyzer changed during run")
    ended = datetime.now(timezone.utc)
    return rows, {
        "runId": run_id,
        "phase": phase,
        "startedAt": started.isoformat(),
        "endedAt": ended.isoformat(),
        "durationSeconds": round((ended - started).total_seconds(), 3),
        "retryCount": client.retry_count,
        "frozenHashes": frozen,
        "gateHashes": gates,
        "commonGateVersion": gate_v3.GATE_VERSION,
        "policyAnalyzerVersion": gate_v3.POLICY_ANALYZER_VERSION,
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
    args.meta_output.write_text(v1.canonical_json({**metadata, "aggregate": aggregate}) + "\n", encoding="utf-8")
    print(v1.canonical_json({**metadata, "aggregate": aggregate}))


if __name__ == "__main__":
    main()
