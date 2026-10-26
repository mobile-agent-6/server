"""Deterministic validation runner for Issue #30 common gate v0.2."""

from __future__ import annotations

import argparse
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

import common_gate_v0_2 as gate_v2  # noqa: E402
import contracts  # noqa: E402
import validate_contracts as v1_validation  # noqa: E402

VERSION = "issue30-contract-validation-v0.2"
FIXTURES = HERE / "mutation-fixtures-v0.2.jsonl"
DEVELOPMENT = HERE / "development-cases-v0.1.jsonl"
LOCKED = HERE / "locked-cases-v0.1.jsonl"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def mutation_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for fixture in load_jsonl(FIXTURES):
        turns = {int(key): value for key, value in fixture["userTurns"].items()}
        local_result = "accept"
        local_code = None
        parsed: Mapping[str, Any] | None = None
        try:
            parsed = contracts.validate_candidate_c(
                json.dumps(fixture["candidateOutput"], ensure_ascii=False),
                user_turns=turns,
            )
        except contracts.ContractError as exc:
            local_result, local_code = "reject", exc.code
        if parsed is None:
            gate_result = contracts.GateResult(False, "FORMAT", "LOCAL_REJECT")
            gate_label = "not_run"
        else:
            gate_result = gate_v2.common_backend_gate_v0_2(
                fixture["proposal"],
                fixture["gateContext"],
                candidate_c_output=parsed,
                user_turns=turns,
            )
            gate_label = "accept" if gate_result.accepted else "reject"
        final = "unsafe_accept" if local_result == "accept" and gate_result.accepted else "safe_reject"
        expected = (
            fixture["expectedLocalResult"],
            fixture["expectedGateResult"],
            fixture["expectedGateCode"],
            fixture["expectedFinalOutcome"],
        )
        actual = (local_result, gate_label, gate_result.code, final)
        if actual != expected:
            raise AssertionError(f"{fixture['fixtureId']}: expected {expected}, got {actual}")
        rows.append({
            "recordType": "mutation",
            "version": VERSION,
            "fixtureId": fixture["fixtureId"],
            "candidate": "C",
            "sourceCaseId": fixture["sourceCaseId"],
            "mutationLocation": fixture["mutationLocation"],
            "localValidation": local_result,
            "localCode": local_code,
            "commonGateVersion": gate_v2.GATE_VERSION,
            "commonGate": gate_label,
            "commonCause": gate_result.cause,
            "commonCode": gate_result.code,
            "finalOutcome": final,
            "unsafeAccept": final == "unsafe_accept",
            "safetyConditions": fixture["safetyConditions"],
        })
    return rows


def positive_role_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for dataset, path in (("development", DEVELOPMENT), ("locked", LOCKED)):
        for case in load_jsonl(path):
            output = v1_validation.candidate_c_from_case(case)
            turns = {
                turn["turn"]: turn["text"]
                for turn in case["context"]["turns"]
                if turn["inputType"] == "text"
            }
            parsed = contracts.validate_candidate_c(
                json.dumps(output, ensure_ascii=False), user_turns=turns
            )
            role = gate_v2.validate_candidate_c_role_semantics(
                parsed,
                user_turns=turns,
                reference_time=case["context"]["referenceTime"],
            )
            if not role.accepted:
                raise AssertionError(f"ideal role rejected: {case['meta']['caseId']}:{role.code}")
            rows.append({
                "recordType": "positive_role_validation",
                "version": VERSION,
                "dataset": dataset,
                "caseId": case["meta"]["caseId"],
                "candidate": "C",
                "commonGateVersion": gate_v2.GATE_VERSION,
                "roleSemanticValidation": "accept",
                "oracleComparedByGate": False,
                "finalOutcome": "correct",
            })
    return rows


def run_validation() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    mutations = mutation_rows()
    positives = positive_role_rows()
    fixtures = load_jsonl(FIXTURES)
    ids = [fixture["fixtureId"] for fixture in fixtures]
    if len(ids) != len(set(ids)):
        raise AssertionError("duplicate v0.2 fixture ID")
    rows = positives + mutations
    summary = {
        "version": VERSION,
        "status": "PROPOSED",
        "commonGateVersion": gate_v2.GATE_VERSION,
        "positiveRoleCases": len(positives),
        "positiveRoleDatasets": dict(Counter(row["dataset"] for row in positives)),
        "mutationFixtureCount": len(fixtures),
        "mutationLocalValidation": dict(Counter(row["localValidation"] for row in mutations)),
        "mutationCommonGate": dict(Counter(row["commonGate"] for row in mutations)),
        "mutationGateCodes": dict(Counter(row["commonCode"] for row in mutations)),
        "finalUnsafeAccept": sum(row["unsafeAccept"] for row in mutations),
        "modelCalls": 0,
        "serverCalls": 0,
        "databaseCalls": 0,
        "oracleComparedByGate": False,
        "inputs": {
            "fixturesSha256": sha256(FIXTURES),
            "lockedSha256": sha256(LOCKED),
            "semanticSchemaSha256": sha256(HERE / "semantic-oracle-schema-v0.1.json"),
            "v01OllamaManifestSha256": sha256(HERE / "ollama-eval-manifest-v0.1.json"),
        },
    }
    if summary["finalUnsafeAccept"]:
        raise AssertionError("v0.2 mutation unsafe accept")
    return rows, summary


def write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


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
