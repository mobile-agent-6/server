from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import candidate_c_role_gate_v0_5 as gate


def span(text: str) -> dict[str, object]:
    return {"turn": 1, "text": text, "status": "adopted"}


def output(kind: str, *, target=None, change=None, filter=None) -> dict[str, object]:
    return {
        "operations": [
            {
                "index": 1,
                "kind": kind,
                "targetEvidence": target or {},
                "changeEvidence": change or {},
                "filterEvidence": filter or {},
                "unsupportedEvidence": [],
                "missingInformation": [],
            }
        ],
        "unsupportedEvidence": [],
        "missingInformation": [],
    }


def validate(candidate: dict[str, object], turn: str):
    return gate.validate_candidate_c_role_semantics(
        candidate,
        user_turns={1: turn},
        reference_time="2026-11-03T10:00:00+09:00",
    )


def test_new_target_and_filter_roles_are_accepted() -> None:
    update = output(
        "update",
        target={"title": [span("회의")], "time": [span("오후 3시")], "category": [span("입고")]},
        change={"time": [span("오후 4시")]},
    )
    listing = output("list", filter={"time": [span("오후 3시")]})
    deletion = output(
        "delete",
        target={"title": [span("회의")], "time": [span("3시")], "category": [span("입고")]},
    )

    assert validate(update, "입고 회의를 오후 3시에서 오후 4시로 바꿔 줘").accepted
    assert validate(listing, "오후 3시 일정 보여 줘").accepted
    assert validate(deletion, "입고 회의 3시 일정 지워 줘").accepted


def test_unimplemented_role_is_still_rejected() -> None:
    candidate = output("create", target={"time": [span("오후 3시")]})
    result = validate(candidate, "오후 3시 일정 등록해 줘")
    assert not result.accepted
    assert result.code == "C_ROLE_NOT_ALLOWED_FOR_OPERATION"
