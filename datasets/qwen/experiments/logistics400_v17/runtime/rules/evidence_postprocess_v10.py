"""Source-grounded correction repairs over v9.

Ambiguity raises ClarificationRequired, never an executable guessed payload.
Use correction_bridge.CorrectionDecisionProvider to turn it into a backend ask.
No model calls, case IDs, gold lookup, schedule IDs or writes.
"""
import copy
import json
import re
from dataclasses import dataclass

from evidence_postprocess_v9 import apply_rules as apply_v9
from evidence_postprocess_v3 import DATE, TIME, CREATE, DELETE, END, span, dump
from category_postprocess import CUES, _strict_pairs, _reject_constant, _validate, MAX_PAYLOAD_BYTES

VERSION = 'evidence-rules-v10'
ROLES = ('targetEvidence', 'changeEvidence', 'filterEvidence')
MARK = re.compile(r'(?<![가-힣])(?:말고|대신|아니(?:다|라|고)?)(?![가-힣])')
UNCERTAIN = re.compile(r'또는|혹은|아마|아무거나|둘 중|인지|(?:이거|그거|저거|이걸|그걸|저걸)(?![가-힣])|(?:뭐|어느)(?=\s|$)|[?？]|(?:하지|지)\s*(?:마|말)|취소하지')
QUERY = r'(?:조회해|찾아|보여)\s*(?:줘|주세요)'
TITLE = r'[가-힣A-Za-z0-9][가-힣A-Za-z0-9 ]{1,79}?'
BAD_TITLE = re.compile(r'말고|대신|아니|또는|혹은|그리고|일정|제목|분류|카테고리|(?:으로|로) 해|등록해|지워|삭제해|조회해|찾아|완료|미완료')


class ClarificationRequired(ValueError):
    def __init__(self, reason):
        self.reason = reason
        self.question = '정정할 대상이나 값이 명확하지 않아요. 최종 일정 제목과 바꿀 내용, 원하는 작업을 한 문장으로 다시 말씀해 주세요.'
        super().__init__(reason)


@dataclass(frozen=True)
class Result:
    raw: str
    changed: bool
    reasons: tuple[str, ...]
    version: str = VERSION


def source_markers(turns):
    # Explicit quoted titles are not treated as instructions by this layer.
    found = []
    for n, text in turns.items():
        quoted = [(m.start(), m.end()) for m in re.finditer(r'"[^"\n]*"|“[^”\n]*”|‘[^’\n]*’|\x27[^\x27\n]*\x27', text)]
        for m in MARK.finditer(text):
            if not any(a <= m.start() < b for a, b in quoted):
                found.append((n, m))
    return found


def history(old, new, marker, old_turn, new_turn, marker_turn):
    return [*span(new, new_turn), {'turn': old_turn, 'text': old, 'status': 'superseded'},
            {'turn': marker_turn, 'text': marker, 'status': 'correction_marker'}]


def clean_title(title):
    return bool(re.fullmatch(TITLE, title) and not BAD_TITLE.search(title)
                and not re.search(DATE, title) and not re.search(TIME, title))


def category_for_title(title, turn):
    matches = [(m.start(), m.group()) for m in re.finditer('|'.join(CUES), title)]
    if len(matches) > 1:
        raise ClarificationRequired('multiple_category_cues_in_corrected_title')
    return span(matches[0][1], turn) if matches else None


def corrected_category(old, new, marker, ot, nt, mt):
    before = category_for_title(old, ot)
    after = category_for_title(new, nt)
    if before and after and before[0]['text'] != after[0]['text']:
        return history(before[0]['text'], after[0]['text'], marker, ot, nt, mt)
    return after


