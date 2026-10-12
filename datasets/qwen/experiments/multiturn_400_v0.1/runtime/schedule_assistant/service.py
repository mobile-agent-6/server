"""Single-session provisional conversation controller.

The model proposes actions; this controller is the only component that can write
through the Schedule API. Public AI endpoint and shared schema remain TBD-AI.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

from .actions import ActionError, parse_action
from .clients import ScheduleError

PROMPT = (Path(__file__).parent / "prompt.txt").read_text(encoding="utf-8")
YES = {"응", "네", "예", "맞아", "확인", "진행해"}
NO = {"아니", "아니요", "취소", "취소해", "그만"}


class Model(Protocol):
    def generate(self, messages: list[dict[str, str]]) -> str: ...


class Schedules(Protocol):
    def list(self, filters: dict[str, Any]) -> list[dict[str, Any]]: ...
    def get(self, schedule_id: str) -> dict[str, Any]: ...
    def create(self, body: dict[str, Any], key: str) -> dict[str, Any]: ...
    def update(self, schedule_id: str, changes: dict[str, Any]) -> dict[str, Any]: ...
    def delete(self, schedule_id: str) -> None: ...


@dataclass(frozen=True)
class Reply:
    kind: str
    message: str
    schedule: dict[str, Any] | None = None


@dataclass
class Pending:
    operation: str
    schedule_id: str
    updated_at: str
    changes: dict[str, Any] | None
    confirmation: str


@dataclass
class Selection:
    operation: str
    changes: dict[str, Any] | None
    candidate_ids: list[str]


@dataclass
class UncertainCreate:
    body: dict[str, Any]
    key: str
    started_at: datetime


@dataclass
class State:
    history: list[dict[str, str]] = field(default_factory=list)
    candidates: dict[str, dict[str, Any]] = field(default_factory=dict)
    pending: Pending | None = None
    selection: Selection | None = None
    uncertain_create: UncertainCreate | None = None
    last_result: dict[str, Any] | None = None


class ConversationService:
    """One in-memory conversation; create one instance per local session."""

    def __init__(self, model: Model, schedules: Schedules, clock: Any = None):
        self.model = model
        self.schedules = schedules
        self.clock = clock or (lambda: datetime.now().astimezone())
        self.state = State()

    def reset(self) -> None:
        # A lost POST response may already have created a schedule. Keep its key.
        self.state = State(uncertain_create=self.state.uncertain_create)

    def _in_past(self, value: str) -> bool:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) < self.clock()

    def _ask_future_time(self, operation: str) -> Reply:
        verb = "등록" if operation == "create" else "변경"
        message = f"말씀하신 시각이 이미 지났어요. 언제로 {verb}할까요?"
        self.state.history.append({"role": "assistant", "content": message})
        return Reply("ask", message)

    @staticmethod
    def _confirmation(current: dict[str, Any], operation: str,
                      changes: dict[str, Any] | None) -> str:
        title = current.get("title")
        when = current.get("scheduledAt")
        if not isinstance(title, str) or not isinstance(when, str):
            raise ValueError("invalid schedule")
        subject = f"{title} ({when})"
        if operation == "delete":
            return f"{subject} 일정을 삭제할까요?"
        labels = {"title": "제목", "scheduledAt": "시각", "category": "카테고리",
                  "completed": "완료", "reminderMinutesBefore": "알림(분 전)"}
        details = ", ".join(f"{labels[key]}={json.dumps(value, ensure_ascii=False)}"
                            for key, value in (changes or {}).items())
        return f"{subject} 일정을 {details}로 변경할까요?"

    def _messages(self) -> list[dict[str, str]]:
        now = self.clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("clock must return timezone-aware datetime")
        context = {
            "reference_datetime": now.isoformat(timespec="seconds"),
            "schedule_candidates": list(self.state.candidates.values()),
            "last_schedule_result": self.state.last_result,
        }
        return [{"role": "system", "content": PROMPT + "\n현재 문맥: " + json.dumps(context, ensure_ascii=False)}] + self.state.history[-20:]

    def _begin_confirmation(self, target_id: str, operation: str,
                            changes: dict[str, Any] | None) -> Reply:
        try:
            current = self.schedules.get(target_id)
        except ScheduleError:
            return Reply("error", "대상 일정을 확인하지 못했어요.")
        if current.get("updatedAt") != self.state.candidates[target_id].get("updatedAt"):
            self.state.candidates = {}
            return Reply("stale", "일정이 바뀌었어요. 다시 조회해 주세요.")
        try:
            confirmation = self._confirmation(current, operation, changes)
        except ValueError:
            return Reply("error", "대상 일정을 확인하지 못했어요.")
        self.state.pending = Pending(operation, target_id, current["updatedAt"],
                                     changes, confirmation)
        return Reply("confirm", confirmation)

    def _select(self, utterance: str) -> Reply | None:
        selection = self.state.selection
        if selection is None:
            return None
        answer = utterance.strip().rstrip(".!? ").lower()
        if answer in NO:
            self.state.selection = None
            self.state.candidates = {}
            return Reply("cancelled", "대상 선택을 취소했어요. 일정은 변경하지 않았어요.")
        if answer.startswith(("아니 ", "아니,", "취소하고", "대신 ")):
            self.state.selection = None
            self.state.candidates = {}
            self.state.history.append({"role": "assistant", "content": "이전 대상 선택은 취소했어요."})
            return None
        ordinals = {"첫 번째": 1, "첫번째": 1, "두 번째": 2, "두번째": 2,
                    "세 번째": 3, "세번째": 3}
        number = ordinals.get(answer)
        if number is None:
            digits = answer.removesuffix("번").strip()
            number = int(digits) if digits.isdecimal() else None
        if number is None or not 1 <= number <= len(selection.candidate_ids):
            return Reply("ask", f"1번부터 {len(selection.candidate_ids)}번 중 대상 번호를 말씀해 주세요. 취소할 수도 있어요.")
        self.state.selection = None
        target_id = selection.candidate_ids[number - 1]
        reply = self._begin_confirmation(target_id, selection.operation, selection.changes)
        self.state.history.append({"role": "user", "content": utterance.strip()})
        self.state.history.append({"role": "assistant", "content": reply.message})
        return reply

    def _retry_create(self, utterance: str) -> Reply | None:
        pending = self.state.uncertain_create
        if pending is None:
            return None
        if utterance.strip() not in {"재시도", "다시 시도", "결과 확인"}:
            return Reply("error", "앞선 등록 결과가 불확실해요. 같은 요청을 확인하려면 '재시도'라고 말씀해 주세요.")
        if self.clock() - pending.started_at >= timedelta(hours=24):
            return Reply("error", "재시도 가능 시간이 지났어요. 서버에서 등록 여부를 먼저 확인해 주세요.")
        try:
            result = self.schedules.create(pending.body, pending.key)
        except ScheduleError as exc:
            return Reply("error", "등록 결과를 확인할 수 없어요." if exc.uncertain else "등록을 확인하지 못했어요. 서버 상태를 확인해 주세요.")
        self.state.uncertain_create = None
        self.state.last_result = result
        self.state.candidates = {}
        return Reply("created", "일정을 등록했어요.", result)

    def _confirm(self, utterance: str) -> Reply | None:
        pending = self.state.pending
        if pending is None:
            return None
        answer = utterance.strip().rstrip(".!? ").lower()
        if answer in NO:
            self.state.pending = None
            self.state.candidates = {}
            self.state.history.append({"role": "user", "content": utterance})
            self.state.history.append({"role": "assistant", "content": "취소했어요."})
            return Reply("cancelled", "취소했어요. 일정은 변경하지 않았어요.")
        if answer not in YES:
            self.state.pending = None
            # A correction that names the confirmed schedule may reuse only that
            # candidate. Every other request must look up its target again.
            selected = self.state.candidates.get(pending.schedule_id)
            title = selected.get("title") if selected else None
            self.state.candidates = ({pending.schedule_id: selected}
                                     if isinstance(title, str) and title in utterance else {})
            self.state.history.append({"role": "assistant", "content": "이전 변경 확인은 취소했어요."})
            return None
        self.state.pending = None
        write_attempted = False
        try:
            current = self.schedules.get(pending.schedule_id)
            if current.get("updatedAt") != pending.updated_at:
                self.state.candidates = {}
                return Reply("stale", "그 사이 일정이 바뀌었어요. 다시 조회해 주세요.")
            if pending.operation == "update":
                if pending.changes and "scheduledAt" in pending.changes and self._in_past(pending.changes["scheduledAt"]):
                    self.state.candidates = {}
                    self.state.history.append({"role": "user", "content": utterance})
                    return self._ask_future_time("update")
                write_attempted = True
                result = self.schedules.update(pending.schedule_id, pending.changes or {})
                self.state.last_result = result
                message = "일정을 변경했어요."
                kind = "updated"
            else:
                write_attempted = True
                self.schedules.delete(pending.schedule_id)
                self.state.last_result = {"deletedId": pending.schedule_id}
                result = None
                message = "일정을 삭제했어요."
                kind = "deleted"
        except ScheduleError as exc:
            self.state.candidates = {}
            if exc.uncertain and write_attempted:
                try:
                    current = self.schedules.get(pending.schedule_id)
                except ScheduleError:
                    message = "처리 결과가 불확실하고 현재 일정을 조회하지 못했어요. 서버 상태를 확인한 뒤 다시 요청해 주세요."
                else:
                    title = current.get("title", "제목 없음")
                    when = current.get("scheduledAt", "시각 없음")
                    message = f"처리 결과가 불확실해요. 현재 일정은 {title} ({when})입니다. 추가 작업은 새로 요청해 주세요."
            else:
                message = "처리 결과를 확인할 수 없어요." if exc.uncertain else "일정 처리에 실패했어요."
            self.state.history.append({"role": "user", "content": utterance})
            self.state.history.append({"role": "assistant", "content": message})
            return Reply("error", message)
        self.state.candidates = {}
        self.state.history.append({"role": "user", "content": utterance})
        self.state.history.append({"role": "assistant", "content": message})
        return Reply(kind, message, result)

    def handle(self, utterance: str) -> Reply:
        if not isinstance(utterance, str) or not utterance.strip() or len(utterance) > 500:
            return Reply("error", "짧은 일정 요청을 입력해 주세요.")
        prior = self._retry_create(utterance)
        if prior is not None:
            return prior
        prior = self._select(utterance)
        if prior is not None:
            return prior
        prior = self._confirm(utterance)
        if prior is not None:
            return prior
        self.state.history.append({"role": "user", "content": utterance.strip()})
        listed = False
        for _ in range(2):
            try:
                raw = self.model.generate(self._messages())
                action = parse_action(raw)
            except (ActionError, RuntimeError, ValueError):
                return Reply("error", "요청을 이해하지 못했어요. 다시 말씀해 주세요.")
            self.state.history.append({"role": "assistant", "content": raw})
            request = action.request
            if action.name == "ASK":
                return Reply("ask", action.message or "다시 말씀해 주세요.")
            if action.name == "ANSWER":
                if listed:
                    items = list(self.state.candidates.values())
                    if not items:
                        message = "조회된 일정이 없어요."
                        self.state.history[-1] = {"role": "assistant", "content": message}
                        return Reply("answer", message)
                    summary = ", ".join(f"{item.get('title', '제목 없음')} ({item.get('scheduledAt', '시각 없음')})"
                                        for item in items[:10])
                    suffix = " 외 더 있어요. 범위를 좁혀 조회해 주세요." if len(items) > 10 else ""
                    message = f"조회된 일정: {summary}{suffix}"
                else:
                    message = "네, 일정 요청을 말씀해 주세요."
                self.state.history[-1] = {"role": "assistant", "content": message}
                return Reply("answer", message)
            if action.name == "LIST":
                if listed:
                    return Reply("error", "일정을 다시 조회해야 해요. 요청을 다시 말씀해 주세요.")
                try:
                    items = self.schedules.list(request or {})
                    candidates: dict[str, dict[str, Any]] = {}
                    for item in items:
                        identifier = str(UUID(item["id"]))
                        if identifier in candidates or not isinstance(item.get("updatedAt"), str):
                            raise ValueError("invalid candidate")
                        candidates[identifier] = item
                    self.state.candidates = candidates
                except (ScheduleError, ValueError, KeyError, TypeError):
                    return Reply("error", "일정을 조회하지 못했어요.")
                listed = True
                continue
            if action.name == "CREATE":
                if request and self._in_past(request["scheduledAt"]):
                    return self._ask_future_time("create")
                body = request or {}
                key = str(uuid4())
                started_at = self.clock()
                try:
                    result = self.schedules.create(body, key)
                except ScheduleError as exc:
                    if exc.uncertain:
                        self.state.uncertain_create = UncertainCreate(body, key, started_at)
                    return Reply("error", "등록 결과를 확인할 수 없어요." if exc.uncertain else "일정 등록에 실패했어요.")
                self.state.last_result = result
                self.state.candidates = {}
                return Reply("created", "일정을 등록했어요.", result)
            if action.name == "SELECT_TARGET":
                if len(self.state.candidates) > 10:
                    return Reply("ask", "대상 일정이 많아요. 날짜와 제목을 더 좁혀 말씀해 주세요.")
                if len(self.state.candidates) < 2:
                    return Reply("error", "대상 일정을 다시 조회해 주세요.")
                operation = request["operation"]
                changes = request.get("changes")
                self.state.selection = Selection(operation, changes, list(self.state.candidates))
                options = ", ".join(f"{index}번 {item.get('title', '제목 없음')} ({item.get('scheduledAt', '시각 없음')})"
                                    for index, item in enumerate(self.state.candidates.values(), 1))
                return Reply("ask", f"어느 일정을 뜻하나요? {options}. 번호를 말씀해 주세요.")
            if action.name in {"CONFIRM_UPDATE", "CONFIRM_DELETE"}:
                target_id = request["targetId"] if request else ""
                if target_id not in self.state.candidates:
                    return Reply("error", "대상 일정을 먼저 조회해 주세요.")
                if len(self.state.candidates) != 1:
                    return Reply("error", "여러 일정이 조회됐어요. 번호로 대상을 선택해 주세요.")
                operation = "update" if action.name == "CONFIRM_UPDATE" else "delete"
                changes = request.get("changes") if request else None
                return self._begin_confirmation(target_id, operation, changes)
        return Reply("error", "요청을 처리하지 못했어요. 다시 말씀해 주세요.")
