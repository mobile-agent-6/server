"""Source-proven query-time recovery and explicit alternative-choice questions.
No answer lookups, model calls, DB operations, or automatic choice of alternatives.
"""
import copy,json,re
from dataclasses import dataclass
from evidence_postprocess_v22_candidate import apply_rules as previous
from evidence_postprocess_v15 import model_envelope,NoPlan,ClarificationRequired
from evidence_postprocess_v21 import QUOTED
from evidence_postprocess_v3 import dump
from category_postprocess import _validate,_strict_pairs,_reject_constant,MAX_PAYLOAD_BYTES
from query_correction_source import list_correction_history
VERSION='evidence-rules-v23-candidate'
ALTERNATIVE=re.compile(r'(?<!\S)(?:또는|혹은)(?!\S)')

@dataclass(frozen=True)
class Result:
    raw:str
    changed:bool
    reasons:tuple[str,...]
    version:str=VERSION

def has_alternative_choice(turns):
    # Quoted text may be a literal title. A connector outside two quoted choices
    # is still ambiguous. Conservatively require restatement of the final choice.
    for text in turns.values():
        if isinstance(text,str) and ALTERNATIVE.search(QUOTED.sub(lambda m:' '*len(m.group()),text)):
            return True
    return False

def repair_query_ampm(raw,turns,reference,trusted):
    if not trusted or not isinstance(raw,str) or len(raw.encode())>MAX_PAYLOAD_BYTES:
        raise NoPlan()
    value=json.loads(raw,object_pairs_hook=_strict_pairs,parse_constant=_reject_constant)
    if len(value['operations'])!=1:raise NoPlan()
    op=value['operations'][0]
    if op['kind']!='list' or 'ampm' not in op['filterEvidence']:raise NoPlan()
    sanitized=copy.deepcopy(value)
    del sanitized['operations'][0]['filterEvidence']['ampm']
    if model_envelope(dump(sanitized))!='list':raise NoPlan()
    plan=list_correction_history(turns,trusted)
    ss=plan['operations'][0]['filterEvidence'].get('time',[])
    if [s['status'] for s in ss]!=['superseded','correction_marker','adopted']:
        raise NoPlan()
    proposed=dump(plan)
    _validate(proposed,turns,reference)
    return proposed

def apply_rules(raw,*,user_turns,reference,server_time_trusted):
    if has_alternative_choice(user_turns):
        exc=ClarificationRequired('alternative_choice_needs_clarification')
        exc.question='여러 후보 중 어떤 일정이나 값을 뜻하는지 확인이 필요해요. 최종 대상과 원하는 변경 내용을 하나로 정해 다시 말씀해 주세요.'
        raise exc
    try:
        repaired=repair_query_ampm(raw,user_turns,reference,server_time_trusted)
    except (ValueError,KeyError,TypeError,IndexError,AttributeError,RecursionError):
        repaired=None
    result=previous(repaired if repaired is not None else raw,user_turns=user_turns,reference=reference,server_time_trusted=server_time_trusted)
    reasons=(('source_verified_query_time_slot_repair',) if repaired is not None else ())+tuple(result.reasons)
    return Result(result.raw,result.raw!=raw,reasons)
