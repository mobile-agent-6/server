"""Source-grammar repairs on frozen v5. No evaluation IDs, labels or model calls.

Only single-turn, single-operation commands with exact existing title/evidence
qualify. Backend validation/clarification/confirmation remains required.
"""
import copy
import json
import re
from dataclasses import dataclass
from .evidence_postprocess_v5 import apply_rules as apply_v5,BLOCK,QUERY,CATEGORY
from .evidence_postprocess_v3 import DATE,TIME,CREATE,CHANGE,END,dump,single,span
from .category_postprocess import _validate,_strict_pairs,_reject_constant,MAX_PAYLOAD_BYTES
from app.modules.assistant.evidence.normalize import resolve_date,resolve_time,resolve_category,DatePurpose,DateStatus,TimeStatus,mentions_date_or_time

VERSION='evidence-rules-v6'
@dataclass(frozen=True)
class Result:
    raw:str
    changed:bool
    reasons:tuple[str,...]
    version:str=VERSION

def scope(value,turns):
    if list(turns)!=[1] or BLOCK.search(turns[1]):return False
    if len(value['operations'])!=1 or value['unsupportedEvidence']:return False
    op=value['operations'][0]
    if op['unsupportedEvidence']:return False
    for role in ('targetEvidence','changeEvidence','filterEvidence'):
        for spans in op[role].values():
            if not isinstance(spans,list) or len(spans)!=1:return False
            s=spans[0]
            if not isinstance(s,dict) or set(s)!={'turn','text','status'} or type(s['turn']) is not int or s['turn']!=1:return False
    return True

def adopted(op,except_slot=None):
    return all(single(v) for role in ('targetEvidence','changeEvidence','filterEvidence') for k,v in op[role].items() if (role,k)!=except_slot)

def existing_title(op,role,text):
    spans=op[role].get('title')
    if not single(spans):return None
    title=spans[0]['text']
    return title if title and text.count(title)==1 else None

def missing_time(value,text,reference):
    op=value['operations'][0];change=op['changeEvidence']
    if op['kind']!='create' or op['targetEvidence'] or op['filterEvidence']:return None
    if set(change)-{'title','date','time','category'} or not {'title','date','time'}<=set(change):return None
    if not adopted(op,('changeEvidence','time')):return None
    title=existing_title(op,'changeEvidence',text)
    if not title or mentions_date_or_time(title):return None
    old=change['time'][0]
    # Discard only a non-time adopted fragment or an invented correction marker.
    if old['status']=='correction_marker':
        if old['text'] in text:return None
    elif old['status']=='adopted':
        if resolve_time([old['text']]).status!=TimeStatus.NOT_A_TIME:return None
    else:return None
    m=re.fullmatch(rf'\s*(?P<date>{DATE})(?:에는|에)?\s+{re.escape(title)}(?:을|를)?\s*(?:일정(?:에)?\s*)?{CREATE}{END}',text)
    if not m or change['date']!=span(m['date']):return None
    del change['time']
    # Everything outside the removed erroneous slot must already be valid.
    _validate(dump(value),{1:text},reference)
    return 'missing_time_from_complete_create_request'

def update_destination(value,text,reference):
    op=value['operations'][0];target=op['targetEvidence'];change=op['changeEvidence']
    if op['kind']!='update' or op['filterEvidence'] or not adopted(op):return None
    if set(target)-{'title','date','time'} or set(change)-{'date','time'}:return None
    if not ({'date','time'} & set(target)):return None
    title=existing_title(op,'targetEvidence',text)
    if not title:return None
    m=re.fullmatch(rf'\s*{re.escape(title)}(?:을|를)?\s*(?:일정(?:을|를)?\s*)?(?P<date>{DATE})(?:\s+(?P<time>{TIME}))?(?:으)?로\s*{CHANGE}{END}',text)
    if not m:return None
    if resolve_date([m['date']],reference,purpose=DatePurpose.SCHEDULE).status not in {DateStatus.RESOLVED,DateStatus.PAST_WITHOUT_YEAR,DateStatus.WEEKDAY_IS_TODAY}:return None
    if m['time'] and not resolve_time([m['time']]).resolved:return None
    for key in ('date','time'):
        captured=m[key]
        if captured and text.count(captured)!=1:return None
        if key in target or key in change:
            if not captured:return None
            if key in target and target[key]!=span(captured):return None
            if key in change and change[key]!=span(captured):return None
    for key in ('date','time'):
        if m[key]:
            target.pop(key,None);change[key]=span(m[key])
    return 'explicit_update_destination_roles'

