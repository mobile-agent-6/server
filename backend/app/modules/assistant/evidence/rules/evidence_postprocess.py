"""Conservative rules after category-v1; no targets, case IDs or model imports.

Run normal backend validation/planning after this helper. This is not an
authorization to execute an operation. Ambiguous or invalid outputs pass through.
"""
import copy
import json
import re
from dataclasses import dataclass
from .category_postprocess import (
    apply_category_rules, _strict_pairs, _reject_constant, _validate,
    MAX_PAYLOAD_BYTES,
)
from app.modules.assistant.evidence.normalize import (
    DatePurpose, DateStatus, TimeStatus, resolve_date, resolve_time,
    is_date_or_time_only, mentions_date_or_time,
)

VERSION = 'evidence-rules-v2'

@dataclass(frozen=True)
class RuleResult:
    raw: str
    changed: bool
    reasons: tuple[str, ...]
    version: str = VERSION

def _dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))

def _single(spans):
    return (isinstance(spans, list) and len(spans) == 1
            and isinstance(spans[0], dict) and spans[0].get('status') == 'adopted')

def _state_span_is_complete(spans, turns):
    if not _single(spans):
        return False
    span = spans[0]
    text = turns[span['turn']]
    positions = list(re.finditer(re.escape(span['text']), text))
    if len(positions) != 1:
        return False
    start, end = positions[0].span()
    before, after = text[:start], text[end:]
    # Do not turn an invalid-key output into an accepted positive state when the
    # model cut '완료' out of '미완료' or '끝난' out of '안 끝난'.
    if before and re.search(r'[가-힣A-Za-z0-9]$', before):
        return False
    if re.search(r'(?:안|못|아직|미)\s*$', before):
        return False
    if re.match(r'\s*(?:(?:안|못)(?=\s|되|돼|됐|끝|$)|아니|취소|해제)', after):
        return False
    return True

def _typed_fields(value, turns, reference):
    op = value['operations'][0]
    reasons = []
    # These two slots mean the same state; the legal key depends on the role.
    role = {'update': 'changeEvidence', 'list': 'filterEvidence'}.get(op['kind'])
    if role:
        evidence = op[role]
        if 'completedState' in evidence and 'completed' not in evidence:
            if _state_span_is_complete(evidence['completedState'], turns):
                evidence['completed'] = evidence.pop('completedState')
                reasons.append('completed_field_name')
    # Reclassify only pure date/time text, in the SAME role, one turn, no history.
    # Never infer whether a date is an old target or a new value.
    if len(turns) != 1 or role is None:
        return reasons
    for role in (role,):
        evidence = op[role]
        for source in ('date', 'title'):
            spans = evidence.get(source)
            if not _single(spans):
                continue
            text = spans[0]['text']
            if not is_date_or_time_only(text):
                continue
            purpose = DatePurpose.SEARCH if role == 'filterEvidence' else DatePurpose.SCHEDULE
            date = resolve_date([text], reference, purpose=purpose)
            time = resolve_time([text])
            dest = None
            if source == 'date' and date.status == DateStatus.NOT_A_DATE and time.resolved:
                dest = 'time'
            if source == 'title' and date.resolved and time.status == TimeStatus.NOT_A_TIME:
                dest = 'date'
            if dest and dest not in evidence:
                evidence[dest] = evidence.pop(source)
                reasons.append(f'{role}.{source}_to_{dest}')
    return reasons

def _missing_flags(value, turns, reference, trusted):
    op = value['operations'][0]
    if op['kind'] != 'create' or len(turns) != 1 or type(trusted) is not bool:
        return []
    change = op['changeEvidence']
    if not _single(change.get('title')) or not _single(change.get('date')):
        return []
    if any(not _single(spans) for spans in change.values()):
        return []
    text = next(iter(turns.values()))
    if re.search(r'아니|말고|대신|취소', text):
        return []
    # Account for every temporal cue outside the title/evidence before declaring
    # a missing time/period. An omitted explicit period must not become AMPM-missing.
    covered = [False] * len(text)
    for spans in change.values():
        span = spans[0]
        positions = list(re.finditer(re.escape(span['text']), text))
        if len(positions) != 1 or span.get('occurrence') not in (None, 1):
            return []
        for i in range(*positions[0].span()):
            covered[i] = True
    remainder = ''.join(' ' if covered[i] else c for i,c in enumerate(text))
    if mentions_date_or_time(remainder):
        return []
    flags = []
    time_spans = change.get('time')
    if time_spans is None:
        flags.append('time')
    elif resolve_time([time_spans[0]['text']]).status == TimeStatus.NEEDS_AMPM:
        flags.append('ampm')
    date = resolve_date([change['date'][0]['text']], reference, purpose=DatePurpose.SCHEDULE)
    if not trusted and not date.absolute and date.status in (
        DateStatus.RESOLVED, DateStatus.PAST_WITHOUT_YEAR, DateStatus.WEEKDAY_IS_TODAY,
    ):
        flags.append('absolute_datetime_due_to_untrusted_time')
    added = []
    for flag in flags:
        for container in (op, value):
            if flag not in container['missingInformation']:
                container['missingInformation'].append(flag)
                if flag not in added:
                    added.append(flag)
    return ['missing_'+flag for flag in added]

def apply_evidence_rules(raw, *, user_turns, reference, server_time_trusted):
    category = apply_category_rules(raw, user_turns=user_turns, reference=reference)
    current = category.raw
    reasons = [category.reason] if category.changed else []
    try:
        if not isinstance(current, str) or len(current.encode('utf-8')) > MAX_PAYLOAD_BYTES:
            raise ValueError('invalid payload')
        value = json.loads(current, object_pairs_hook=_strict_pairs, parse_constant=_reject_constant)
        if len(value['operations']) != 1:
            raise ValueError('outside single-operation scope')
        candidate = copy.deepcopy(value)
        typed = _typed_fields(candidate, user_turns, reference)
        if typed:
            _validate(_dump(candidate), user_turns, reference)
            value = candidate
            current = _dump(value)
            reasons.extend(typed)
        # Missing metadata never conceals invalid evidence or malformed JSON.
        _validate(current, user_turns, reference)
        candidate = copy.deepcopy(value)
        missing = _missing_flags(candidate, user_turns, reference, server_time_trusted)
        if missing:
            _validate(_dump(candidate), user_turns, reference)
            current = _dump(candidate)
            reasons.extend(missing)
    except (ValueError, KeyError, TypeError, IndexError, AttributeError, RecursionError, UnicodeError):
        pass
    return RuleResult(current, current != raw, tuple(reasons))
