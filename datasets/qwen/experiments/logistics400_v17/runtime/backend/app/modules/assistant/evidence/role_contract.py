"""Canonical evidence role matrix implemented by the production backend."""

from __future__ import annotations

from app.modules.assistant.evidence.contract import OperationKind

ROLE_SLOTS: dict[OperationKind, dict[str, frozenset[str]]] = {
    OperationKind.CREATE: {
        "target": frozenset(),
        "change": frozenset({"title", "date", "time", "category"}),
        "filter": frozenset(),
    },
    OperationKind.LIST: {
        "target": frozenset(),
        "change": frozenset(),
        "filter": frozenset(
            {"title", "date", "time", "category", "completed", "futureScope"}
        ),
    },
    OperationKind.UPDATE: {
        "target": frozenset({"title", "date", "time", "category", "completedState"}),
        "change": frozenset({"title", "date", "time", "category", "completed"}),
        "filter": frozenset(),
    },
    OperationKind.DELETE: {
        "target": frozenset({"title", "date", "time", "category", "completedState"}),
        "change": frozenset(),
        "filter": frozenset(),
    },
}


def serializable_role_slots() -> dict[str, dict[str, tuple[str, ...]]]:
    """Stable representation used by drift tests and the DATA role gate."""

    return {
        kind.value: {
            role: tuple(sorted(slots))
            for role, slots in roles.items()
        }
        for kind, roles in ROLE_SLOTS.items()
    }