def plan_source(turns):
    """Return expected operation and only fields unambiguously parsed from source."""
    if list(turns) not in ([1], [1, 2]):
        return None
    if any(re.search(r'["\x27“”‘’\n\r]|그리고|또한', t) for t in turns.values()):
        return None
    first = turns[1]
    # Same-turn create: date correction followed by a full clock/title/action.
    m = re.fullmatch(rf'\s*(?P<old>{DATE}) (?P<marker>말고|대신) (?P<new>{DATE}) (?P<time>{TIME})(?:에)? (?P<title>{TITLE})(?:을|를)? {CREATE}{END}', first)
    if len(turns) == 1 and m and clean_title(m['title']):
        c = {'date': history(m['old'], m['new'], m['marker'], 1, 1, 1),
             'time': span(m['time']), 'title': span(m['title']),
             'category': category_for_title(m['title'], 1)}
        return 'create', {'changeEvidence': c}, 'explicit_create_date_correction'
    # Same-turn create: title correction with an explicit date and clock.
    m = re.fullmatch(rf'\s*(?P<old>{TITLE}) (?P<marker>말고|대신) (?P<new>{TITLE})(?:을|를) (?P<date>{DATE}) (?P<time>{TIME})(?:에)? {CREATE}{END}', first)
    if len(turns) == 1 and m and clean_title(m['old']) and clean_title(m['new']):
        c = {'title': history(m['old'], m['new'], m['marker'], 1, 1, 1),
             'date': span(m['date']), 'time': span(m['time']),
             'category': corrected_category(m['old'], m['new'], m['marker'], 1, 1, 1)}
        return 'create', {'changeEvidence': c}, 'explicit_create_title_correction'
    if len(turns) != 2:
        return None
    # First turn identifies a single operation and its title independently of model output.
    forms = (
        ('create', 'changeEvidence', rf'\s*(?P<date>{DATE}) (?P<time>{TIME})(?:에)? (?P<title>{TITLE})(?:을|를)? {CREATE}{END}'),
        ('list', 'filterEvidence', rf'\s*(?P<title>{TITLE})(?:을|를)? {QUERY}{END}'),
        ('delete', 'targetEvidence', rf'\s*(?P<title>{TITLE})(?: 일정)?(?:을|를)? {DELETE}{END}'),
    )
    for kind, role, pattern in forms:
        a = re.fullmatch(pattern, first)
        if not a or not clean_title(a['title']):
            continue
        old = a['title']
        # A 말고 B explicitly repeats the old title in the latest turn.
        b = re.fullmatch(rf'\s*{re.escape(old)} (?P<marker>말고|대신) (?P<new>{TITLE})(?:으로|로) 해\s*(?:줘|주세요){END}', turns[2])
        ot = 2
        if not b:
            endings = {'create': r'(?:으로|로) 해\s*(?:줘|주세요)',
                       'list': rf'(?:으로|로) {QUERY}',
                       'delete': r'(?:을|를) 말하는 거야|(?:이야|야)'}
            b = re.fullmatch(rf'\s*(?P<marker>아니|아니다)[,，]?\s+(?P<new>{TITLE})(?:{endings[kind]}){END}', turns[2])
            ot = 1
        if not b or not clean_title(b['new']) or b['new'] == old:
            continue
        edits = {'title': history(old, b['new'], b['marker'], ot, 2, 2)}
        if kind == 'create':
            edits.update(date=span(a['date']), time=span(a['time']),
                         category=corrected_category(old, b['new'], b['marker'], ot, 2, 2))
        return kind, {role: edits}, 'explicit_followup_title_correction'
    return None


def safe_shape(value, turns):
    if set(value) != {'operations', 'unsupportedEvidence', 'missingInformation'}:
        return False
    if not isinstance(value['operations'], list) or len(value['operations']) != 1 or value['unsupportedEvidence']:
        return False
    op = value['operations'][0]
    if set(op) != {'index', 'kind', *ROLES, 'unsupportedEvidence', 'missingInformation'} or op['unsupportedEvidence']:
        return False
    for role in ROLES:
        if not isinstance(op[role], dict):
            return False
        for spans in op[role].values():
            if not isinstance(spans, list) or not 1 <= len(spans) <= 12:
                return False
            for s in spans:
                if not isinstance(s, dict) or set(s) != {'turn', 'text', 'status'}:
                    return False
                if type(s['turn']) is not int or s['turn'] not in turns or not isinstance(s['text'], str) or not s['text']:
                    return False
                if s['status'] not in ('adopted', 'superseded', 'correction_marker'):
                    return False
    return True


def apply_rules(raw, *, user_turns, reference, server_time_trusted):
    markers = source_markers(user_turns)
    if markers and any(UNCERTAIN.search(t) for t in user_turns.values()):
        raise ClarificationRequired('ambiguous_correction_source')
    if len(markers) > 1:
        raise ClarificationRequired('multiple_corrections_need_restatement')
    previous = apply_v9(raw, user_turns=user_turns, reference=reference, server_time_trusted=server_time_trusted)
    if not markers:
        return Result(previous.raw, previous.raw != raw, tuple(previous.reasons))
    current = previous.raw
    reasons = list(previous.reasons)
    try:
        if not isinstance(current, str) or len(current.encode()) > MAX_PAYLOAD_BYTES:
            raise ValueError('payload')
        value = json.loads(current, object_pairs_hook=_strict_pairs, parse_constant=_reject_constant)
        plan = plan_source(user_turns)
        if plan and safe_shape(value, user_turns):
            kind, edits, reason = plan
            candidate = copy.deepcopy(value)
            op = candidate['operations'][0]
            if op['kind'] != kind:
                raise ClarificationRequired('operation_disagrees_with_correction_source')
            if op['kind'] == kind:
                for role, slots in edits.items():
                    for slot, spans in slots.items():
                        if spans is None:
                            op[role].pop(slot, None)
                        else:
                            op[role][slot] = spans
                # Validate every unchanged field too; never hide unrelated invalid evidence.
                _validate(dump(candidate), user_turns, reference)
                if candidate != value:
                    current = dump(candidate)
                    value = candidate
                    reasons.append(reason)
        _validate(current, user_turns, reference)
        # Unknown grammar is accepted only when every visible correction marker
        # is already accounted for in valid evidence. Otherwise ask for restatement.
        covered = {(s['turn'], s['text']) for op in value['operations'] for role in ROLES
                   for spans in op[role].values() for s in spans if s['status'] == 'correction_marker'}
        if any(not any(n == turn and m.group() in text for turn, text in covered) for n, m in markers):
            raise ClarificationRequired('unresolved_correction_marker')
    except ClarificationRequired:
        raise
    except (ValueError, KeyError, TypeError, IndexError, AttributeError, UnicodeError, RecursionError):
        raise ClarificationRequired('invalid_or_unresolved_correction') from None
    return Result(current, current != raw, tuple(reasons))
