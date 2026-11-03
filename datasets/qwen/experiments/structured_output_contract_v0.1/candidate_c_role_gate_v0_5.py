"""Candidate-C role gate v0.5 for datasets authored after Issue #52.

The immutable v0.2 through v0.4 research gates keep their recorded hashes.
This version only widens roles that production can now consume safely.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

import common_gate_v0_2 as gate_v2
import contracts
import dry_mapping

GATE_VERSION = "candidate-c-role-gate-v0.5"

ROLE_SLOTS: dict[str, dict[str, frozenset[str]]] = {
    "create": {
        "target": frozenset(),
        "change": frozenset({"title", "date", "time", "category"}),
        "filter": frozenset(),
    },
    "list": {
        "target": frozenset(),
        "change": frozenset(),
        "filter": frozenset(
            {"title", "date", "time", "category", "completed", "futureScope"}
        ),
    },
    "update": {
        "target": frozenset({"title", "date", "time", "category", "completedState"}),
        "change": frozenset({"title", "date", "time", "category", "completed"}),
        "filter": frozenset(),
    },
    "delete": {
        "target": frozenset({"title", "date", "time", "category", "completedState"}),
        "change": frozenset(),
        "filter": frozenset(),
    },
}


def serializable_role_slots() -> dict[str, dict[str, tuple[str, ...]]]:
    return {
        kind: {role: tuple(sorted(slots)) for role, slots in roles.items()}
        for kind, roles in ROLE_SLOTS.items()
    }


def validate_candidate_c_role_semantics(
    candidate_output: Mapping[str, Any],
    *,
    user_turns: Mapping[int, str],
    reference_time: str,
) -> contracts.GateResult:
    """Validate the v0.5 role matrix without changing older gate modules."""

    try:
        parsed = contracts.validate_candidate_c(
            json.dumps(candidate_output, ensure_ascii=False, separators=(",", ":")),
            user_turns=user_turns,
        )
        reference = dry_mapping._parse_datetime(reference_time)
    except (contracts.ContractError, KeyError, TypeError, ValueError, OverflowError) as exc:
        if isinstance(exc, contracts.ContractError):
            return gate_v2._reject(exc.cause, exc.code)
        return gate_v2._reject("FORMAT", "C_ROLE_VALIDATION_INPUT_INVALID")

    used: dict[tuple[int, tuple[int, str, int | None]], tuple[str, str]] = {}
    role_names = {
        "targetEvidence": "target",
        "changeEvidence": "change",
        "filterEvidence": "filter",
    }
    for operation in parsed["operations"]:
        kind = str(operation["kind"])
        allowed = ROLE_SLOTS[kind]
        for evidence_name, role in role_names.items():
            for slot, spans in operation[evidence_name].items():
                if slot not in allowed[role]:
                    return gate_v2._reject(
                        "MODEL_SEMANTIC", "C_ROLE_NOT_ALLOWED_FOR_OPERATION"
                    )
                result = gate_v2._validate_slot(
                    evidence_name,
                    slot,
                    spans,
                    reference=reference,
                )
                if not result.accepted:
                    return result
                for span in gate_v2._adopted(spans):
                    identity = (
                        int(operation["index"]),
                        gate_v2._span_identity(span),
                    )
                    semantic_role = (evidence_name, slot)
                    previous = used.get(identity)
                    if previous is not None and previous != semantic_role:
                        return gate_v2._reject(
                            "MODEL_SEMANTIC", "C_EVIDENCE_ROLE_CONFLICT"
                        )
                    used[identity] = semantic_role
    return contracts.GateResult(True, None, None)
