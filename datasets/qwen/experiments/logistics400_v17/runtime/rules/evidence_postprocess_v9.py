"""Narrow source-grammar repairs over v8; no case IDs, labels or model calls."""
import copy
import json
import re
from dataclasses import dataclass
from evidence_postprocess_v8 import apply_rules as apply_v8,TITLE,COMPLETE,AMBIGUOUS
from evidence_postprocess_v7 import scope,all_single
from evidence_postprocess_v5 import CATEGORY
from evidence_postprocess_v3 import DATE,TIME,DELETE,CREATE,END,span,dump
from category_postprocess import CUES,_validate,_strict_pairs,_reject_constant,MAX_PAYLOAD_BYTES

VERSION='evidence-rules-v9'
@dataclass(frozen=True)
class Result:
    raw:str
    changed:bool
    reasons:tuple[str,...]
    version:str=VERSION

def create_category_wrapper(value,turns,reference):
    if list(turns)!=[1,2]:return None
    op=value['operations'][0];c=op['changeEvidence']
    if op['kind']!='create' or op['targetEvidence'] or op['filterEvidence'] or set(c)!={'title','date','time','category'}:return None
    title=c['title'][0]['text']
    first=re.fullmatch(rf'\s*(?P<date>{DATE}) (?P<time>{TIME})(?:에)? {re.escape(title)}(?:을|를)? {CREATE}{END}',turns[1])
    second=re.fullmatch(rf'\s*분류는 (?P<category>{"|".join(CUES)})(?:으)?로 해\s*(?:줘|주세요){END}',turns[2])
    if not first or not second:return None
    if c['title']!=span(title) or c['date']!=span(first['date']) or c['time']!=span(first['time']):return None
    if c['category']!=span('분류는 '+second['category'],2):return None
    _validate(dump(value),turns,reference)
    c['category']=span(second['category'],2);return 'explicit_create_category_wrapper'

def missing_change_request(value,turns,reference):
    if list(turns)!=[1]:return None
    op=value['operations'][0];t=op['targetEvidence']
    if op['kind']!='update' or set(t)!={'title'} or op['changeEvidence'] or op['filterEvidence']:return None
    if value['missingInformation'] or op['missingInformation']:return None
    title=t['title'][0]['text']
    if t['title']!=span(title) or not re.fullmatch(rf'\s*{re.escape(title)}(?:을|를) 수정해\s*(?:줘|주세요){END}',turns[1]):return None
    _validate(dump(value),turns,reference)
    op['missingInformation']=['change_value'];value['missingInformation']=['change_value']
    return 'explicit_update_missing_change_value'

def rename_followup_wrapper(value,turns,reference):
    if list(turns)!=[1,2]:return None
    op=value['operations'][0];t=op['targetEvidence'];c=op['changeEvidence']
    if op['kind']!='update' or set(t)!={'title'} or set(c)!={'title'} or op['filterEvidence']:return None
    old=t['title'][0]['text']
    if t['title']!=span(old) or not re.fullmatch(rf'\s*{re.escape(old)} 제목을 수정해\s*(?:줘|주세요){END}',turns[1]):return None
    m=re.fullmatch(rf'\s*이름은 (?P<title>.+?)(?:으로|로) 해\s*(?:줘|주세요){END}',turns[2])
    if not m or not re.fullmatch(TITLE,m['title']):return None
    if c['title']!=span('이름은 '+m['title'],2):return None
    _validate(dump(value),turns,reference)
    c['title']=span(m['title'],2);return 'explicit_rename_followup_wrapper'

