"""Optional backend integration for v37. No backend files or public schemas changed.

Call make_provider(inner, reader) to wrap both extraction and decision handling.
An unresolved correction asks for a complete restatement and clears stale task
evidence; a bare 'yes' cannot authorize the discarded operation.
"""
import json
import logging
import re
from .evidence_postprocess_v37 import apply_rules, ClarificationRequired

logger = logging.getLogger(__name__)

_ALLOWED_SLOTS = {
    "targetEvidence": {"title", "date", "time", "category", "completedState"},
    "changeEvidence": {"title", "date", "time", "category", "completed"},
    "filterEvidence": {"title", "date", "time", "category", "completed", "futureScope"},
}

_SLOT_PRIORITY = {
    "time": 1,
    "date": 2,
    "category": 3,
    "title": 4,
    "completed": 5,
    "completedState": 5,
    "futureScope": 6,
}


def _find_ranges(sub: str, turn_text: str) -> list[tuple[int, int]]:
    if not sub or not turn_text:
        return []
    ranges = []
    start = 0
    while True:
        idx = turn_text.find(sub, start)
        if idx == -1:
            break
        ranges.append((idx, idx + len(sub)))
        start = idx + 1
    return ranges


def _trim_title_by_range(turn_text: str, title_range: tuple[int, int], other_range: tuple[int, int]) -> str:
    t_start, t_end = title_range
    o_start, o_end = other_range
    parts = []
    if t_start < o_start:
        parts.append(turn_text[t_start:o_start])
    if o_end < t_end:
        parts.append(turn_text[o_end:t_end])
    cleaned = " ".join(p.strip() for p in parts if p.strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"^(에|을|를|의|로|으로)\s+", "", cleaned).strip()
    return cleaned


def _iter_span_items(slot_val):
    if isinstance(slot_val, list):
        return [item for item in slot_val if isinstance(item, dict) and isinstance(item.get("text"), str)]
    if isinstance(slot_val, dict) and isinstance(slot_val.get("text"), str):
        return [slot_val]
    return []


def _sanitize_output(raw: str, user_turns: dict[int, str]) -> str:
    try:
        payload = json.loads(raw)
    except Exception:
        return raw
    if not isinstance(payload, dict) or not isinstance(payload.get("operations"), list):
        return raw

    changed = False
    for op in payload["operations"]:
        if not isinstance(op, dict):
            continue

        # 1. Extra slot sanitization (e.g., ampm -> time)
        for field_name, allowed in _ALLOWED_SLOTS.items():
            ev = op.get(field_name)
            if not isinstance(ev, dict):
                continue
            if "ampm" in ev and "time" not in ev:
                ev["time"] = ev.pop("ampm")
                changed = True
            extra_keys = [k for k in ev.keys() if k not in allowed]
            for k in extra_keys:
                ev.pop(k, None)
                changed = True

        # 2. Prevent role overlap on create operations
        if op.get("kind") == "create" or op.get("action") == "create":
            if isinstance(op.get("changeEvidence"), dict):
                for redundant_role in ("targetEvidence", "filterEvidence"):
                    if redundant_role in op and op[redundant_role]:
                        op[redundant_role] = {}
                        changed = True

        # 3. Resolve index-based span overlaps across list[dict] / dict slot items
        spans = []
        for role in ("targetEvidence", "changeEvidence", "filterEvidence"):
            ev = op.get(role)
            if not isinstance(ev, dict):
                continue
            for slot, slot_val in list(ev.items()):
                for item in _iter_span_items(slot_val):
                    spans.append((role, slot, item))

        for i in range(len(spans)):
            r1, s1, obj1 = spans[i]
            for j in range(i + 1, len(spans)):
                r2, s2, obj2 = spans[j]
                if (r1, s1) == (r2, s2):
                    continue
                if obj1.get("turn") != obj2.get("turn"):
                    continue
                if {s1, s2} == {"title", "category"}:
                    continue
                turn_str = user_turns.get(obj1.get("turn"), "")
                ranges1 = _find_ranges(obj1.get("text", ""), turn_str)
                ranges2 = _find_ranges(obj2.get("text", ""), turn_str)
                if not ranges1 or not ranges2:
                    continue
                overlaps = [
                    max(a_s, b_s) < min(a_e, b_e)
                    for a_s, a_e in ranges1
                    for b_s, b_e in ranges2
                ]
                if overlaps and all(overlaps):
                    logger.info("Sanitizing EVIDENCE_ROLE_OVERLAP between %s.%s (%r) and %s.%s (%r)",
                                r1, s1, obj1.get("text"), r2, s2, obj2.get("text"))
                    if s1 == "title" and s2 in {"date", "time"}:
                        new_t = _trim_title_by_range(turn_str, ranges1[0], ranges2[0])
                        if new_t and new_t in turn_str:
                            obj1["text"] = new_t
                            changed = True
                            continue
                    elif s2 == "title" and s1 in {"date", "time"}:
                        new_t = _trim_title_by_range(turn_str, ranges2[0], ranges1[0])
                        if new_t and new_t in turn_str:
                            obj2["text"] = new_t
                            changed = True
                            continue

                    p1 = _SLOT_PRIORITY.get(s1, 99)
                    p2 = _SLOT_PRIORITY.get(s2, 99)
                    drop_role, drop_slot, drop_obj = (r2, s2, obj2) if p1 <= p2 else (r1, s1, obj1)
                    slot_container = op.get(drop_role, {}).get(drop_slot)
                    if isinstance(slot_container, list) and drop_obj in slot_container:
                        slot_container.remove(drop_obj)
                        if not slot_container:
                            op[drop_role].pop(drop_slot, None)
                        changed = True
                    elif isinstance(op.get(drop_role), dict) and drop_slot in op[drop_role]:
                        op[drop_role].pop(drop_slot, None)
                        changed = True

    out = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) if changed else raw
    if changed:
        logger.info("Sanitized model output: %s", out)
    return out


class PostprocessedEvidenceModel:
    def __init__(self, inner):
        self.inner = inner

    def is_ready(self):
        return self.inner.is_ready()

    @property
    def name(self):
        return getattr(self.inner, 'name', 'qwen440') + '+v37'

    @property
    def prompt_sha256(self):
        return self.inner.prompt_sha256

    def extract(self, *, turns, reference_time, server_time_trusted, timeout_seconds=None):
        raw = self.inner.extract(turns=turns, reference_time=reference_time,
                                 server_time_trusted=server_time_trusted, timeout_seconds=timeout_seconds)
        user_turns = dict(enumerate(turns, 1))
        processed = apply_rules(raw, user_turns=user_turns,
                                reference=reference_time, server_time_trusted=server_time_trusted).raw
        return _sanitize_output(processed, user_turns)


def make_provider(model, schedule_reader):
    # Import lazily so CPU postprocessing tools need only the contract snapshot.
    from app.modules.assistant.evidence.provider import EvidenceDecisionProvider, _State, _envelope

    class CorrectionDecisionProvider(EvidenceDecisionProvider):
        def decide(self, context):
            try:
                return super().decide(context)
            except ClarificationRequired as exc:
                # No planner/reader call has occurred: extraction precedes planning.
                # Reset pending confirmations and obsolete evidence. A new full
                # command is required, using the existing public ask response.
                return _envelope({'kind': 'ask', 'text': exc.question}, _State())

    return CorrectionDecisionProvider(PostprocessedEvidenceModel(model), schedule_reader)
