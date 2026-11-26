from __future__ import annotations

import json
from pathlib import Path


FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "assistant_evidence_replay_approved_differences.json"
)


def test_replay_difference_approval_list_is_explicit_and_case_scoped() -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert data["baselineCommit"] == "3b0f937"
    assert data["approver"] == "backend-owner"
    recheck = data["baselineRecheck"]
    assert recheck["goldTargetRows"] == 849
    assert recheck["approvedDifferencesAtBaseline"] == 0
    assert recheck["batch6MarkerlessCases"] == [
        "c3-create-b6-056",
        "c3-create-b6-057",
        "c3-create-b6-058",
        "c3-create-b6-059",
        "c3-create-b6-060",
    ]
    seen: set[tuple[str, int]] = set()
    required = {"case", "turn", "baseline", "new", "reason", "decisionBasis"}
    for item in data["differences"]:
        assert set(item) == required
        assert isinstance(item["case"], str) and item["case"]
        assert type(item["turn"]) is int and item["turn"] >= 1
        assert all(isinstance(item[key], str) and item[key] for key in required - {"turn"})
        assert isinstance(json.loads(item["baseline"]), dict)
        assert isinstance(json.loads(item["new"]), dict)
        identity = (item["case"], item["turn"])
        assert identity not in seen
        seen.add(identity)
    assert seen == {
        ("c3-create-b3-002", 1),
        ("c3-create-b3-053", 2),
    }
