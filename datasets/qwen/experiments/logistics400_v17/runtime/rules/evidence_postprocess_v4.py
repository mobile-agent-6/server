"""Add source-grounded correction history; never change the model's adopted value.

Only complete, single-turn, single-create commands with an already exact title,
date and time are supported. No model, gold data, case IDs or writes are used.
"""
import copy
import json
import re
from dataclasses import dataclass
from evidence_postprocess_v3 import apply_rules as apply_v3,DATE,TIME,CREATE,END,dump,single
from category_postprocess import _validate,_strict_pairs,_reject_constant,MAX_PAYLOAD_BYTES
from app.modules.assistant.evidence.normalize import resolve_date,resolve_time,DatePurpose,DateStatus

VERSION='evidence-rules-v4'
MARKER=r'말고|아니라|아니고|대신'
EXCLUDE=re.compile(r'[?？"\'“”‘’]|또는|혹은|부터|까지|매주|매일|취소|하지\s*(?:마|말)|안\s*(?:잡|등록|넣)')

@dataclass(frozen=True)
class Result:
    raw:str
    changed:bool
    reasons:tuple[str,...]
    version:str=VERSION

def _date_valid(text,reference):
    return resolve_date([text],reference,purpose=DatePurpose.SCHEDULE).status in {
        DateStatus.RESOLVED,DateStatus.PAST_WITHOUT_YEAR,DateStatus.WEEKDAY_IS_TODAY}

def _history(value,turns,reference):
    if list(turns)!=[1] or len(value['operations'])!=1:return None
    op=value['operations'][0]
    if op['kind']!='create' or op['targetEvidence'] or op['filterEvidence']:return None
    if value['unsupportedEvidence'] or op['unsupportedEvidence']:return None
    change=op['changeEvidence']
    if not {'title','date','time'}<=set(change):return None
    if any(not single(spans) for spans in change.values()):return None
    if any(s[0].get('occurrence') is not None or s[0]['turn']!=1 for s in change.values()):return None
    text=turns[1];title=change['title'][0]['text']
    if EXCLUDE.search(text) or text.count(title)!=1:return None
    markers=list(re.finditer(MARKER,text))
    if len(markers)!=1:return None
    # Full request grammar keeps apparent temporal corrections inside titles,
    # quotes, questions and mixed requests out of this rule.
    patterns={
        'date':rf'\s*(?P<old>{DATE})(?:이|가)?\s*(?P<marker>{MARKER})\s*(?P<new>{DATE})\s+(?P<other>{TIME})(?:에|으로|로)?\s*{re.escape(title)}(?:을|를)?\s*{CREATE}{END}',
        'time':rf'\s*(?P<other>{DATE})(?:에)?\s+(?P<old>{TIME})(?:이|가)?\s*(?P<marker>{MARKER})\s*(?P<new>{TIME})(?:에|으로|로)?\s*{re.escape(title)}(?:을|를)?\s*{CREATE}{END}',
    }
    for slot,pattern in patterns.items():
        match=re.fullmatch(pattern,text)
        if not match:continue
        old,new,marker=match['old'],match['new'],match['marker']
        other='time' if slot=='date' else 'date'
        if change[slot][0]['text']!=new or change[other][0]['text']!=match['other']:continue
        # No inferred occurrence: every historical/new/marker span must be unique
        # and physically ordered in the user's original text.
        if any(text.count(x)!=1 for x in (old,new,marker)):continue
        if not match.end('old')<=match.start('marker')<match.end('marker')<=match.start('new'):continue
        if slot=='date':
            if not all(_date_valid(x,reference) for x in (old,new)):continue
        elif not all(resolve_time([x]).resolved for x in (old,new)):continue
        _validate(dump(value),turns,reference)
        change[slot].extend([
            {'turn':1,'text':old,'status':'superseded'},
            {'turn':1,'text':marker,'status':'correction_marker'}])
        return f'same_turn_{slot}_correction_history'
    return None

def apply_rules(raw,*,user_turns,reference,server_time_trusted):
    previous=apply_v3(raw,user_turns=user_turns,reference=reference,server_time_trusted=server_time_trusted)
    current=previous.raw;reasons=list(previous.reasons)
    try:
        if not isinstance(current,str) or len(current.encode())>MAX_PAYLOAD_BYTES:raise ValueError('payload')
        value=json.loads(current,object_pairs_hook=_strict_pairs,parse_constant=_reject_constant)
        candidate=copy.deepcopy(value)
        reason=_history(candidate,user_turns,reference)
        if reason:
            proposed=dump(candidate)
            _validate(proposed,user_turns,reference)
            current=proposed;reasons.append(reason)
    except (ValueError,KeyError,TypeError,IndexError,AttributeError,UnicodeError,RecursionError):pass
    return Result(current,current!=raw,tuple(reasons))
