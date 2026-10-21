"""Strict provisional action format for Qwen responses."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

CATEGORIES = {"INBOUND", "INVENTORY", "OUTBOUND", "RETURN", "OTHER"}
ACTIONS = {"ASK", "LIST", "CREATE", "SELECT_TARGET", "CONFIRM_UPDATE", "CONFIRM_DELETE", "ANSWER"}
DATETIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})\Z")


class ActionError(ValueError):
    """Model output cannot safely be used."""


@dataclass(frozen=True)
class Action:
    name: str
    message: str | None
    request: dict[str, Any] | None


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ActionError(f"duplicate key: {key}")
        result[key] = value
    return result


def _fields(value: Any, required: set[str], optional: set[str] = frozenset()) -> dict[str, Any]:
    if not isinstance(value, dict) or required - value.keys() or value.keys() - required - optional:
        raise ActionError("invalid fields")
    return value


def _datetime(value: Any) -> str:
    if not isinstance(value, str) or not DATETIME.fullmatch(value):
        raise ActionError("invalid datetime")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ActionError("invalid datetime") from exc
    return value


def _title(value: Any) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 100:
        raise ActionError("invalid title")
    if any(unicodedata.category(ch) == "Cc" for ch in value):
        raise ActionError("invalid title")
    return value.strip()


def _category(value: Any) -> str:
    if not isinstance(value, str) or value not in CATEGORIES:
        raise ActionError("invalid category")
    return value


def _uuid(value: Any) -> str:
    if not isinstance(value, str):
        raise ActionError("invalid target ID")
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise ActionError("invalid target ID") from exc


def _message(value: Any, required: bool) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 240:
        raise ActionError("invalid message")
    return value.strip()


def parse_action(raw: str) -> Action:
    try:
        value = json.loads(raw, object_pairs_hook=_object)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ActionError("invalid JSON") from exc
    data = _fields(value, {"action", "message", "request"})
    name = data["action"]
    if not isinstance(name, str) or name not in ACTIONS:
        raise ActionError("invalid action")
    message = _message(data["message"], name == "ASK")
    if name in {"ANSWER", "SELECT_TARGET"} and message is not None:
        raise ActionError("unexpected message")
    request = data["request"]
    if name in {"ASK", "ANSWER"}:
        if request is not None:
            raise ActionError("unexpected request")
    elif name == "LIST":
        request = _fields(request, {"from", "to"}, {"category", "completed"})
        _datetime(request["from"])
        _datetime(request["to"])
        start = datetime.fromisoformat(request["from"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(request["to"].replace("Z", "+00:00"))
        if not start < end <= start + timedelta(days=92):
            raise ActionError("invalid date range")
        if "category" in request:
            _category(request["category"])
        if "completed" in request and type(request["completed"]) is not bool:
            raise ActionError("invalid completed")
    elif name == "CREATE":
        request = _fields(request, {"title", "scheduledAt", "category"})
        request = {"title": _title(request["title"]), "scheduledAt": _datetime(request["scheduledAt"]),
                   "category": _category(request["category"])}
    elif name == "CONFIRM_UPDATE" or (name == "SELECT_TARGET" and isinstance(request, dict)
                                      and request.get("operation") == "update"):
        request = _fields(request, {"targetId", "changes"} if name == "CONFIRM_UPDATE"
                          else {"operation", "changes"})
        changes = _fields(request["changes"], set(), {"title", "scheduledAt", "category", "completed"})
        if not changes:
            raise ActionError("empty update")
        checked: dict[str, Any] = {}
        for key, item in changes.items():
            if key == "title":
                checked[key] = _title(item)
            elif key == "scheduledAt":
                checked[key] = _datetime(item)
            elif key == "category":
                checked[key] = _category(item)
            elif key == "completed":
                if type(item) is not bool:
                    raise ActionError("invalid completed")
                checked[key] = item
        request = ({"targetId": _uuid(request["targetId"]), "changes": checked}
                   if name == "CONFIRM_UPDATE" else {"operation": "update", "changes": checked})
    elif name == "SELECT_TARGET":
        request = _fields(request, {"operation"})
        if request["operation"] != "delete":
            raise ActionError("invalid selection operation")
    else:
        request = _fields(request, {"targetId"})
        request = {"targetId": _uuid(request["targetId"])}
    return Action(name, message, request)