def completion_target_clock(value,turns,reference):
    if list(turns)!=[1,2]:return None
    op=value['operations'][0];t=op['targetEvidence'];c=op['changeEvidence']
    if op['kind']!='update' or op['filterEvidence'] or set(t) not in ({'title'},{'title','date'}) or set(c) not in ({'completed'},{'completed','time'}):return None
    title=t['title'][0]['text']
    first=re.fullmatch(rf'\s*(?:(?P<date>{DATE}) )?{re.escape(title)}(?:을|를) (?P<cue>{COMPLETE}){END}',turns[1])
    second=re.fullmatch(rf'\s*(?:시각은 (?P<clock1>{TIME})이야|(?P<clock2>{TIME})에 있는 걸 말하는 거야){END}',turns[2])
    if not first or not second or t['title']!=span(title) or c['completed']!=span(first['cue']):return None
    if first['date']:
        if t.get('date')!=span(first['date']):return None
    elif 'date' in t:return None
    clock=span(second['clock1'] or second['clock2'],2)
    if 'time' in c and c['time']!=clock:return None
    _validate(dump(value),turns,reference)
    c.pop('time',None);t['time']=clock
    return 'explicit_completion_target_clock_role'

def completion_target_category_wrapper(value,turns,reference):
    if list(turns)!=[1,2]:return None
    op=value['operations'][0];t=op['targetEvidence'];c=op['changeEvidence']
    if op['kind']!='update' or op['filterEvidence'] or set(t)!={'title','category'} or set(c)!={'completed'}:return None
    title=t['title'][0]['text']
    first=re.fullmatch(rf'\s*{re.escape(title)}(?:을|를) (?P<cue>{COMPLETE}){END}',turns[1])
    second=re.fullmatch(rf'\s*(?P<category>{CATEGORY}) 걸 말하는 거야{END}',turns[2])
    if not first or not second or t['title']!=span(title) or c['completed']!=span(first['cue']):return None
    if t['category']!=span(second['category']+' 걸 말하는',2):return None
    _validate(dump(value),turns,reference)
    t['category']=span(second['category'],2);return 'explicit_completion_target_category_wrapper'

def dated_delete_source_title(value,turns,reference):
    if list(turns)!=[1]:return None
    op=value['operations'][0];t=op['targetEvidence']
    if op['kind']!='delete' or op['changeEvidence'] or op['filterEvidence'] or set(t)!={'title','date'}:return None
    old=t['title'][0]
    if old['turn']!=1 or old['text'] in turns[1]:return None
    m=re.fullmatch(rf'\s*(?P<date>{DATE}) (?P<title>{TITLE}) 일정(?:을)? {DELETE}{END}',turns[1])
    if not m or t['date']!=span(m['date']):return None
    title=m['title']
    if re.search(r'분류|카테고리|완료|\d|일정',title):return None
    if len(old['text'].split())<3 or len(title.split())<3:return None
    if old['text'].split()[0]!=title.split()[0] or old['text'].split()[-1]!=title.split()[-1]:return None
    t['title']=span(title);return 'explicit_dated_delete_source_title'

RULES=(create_category_wrapper,missing_change_request,rename_followup_wrapper,completion_target_clock,completion_target_category_wrapper,dated_delete_source_title)

def apply_rules(raw,*,user_turns,reference,server_time_trusted):
    previous=apply_v8(raw,user_turns=user_turns,reference=reference,server_time_trusted=server_time_trusted)
    current=previous.raw;reasons=list(previous.reasons)
    try:
        if not isinstance(current,str) or len(current.encode())>MAX_PAYLOAD_BYTES:raise ValueError('payload')
        value=json.loads(current,object_pairs_hook=_strict_pairs,parse_constant=_reject_constant)
        if not scope(value,user_turns) or not all_single(value['operations'][0]) or any(AMBIGUOUS.search(t) for t in user_turns.values()):return Result(current,current!=raw,tuple(reasons))
        for rule in RULES:
            candidate=copy.deepcopy(value)
            try:
                reason=rule(candidate,user_turns,reference)
                if not reason:continue
                proposed=dump(candidate);_validate(proposed,user_turns,reference)
                following=apply_v8(proposed,user_turns=user_turns,reference=reference,server_time_trusted=server_time_trusted)
                _validate(following.raw,user_turns,reference)
                current=following.raw;value=json.loads(current);reasons.extend([reason,*following.reasons])
            except (ValueError,KeyError,TypeError,IndexError,AttributeError):continue
    except (ValueError,KeyError,TypeError,IndexError,AttributeError,UnicodeError,RecursionError):pass
    return Result(current,current!=raw,tuple(reasons))
