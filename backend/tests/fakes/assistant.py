"""Scripted Assistant provider used only by tests."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable

from app.modules.assistant.decisions import DecisionContext, DecisionEnvelope


class ScriptedDecisionProvider:
    def __init__(
        self, steps: Iterable[DecisionEnvelope | BaseException]
    ) -> None:
        self._steps = deque(steps)
        self.contexts: list[DecisionContext] = []

    @property
    def call_count(self) -> int:
        return len(self.contexts)

    def decide(self, context: DecisionContext) -> DecisionEnvelope:
        self.contexts.append(context)
        if not self._steps:
            raise AssertionError("unexpected DecisionProvider call: script is empty")
        step = self._steps.popleft()
        if isinstance(step, BaseException):
            raise step
        return step

    def assert_exhausted(self) -> None:
        if self._steps:
            raise AssertionError(
                f"DecisionProvider script has {len(self._steps)} unused step(s)"
            )
