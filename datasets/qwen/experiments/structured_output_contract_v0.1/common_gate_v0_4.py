"""Issue #30 trusted-policy input completeness gate v0.4 (research only).

v0.3 recomputes its policy state from backend-owned turns, but it cannot tell
whether the caller omitted the current or an earlier turn.  v0.4 requires a
complete, ordered event prefix before an executable effect can reach v0.3.

The existing v0.3 implementation and evaluation artifacts remain immutable.
This module adds no product policy and depends only on its explicit inputs.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import common_gate_v0_3 as gate_v3
import contracts

GATE_VERSION = "issue30-common-gate-v0.4"


def _reject(code: str) -> contracts.GateResult:
    return contracts.GateResult(False, "MAPPING", code)


def validate_complete_turn_prefix(
    turns: Sequence[Mapping[str, Any]] | None,
    through_turn: int | None,
) -> contracts.GateResult:
    """Require the full ordered event prefix ``1..through_turn``.

    The research evaluator owns the complete bounded conversation.  Production
    may instead persist equivalent trusted policy state, but must not construct
    it from an arbitrarily truncated list.
    """

    if turns is None or type(through_turn) is not int or through_turn < 1:
        return _reject("POLICY_INPUTS_INCOMPLETE")
    numbers: list[int] = []
    current: Mapping[str, Any] | None = None
    for turn in turns:
        if not isinstance(turn, Mapping) or type(turn.get("turn")) is not int:
            return _reject("POLICY_INPUTS_INVALID")
        number = int(turn["turn"])
        if number < 1:
            return _reject("POLICY_INPUTS_INVALID")
        numbers.append(number)
        if number == through_turn:
            if current is not None:
                return _reject("POLICY_INPUTS_DUPLICATE_TURN")
            current = turn
    if len(numbers) != len(set(numbers)):
        return _reject("POLICY_INPUTS_DUPLICATE_TURN")
    if numbers != sorted(numbers):
        return _reject("POLICY_INPUTS_OUT_OF_ORDER")
    if current is None or current.get("inputType") != "text":
        return _reject("POLICY_INPUTS_INCOMPLETE")
    prefix = [number for number in numbers if number <= through_turn]
    if prefix != list(range(1, through_turn + 1)):
        return _reject("POLICY_INPUTS_INCOMPLETE")
    return contracts.GateResult(True, None, None)


def common_backend_gate_v0_4(
    proposal: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    policy: Any,
    policy_turns: Sequence[Mapping[str, Any]] | None,
    through_turn: int | None,
    executable: bool,
    candidate_c_output: Mapping[str, Any] | None = None,
    user_turns: Mapping[int, str] | None = None,
    reference_time: str | None = None,
    server_time_trusted: bool | None = None,
) -> contracts.GateResult:
    """Validate the trusted input prefix, then apply the unchanged v0.3 gate."""

    if executable:
        complete = validate_complete_turn_prefix(policy_turns, through_turn)
        if not complete.accepted:
            return complete
    return gate_v3.common_backend_gate_v0_3(
        proposal,
        context,
        policy=policy,
        policy_turns=policy_turns,
        through_turn=through_turn,
        executable=executable,
        candidate_c_output=candidate_c_output,
        user_turns=user_turns,
        reference_time=reference_time,
        server_time_trusted=server_time_trusted,
    )