def completion_release(value,text,reference):
    op=value['operations'][0]
    if op['kind']!='update' or op['filterEvidence'] or set(op['targetEvidence'])!={'title'} or set(op['changeEvidence'])!={'completed'} or not adopted(op):return None
    title=existing_title(op,'targetEvidence',text)
    if not title:return None
    old=op['changeEvidence']['completed'][0]['text']
    if not re.fullmatch(r'(?:표시를\s*)?해제해\s*(?:줘|주세요)',old):return None
    m=re.fullmatch(rf'\s*{re.escape(title)}(?:의)?\s+(?P<cue>완료\s*표시를\s*해제해\s*(?:줘|주세요)){END}',text)
    if not m or old not in m['cue'] or text.count(old)!=1:return None
    op['changeEvidence']['completed']=span(m['cue'])
    return 'complete_completion_release_cue'

def time_only_query(value,text,reference):
    op=value['operations'][0];filters=op['filterEvidence']
    if op['kind']!='list' or op['targetEvidence'] or op['changeEvidence'] or set(filters)!={'title'} or not adopted(op):return None
    m=re.fullmatch(rf'\s*(?P<time>{TIME})(?:에)?\s+일정(?:만|을)?\s+{QUERY}{END}',text)
    if not m or filters['title']!=span(m['time']) or not resolve_time([m['time']]).resolved:return None
    filters['time']=filters.pop('title')
    return 'explicit_time_only_query_role'

def dated_category_query(value,text,reference):
    op=value['operations'][0];filters=op['filterEvidence']
    if op['kind']!='list' or op['targetEvidence'] or op['changeEvidence'] or set(filters)!={'title','date'} or not adopted(op):return None
    m=re.fullmatch(rf'\s*(?P<date>{DATE})(?:에)?\s+(?P<category>{CATEGORY})\s+일정(?:\s*목록)?(?:을|만)?\s+{QUERY}{END}',text)
    if not m or filters['date']!=span(m['date']) or filters['title']!=span(m['category']):return None
    if resolve_category([m['category']]) is None:return None
    _validate(dump(value),{1:text},reference)
    filters['category']=filters.pop('title')
    return 'explicit_dated_category_query_role'

RULES=(missing_time,update_destination,completion_release,time_only_query,dated_category_query)

def apply_rules(raw,*,user_turns,reference,server_time_trusted):
    previous=apply_v5(raw,user_turns=user_turns,reference=reference,server_time_trusted=server_time_trusted)
    current=previous.raw;reasons=list(previous.reasons)
    try:
        if not isinstance(current,str) or len(current.encode())>MAX_PAYLOAD_BYTES:raise ValueError('payload')
        value=json.loads(current,object_pairs_hook=_strict_pairs,parse_constant=_reject_constant)
        if not scope(value,user_turns):return Result(current,current!=raw,tuple(reasons))
        for rule in RULES:
            candidate=copy.deepcopy(value)
            try:
                reason=rule(candidate,user_turns[1],reference)
                if not reason:continue
                _validate(dump(candidate),user_turns,reference)
                following=apply_v5(dump(candidate),user_turns=user_turns,reference=reference,server_time_trusted=server_time_trusted)
                _validate(following.raw,user_turns,reference)
                current=following.raw;value=json.loads(current);reasons.extend([reason,*following.reasons])
            except (ValueError,KeyError,TypeError,IndexError,AttributeError):continue
    except (ValueError,KeyError,TypeError,IndexError,AttributeError,UnicodeError,RecursionError):pass
    return Result(current,current!=raw,tuple(reasons))
