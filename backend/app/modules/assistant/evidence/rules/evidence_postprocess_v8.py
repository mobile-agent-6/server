"""Explicit title and condition repairs. No labels, case IDs or model access."""
import copy
import json
import re
from dataclasses import dataclass
from .evidence_postprocess_v7 import apply_rules as apply_v7,scope,all_single
from .evidence_postprocess_v5 import CATEGORY
from .evidence_postprocess_v3 import DATE,TIME,DELETE,END,span,dump
from .category_postprocess import _validate,_strict_pairs,_reject_constant,MAX_PAYLOAD_BYTES

VERSION='evidence-rules-v8'
TITLE=r'[가-힣A-Za-z0-9]+(?: [가-힣A-Za-z0-9]+)*'
COMPLETE=r'(?:미완료로 (?:되돌려|바꿔)|완료로 바꿔)\s*(?:줘|주세요)'
AMBIGUOUS=re.compile(r'아니|말고|대신|부터|까지|매일|매주|또는|혹은|그리고|(?:하지|지)\s*(?:마|말)')

@dataclass(frozen=True)
class Result:
    raw:str
    changed:bool
    reasons:tuple[str,...]
    version:str=VERSION

def completed_title(value,turns,reference):
    if list(turns)!=[1]:return None
    op=value['operations'][0];t=op['targetEvidence'];c=op['changeEvidence']
    if op['kind']!='update' or op['filterEvidence'] or set(t)!={'title','completedState'} or set(c)!={'completed'}:return None
    m=re.fullmatch(rf'\s*(?P<state>완료된|미완료인) (?P<title>{TITLE})(?:을|를) (?P<cue>{COMPLETE}){END}',turns[1])
    if not m or t['completedState']!=span(m['state']) or c['completed']!=span(m['cue']):return None
    old=t['title'][0]
    if old['turn']!=1 or old['text']==m['title'] or not m['title'].endswith(' '+old['text']):return None
    _validate(dump(value),turns,reference)
    t['title']=span(m['title']);return 'explicit_completed_target_title_boundary'

def query_title_suffix(value,turns,reference):
    if list(turns)!=[1,2]:return None
    op=value['operations'][0];f=op['filterEvidence']
    if op['kind']!='list' or op['targetEvidence'] or op['changeEvidence'] or set(f)!={'title'}:return None
    if not re.fullmatch(rf'\s*일정 목록을 보고 싶어{END}',turns[1]):return None
    m=re.fullmatch(rf'\s*제목이 (?P<title>{TITLE})인 걸 찾아\s*(?:줘|주세요){END}',turns[2])
    if not m or f['title']!=span(m['title']+'인',2):return None
    _validate(dump(value),turns,reference)
    f['title']=span(m['title'],2);return 'explicit_query_title_particle_boundary'

def time_query_placeholder(value,turns,reference):
    if list(turns)!=[1]:return None
    op=value['operations'][0];f=op['filterEvidence']
    if op['kind']!='list' or op['targetEvidence'] or op['changeEvidence'] or set(f)!={'title','time'}:return None
    m=re.fullmatch(rf'\s*(?P<time>{TIME})에 잡힌 일정이 뭔지 보여\s*(?:줘|주세요){END}',turns[1])
    if not m or f['time']!=span(m['time']) or f['title']!=span('잡힌'):return None
    _validate(dump(value),turns,reference)
    del f['title'];return 'explicit_time_query_has_no_title'

def rename_destination(value,turns,reference):
    if list(turns)!=[1]:return None
    op=value['operations'][0];t=op['targetEvidence'];c=op['changeEvidence']
    if op['kind']!='update' or op['filterEvidence'] or set(t)!={'title'} or set(c)!={'title','category'}:return None
    old=t['title'][0]['text']
    m=re.fullmatch(rf'\s*{re.escape(old)} 제목을 (?P<title>{TITLE})(?:으)?로 수정해\s*(?:줘|주세요){END}',turns[1])
    if not m or t['title']!=span(old):return None
    left=c['title'][0];right=c['category'][0]
    if left['turn']!=1 or right['turn']!=1 or left['text']+' '+right['text']!=m['title']:return None
    c['title']=span(m['title']);del c['category']
    return 'explicit_rename_destination_is_whole_title'

def delete_target_date(value,turns,reference):
    if list(turns)!=[1,2]:return None
    op=value['operations'][0];t=op['targetEvidence']
    if op['kind']!='delete' or op['changeEvidence'] or op['filterEvidence'] or set(t)!={'title'}:return None
    m=re.fullmatch(rf'\s*(?P<date>{DATE}) 일정 하나 {DELETE}{END}',turns[1])
    title=t['title'][0]['text']
    if not m or t['title']!=span(title,2):return None
    if not re.fullmatch(rf'\s*{re.escape(title)}라는 제목이야{END}',turns[2]):return None
    _validate(dump(value),turns,reference)
    t['date']=span(m['date']);return 'retain_explicit_delete_target_date'

def completed_target_time(value,turns,reference):
    if list(turns)!=[1,2]:return None
    op=value['operations'][0];t=op['targetEvidence'];c=op['changeEvidence']
    if op['kind']!='update' or op['filterEvidence'] or set(t)!={'title'} or set(c)!={'completed'}:return None
    title=t['title'][0]['text']
    m=re.fullmatch(rf'\s*{re.escape(title)}(?:을|를) (?P<cue>{COMPLETE}){END}',turns[1])
    clock=re.fullmatch(rf'\s*시각은 (?P<time>{TIME})이야{END}',turns[2])
    if not m or not clock or t['title']!=span(title) or c['completed']!=span(m['cue']):return None
    _validate(dump(value),turns,reference)
    t['time']=span(clock['time'],2);return 'explicit_completion_target_time'

def category_delete_title(value,turns,reference):
    if list(turns)!=[1]:return None
    op=value['operations'][0];t=op['targetEvidence']
    if op['kind']!='delete' or op['changeEvidence'] or op['filterEvidence'] or set(t)!={'title','category'}:return None
    old=t['title'][0]
    if old['turn']!=1 or old['text'] in turns[1]:return None
    m=re.fullmatch(rf'\s*(?P<category>{CATEGORY}) (?P<title>{TITLE}) 일정을 {DELETE}{END}',turns[1])
    if not m or t['category']!=span(m['category']):return None
    # Keep conservative anchors; source grammar, never spelling substitutions, determines title.
    if len(old['text'].split())<2 or len(m['title'].split())<2:return None
    if old['text'][0]!=m['title'][0] or old['text'].split()[-1]!=m['title'].split()[-1]:return None
    t['title']=span(m['title']);return 'explicit_category_delete_source_title'

RULES=(completed_title,query_title_suffix,time_query_placeholder,rename_destination,delete_target_date,completed_target_time,category_delete_title)

def apply_rules(raw,*,user_turns,reference,server_time_trusted):
    previous=apply_v7(raw,user_turns=user_turns,reference=reference,server_time_trusted=server_time_trusted)
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
                following=apply_v7(proposed,user_turns=user_turns,reference=reference,server_time_trusted=server_time_trusted)
                _validate(following.raw,user_turns,reference)
                current=following.raw;value=json.loads(current);reasons.extend([reason,*following.reasons])
            except (ValueError,KeyError,TypeError,IndexError,AttributeError):continue
    except (ValueError,KeyError,TypeError,IndexError,AttributeError,UnicodeError,RecursionError):pass
    return Result(current,current!=raw,tuple(reasons))
