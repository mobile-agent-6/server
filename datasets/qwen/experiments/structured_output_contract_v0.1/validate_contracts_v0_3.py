"""Deterministic validation runner for Issue #30 common gate v0.3 (trusted policy / D5 consent).

No model, server, database or network call is made.  Fixture cases are given to
the gate with their input ``context`` only (no ``semantics``/``meta``), so any
oracle dependency in the gate path would fail loudly.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import common_gate_v0_3 as gate_v3  # noqa: E402
import contracts  # noqa: E402
import dry_mapping  # noqa: E402
import ollama_eval as v1  # noqa: E402
import ollama_eval_v0_2 as v2  # noqa: E402
import ollama_eval_v0_3 as v3  # noqa: E402
import validate_contracts_v0_2 as v02_validation  # noqa: E402

VERSION = "issue30-contract-validation-v0.3"
FIXTURES = HERE / "mutation-fixtures-v0.3.jsonl"
DEVELOPMENT = HERE / "development-cases-v0.1.jsonl"
LOCKED = HERE / "locked-cases-v0.1.jsonl"
EFFECT_KINDS = {"candidate_selection", "confirmation"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _case(context: Mapping[str, Any]) -> dict[str, Any]:
    return {"context": copy.deepcopy(dict(context))}


def _local(fixture: Mapping[str, Any], case: Mapping[str, Any]) -> tuple[str, str | None, Any, dry_mapping.OperationView | None]:
    candidate = fixture["candidate"]
    through = int(fixture["throughTurn"])
    output = fixture["candidateOutput"]
    turns = v1._visible_text_turns(case, through)
    try:
        if candidate == "A":
            users = [turns[key] for key in sorted(turns)]
            now = dry_mapping._parse_datetime(case["context"]["referenceTime"])
            context = v1.build_a_context(users, now, lookup_completed=False, candidates=[])
            parsed = contracts.validate_candidate_a(output["raw"], context, users=users, now=now)
            return "accept", None, parsed, v3._a_proposal_view_v0_3(parsed)
        if candidate == "B":
            parsed = contracts.validate_candidate_b(output["stage1"], output["stage2"])
            return "accept", None, parsed, v1._view_from_b(parsed)
        parsed = contracts.validate_candidate_c(json.dumps(output, ensure_ascii=False), user_turns=turns)
        views = v1._views_from_c(parsed, case)
        return "accept", None, parsed, views[0] if len(views) == 1 else None
    except contracts.ContractError as exc:
        return "reject", exc.code, None, None


def _v02(view, case, candidate, parsed, through) -> contracts.GateResult:
    if view is not None and view.kind == "select_target":
        return contracts.GateResult(True, None, None)
    return v3._ORIGINAL_V02_GATE(view, None, case, candidate=candidate,
                                 candidate_c_output=parsed if candidate == "C" else None, through_turn=through)


def _v03_with_override(view, case, candidate, parsed, through, override) -> contracts.GateResult:
    turns = case["context"]["turns"]
    policy = gate_v3.analyze_policy(turns, through)
    executable = v3.executable_effect(view)
    if override["mode"] == "missing":
        policy_value: Any = None
    else:
        policy_value = policy.to_dict()
        policy_value.update(override.get("set", {}) if override["mode"] in {"forge", "invalid"} else {})
        if override["mode"] == "forge":
            policy_value["fingerprint"] = gate_v3.fingerprint(policy_value)
    reader = dry_mapping.FakeScheduleReadPort(case["context"]["initialSchedules"])
    candidates = dry_mapping._target_candidates(view, reader) if view is not None else []
    proposal, context = dry_mapping._proposal_and_context(view, reader, candidates, dry_mapping._parse_datetime(case["context"]["referenceTime"])) if view is not None else ({}, {})
    if override["mode"] == "proposal_claim":
        proposal.update(override["set"])
    context["serverTimeTrusted"] = bool(case["context"]["serverTimeTrusted"])
    return gate_v3.common_backend_gate_v0_3(
        proposal, context, policy=policy_value, policy_turns=turns, through_turn=through, executable=executable,
        candidate_c_output=parsed if candidate == "C" else None,
        user_turns=v1._visible_text_turns(case, through) if candidate == "C" else None,
        reference_time=case["context"]["referenceTime"], server_time_trusted=bool(case["context"]["serverTimeTrusted"]),
    )


def fixture_rows() -> list[dict[str, Any]]:
    rows = []
    for fixture in load_jsonl(FIXTURES):
        case = _case(fixture["context"])
        through = int(fixture["throughTurn"])
        candidate = fixture["candidate"]
        local, local_code, parsed, view = _local(fixture, case)
        if local != "accept":
            v02_result = v03_result = contracts.GateResult(False, "FORMAT", "LOCAL_REJECT")
            v02_label = v03_label = "not_run"
        else:
            v02_result = _v02(view, case, candidate, parsed, through)
            v02_label = "accept" if v02_result.accepted else "reject"
            if fixture.get("policyOverride"):
                v03_result = _v03_with_override(view, case, candidate, parsed, through, fixture["policyOverride"])
            else:
                v03_result = v3._trusted_gate_v0_3(view, None, case, candidate=candidate,
                                                   candidate_c_output=parsed if candidate == "C" else None, through_turn=through)
            v03_label = "accept" if v03_result.accepted else "reject"
        if fixture["fixtureKind"] == "mutation":
            final = "unsafe_accept" if local == "accept" and v03_result.accepted else "safe_reject"
        else:
            final = "correct" if local == "accept" and v03_result.accepted else "over_reject"
        policy = gate_v3.analyze_policy(case["context"]["turns"], through)
        actual = {"localValidation": local, "v02Gate": v02_label, "v03Gate": v03_label, "v03Code": v03_result.code if not v03_result.accepted else None, "finalOutcome": final}
        if actual != fixture["expected"]:
            raise AssertionError(f"{fixture['fixtureId']}: expected {fixture['expected']}, got {actual} (local code {local_code}, v02 {v02_result.code})")
        rows.append({
            "recordType": "fixture", "version": VERSION, "fixtureId": fixture["fixtureId"], "fixtureKind": fixture["fixtureKind"],
            "candidate": candidate, "sourceCaseId": fixture["sourceCaseId"], "throughTurn": through,
            "localValidation": local, "localCode": local_code,
            "commonGateV02": v02_label, "commonGateV02Code": None if v02_result.accepted else v02_result.code,
            "commonGateVersion": gate_v3.GATE_VERSION, "commonGate": v03_label,
            "commonCause": v03_result.cause, "commonCode": v03_result.code,
            "consentState": policy.consentState, "unsupportedOnly": policy.unsupportedOnly,
            "unknownCue": bool(policy.unknownCueExpressions), "policyOverride": (fixture.get("policyOverride") or {}).get("mode"),
            "finalOutcome": final, "unsafeAccept": final == "unsafe_accept", "falseReject": final == "over_reject",
            "safetyConditions": fixture["safetyConditions"], "oracleUsedByGate": False,
        })
    return rows


def ideal_policy_rows() -> list[dict[str, Any]]:
    """Every oracle turn that expects an executable effect must pass the policy gate."""

    rows = []
    for dataset, path in (("development", DEVELOPMENT), ("locked", LOCKED)):
        for case in load_jsonl(path):
            turns = case["context"]["turns"]
            checked = 0
            states = []
            for expected in case["semantics"]["expectedTurns"]:
                turn = next(item for item in turns if item["turn"] == expected["turn"])
                if turn["inputType"] != "text":
                    continue
                policy = gate_v3.analyze_policy(turns, expected["turn"])
                result = expected["expectedResult"]
                executable = result["kind"] in EFFECT_KINDS or (result["kind"] == "outcome" and result.get("outcome") == "success")
                states.append({"turn": expected["turn"], "consentState": policy.consentState, "unsupportedOnly": policy.unsupportedOnly,
                               "expectedKind": result["kind"], "questionPurpose": result.get("questionPurpose")})
                if executable:
                    checked += 1
                    gate = gate_v3.policy_gate({"operation": "create"}, policy, turns=turns, through_turn=expected["turn"], executable=True)
                    if not gate.accepted:
                        raise AssertionError(f"ideal effect rejected: {case['meta']['caseId']} turn {expected['turn']} {gate.code}")
            rows.append({"recordType": "ideal_policy_validation", "version": VERSION, "dataset": dataset, "caseId": case["meta"]["caseId"],
                         "executableTurnsChecked": checked, "policyStates": states, "falseReject": 0, "oracleUsedByGate": False})
    return rows


def ideal_c_rows() -> list[dict[str, Any]]:
    """Oracle-derived ideal candidate-C output per model turn: v0.3 must not add rejections to v0.2.

    The evaluator passes the expected view exactly as v0.2 did; the v0.3 policy
    layer itself never reads it.  Rejections that v0.2 already produced (for
    example candidate-C update/delete target titles, which the v0.2 gate does
    not ground from the oracle) are counted separately and left unchanged.
    """

    rows = []
    for dataset, path in (("development", DEVELOPMENT), ("locked", LOCKED)):
        for case in load_jsonl(path):
            gated_case = {"context": case["context"]}
            turns_out = []
            for turn in v1._model_text_turns(case):
                output = v1._expected_c(case, turn["turn"])
                parsed = contracts.validate_candidate_c(json.dumps(output, ensure_ascii=False), user_turns=v1._visible_text_turns(case, turn["turn"]))
                views = v1._views_from_c(parsed, case)
                view = views[0] if len(views) == 1 else None
                expected_view = v1._expected_view(case, turn["turn"])
                v02 = v3._ORIGINAL_V02_GATE(view, expected_view, gated_case, candidate="C", candidate_c_output=parsed, through_turn=turn["turn"])
                v03 = v3._trusted_gate_v0_3(view, expected_view, gated_case, candidate="C", candidate_c_output=parsed, through_turn=turn["turn"])
                if (v02.accepted, v02.code) != (v03.accepted, v03.code):
                    raise AssertionError(f"v0.3 changed ideal C result: {case['meta']['caseId']} turn {turn['turn']} v0.2={v02} v0.3={v03}")
                turns_out.append({"turn": turn["turn"], "commonGate": "accept" if v03.accepted else "reject", "code": v03.code})
            rows.append({"recordType": "ideal_c_v02_v03_equivalence", "version": VERSION, "dataset": dataset, "caseId": case["meta"]["caseId"],
                         "modelTurns": len(turns_out), "turns": turns_out, "addedRejectionByV03": 0,
                         "preexistingV02Rejections": sum(item["commonGate"] == "reject" for item in turns_out), "oracleUsedByPolicyGate": False})
    return rows


def v02_regression_rows() -> list[dict[str, Any]]:
    """The v0.2 role/effect fixtures keep their v0.2 rejection codes under v0.3."""

    rows = []
    for fixture in load_jsonl(HERE / "mutation-fixtures-v0.2.jsonl"):
        turns_map = {int(key): value for key, value in fixture["userTurns"].items()}
        turns = [{"turn": key, "inputType": "text", "inputSource": "typed_text", "text": value} for key, value in sorted(turns_map.items())]
        parsed = contracts.validate_candidate_c(json.dumps(fixture["candidateOutput"], ensure_ascii=False), user_turns=turns_map)
        through = max(turns_map)
        policy = gate_v3.analyze_policy(turns, through)
        result = gate_v3.common_backend_gate_v0_3(
            fixture["proposal"], fixture["gateContext"], policy=policy, policy_turns=turns, through_turn=through, executable=True,
            candidate_c_output=parsed, user_turns=turns_map,
        )
        if result.accepted or result.code != fixture["expectedGateCode"]:
            raise AssertionError(f"v0.2 regression changed: {fixture['fixtureId']} {result}")
        rows.append({"recordType": "v02_regression", "version": VERSION, "fixtureId": fixture["fixtureId"], "commonGate": "reject",
                     "commonCode": result.code, "sameAsV02": True})
    return rows


def run_validation() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    v1.assert_protected_integrity()
    protected_v02 = v3.assert_v02_protected()
    fixtures = fixture_rows()
    ideal_policy = ideal_policy_rows()
    ideal_c = ideal_c_rows()
    regression = v02_regression_rows()
    v02_rows, v02_summary = v02_validation.run_validation()
    mutations = [row for row in fixtures if row["fixtureKind"] == "mutation"]
    positives = [row for row in fixtures if row["fixtureKind"] == "positive"]
    summary = {
        "version": VERSION,
        "status": "PROPOSED",
        "commonGateVersion": gate_v3.GATE_VERSION,
        "policyAnalyzerVersion": gate_v3.POLICY_ANALYZER_VERSION,
        "fixtureCount": len(fixtures),
        "mutationFixtures": len(mutations),
        "mutationLocalAccept": sum(row["localValidation"] == "accept" for row in mutations),
        "mutationV02Accept": sum(row["commonGateV02"] == "accept" for row in mutations),
        "mutationV03Reject": sum(row["commonGate"] == "reject" for row in mutations),
        "mutationV03Codes": dict(Counter(row["commonCode"] for row in mutations)),
        "positiveFixtures": len(positives),
        "positiveV03Accept": sum(row["commonGate"] == "accept" for row in positives),
        "finalUnsafeAccept": sum(row["unsafeAccept"] for row in fixtures),
        "finalFalseReject": sum(row["falseReject"] for row in fixtures),
        "candidates": dict(Counter(row["candidate"] for row in fixtures)),
        "idealPolicyCases": len(ideal_policy),
        "idealPolicyExecutableTurns": sum(row["executableTurnsChecked"] for row in ideal_policy),
        "idealPolicyFalseReject": 0,
        "idealCandidateCEquivalenceCases": len(ideal_c),
        "idealCandidateCTurns": sum(row["modelTurns"] for row in ideal_c),
        "idealCandidateCAddedRejectionByV03": 0,
        "idealCandidateCPreexistingV02Rejections": sum(row["preexistingV02Rejections"] for row in ideal_c),
        "idealCandidateCPreexistingV02Codes": dict(Counter(item["code"] for row in ideal_c for item in row["turns"] if item["code"])),
        "v02FixtureRegression": {"count": len(regression), "sameCodes": len(regression)},
        "v02ValidationRerun": {"finalUnsafeAccept": v02_summary["finalUnsafeAccept"], "positiveRoleCases": v02_summary["positiveRoleCases"], "mutationFixtureCount": v02_summary["mutationFixtureCount"]},
        "modelCalls": 0, "serverCalls": 0, "databaseCalls": 0,
        "oracleUsedByGate": False,
        "inputs": {
            "fixturesSha256": sha256(FIXTURES),
            "lockedSha256": sha256(LOCKED),
            "developmentSha256": sha256(DEVELOPMENT),
            "semanticSchemaSha256": sha256(HERE / "semantic-oracle-schema-v0.1.json"),
            "protectedV02": protected_v02,
        },
        "gateHashes": v3.gate_hashes(),
    }
    if summary["finalUnsafeAccept"] or summary["finalFalseReject"]:
        raise AssertionError("v0.3 fixture unsafe accept or false reject")
    return fixtures + ideal_policy + ideal_c + regression, summary


def write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    rows, summary = run_validation()
    if args.output:
        write_jsonl(args.output, rows)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":") if args.json else None, indent=None if args.json else 2))


if __name__ == "__main__":
    main()
