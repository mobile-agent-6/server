"""Four narrow source-grounded repairs; no gold, IDs or runtime model access.

Normal backend validation/clarification/confirmation remains mandatory.
Every change requires a complete supported source command and contract validation.
"""
import copy
import json
import re
from dataclasses import dataclass
from .evidence_postprocess_v4 import apply_rules as apply_v4
from .evidence_postprocess_v3 import DATE,TIME,CHANGE,DELETE,END,dump,single,span
from .category_postprocess import CUES,_validate,_strict_pairs,_reject_constant,MAX_PAYLOAD_BYTES
from app.modules.assistant.evidence.normalize import resolve_date,resolve_time,resolve_category,DatePurpose

VERSION='evidence-rules-v5'
BLOCK=re.compile(r'[?？"\'“”‘’\n\r]|아니|말고|대신|취소|또는|혹은|부터|까지|매주|매일|하지\s*(?:마|말)')
QUERY=r'(?:보여|조회해)\s*(?:줘|주세요)'
CATEGORY=rf'(?:분류|카테고리)가\s*(?:{"|".join(CUES)})인'

@dataclass(frozen=True)
class Result:
    raw:str
    changed:bool
    reasons:tuple[str,...]
    version:str=VERSION

def simple(value,turns):
    if list(turns)!=[1] or BLOCK.search(turns[1]):return False
    if len(value['operations'])!=1 or value['unsupportedEvidence']:return False
    op=value['operations'][0]
    if op['unsupportedEvidence']:return False
    for role in ('targetEvidence','changeEvidence','filterEvidence'):
        for spans in op[role].values():
            if not single(spans) or spans[0]['turn']!=1 or set(spans[0])!={'turn','text','status'}:return False
    return True

def update_target_date(value,text,reference):
    op=value['operations'][0];target=op['targetEvidence'];change=op['changeEvidence']
    if op['kind']!='update' or set(target)!={'title'} or set(change)!={'time'} or op['filterEvidence']:return None
    title=target['title'][0]['text'];newtime=change['time'][0]['text']
    if text.count(title)!=1:return None
    m=re.fullmatch(rf'\s*(?P<date>{DATE})\s+{re.escape(title)}(?:의)?\s+시간(?:을|은)\s+(?P<time>{TIME})(?:으)?로\s*{CHANGE}{END}',text)
    if not m or m['time']!=newtime:return None
    if text.count(m['date'])!=1 or not resolve_time([newtime]).resolved:return None
    if not resolve_date([m['date']],reference,purpose=DatePurpose.SEARCH).resolved:return None
    _validate(dump(value),{1:text},reference)
    target['date']=span(m['date'])
    return 'explicit_date_of_time_update_target'

def merge_query_weekday(value,text,reference):
    op=value['operations'][0];filters=op['filterEvidence']
    if op['kind']!='list' or op['targetEvidence'] or op['changeEvidence'] or set(filters)!={'date','day'}:return None
    week=filters['date'][0]['text'];day=filters['day'][0]['text']
    if not re.fullmatch(r'(?:이번|다음|다다음)\s*주',week) or not re.fullmatch(r'[월화수목금토일]요일',day):return None
    m=re.fullmatch(rf'\s*(?P<date>{re.escape(week)}\s+{re.escape(day)})(?:의)?\s+일정(?:\s*목록)?(?:을)?\s+{QUERY}{END}',text)
    if not m or not resolve_date([m['date']],reference,purpose=DatePurpose.SEARCH).resolved:return None
    filters['date']=span(m['date']);del filters['day']
    return 'merge_split_weekday_query'

def category_query(value,text,reference):
    op=value['operations'][0];filters=op['filterEvidence']
    if op['kind']!='list' or op['targetEvidence'] or op['changeEvidence'] or set(filters)!={'title'}:return None
    m=re.fullmatch(rf'\s*(?P<category>{CATEGORY})\s+일정(?:\s*목록)?(?:을)?\s+{QUERY}{END}',text)
    if not m or filters['title'][0]['text']!=m['category'] or resolve_category([m['category']]) is None:return None
    _validate(dump(value),{1:text},reference)
    filters['category']=filters.pop('title')
    return 'explicit_category_only_query'

def delete_title_spacing(value,text,reference):
    op=value['operations'][0];target=op['targetEvidence']
    if op['kind']!='delete' or op['changeEvidence'] or op['filterEvidence'] or set(target)!={'title','category'}:return None
    old=target['title'][0]['text'];category=target['category'][0]['text']
    if not re.fullmatch(CATEGORY,category) or resolve_category([category]) is None:return None
    # Existing exact spans are preserved even if another source interpretation is possible.
    if old in text:return None
    m=re.fullmatch(rf'\s*{re.escape(category)}\s+(?P<title>[가-힣A-Za-z0-9][가-힣A-Za-z0-9 ]*?)\s+일정(?:을)?\s+{DELETE}{END}',text)
    if not m:return None
    title=m['title']
    if not re.fullmatch(r'[가-힣A-Za-z0-9 ]+',old):return None
    if old.replace(' ','')!=title.replace(' ',''):return None
    # The same character sequence must map to one contiguous source span only.
    pattern=' *'.join(map(re.escape,old.replace(' ','')))
    matches=list(re.finditer(rf'(?=({pattern}))',text))
    if len(matches)!=1 or matches[0].span(1)!=m.span('title'):return None
    target['title']=span(title)
    return 'unique_source_spacing_in_explicit_delete'

RULES=(update_target_date,merge_query_weekday,category_query,delete_title_spacing)

def apply_rules(raw,*,user_turns,reference,server_time_trusted):
    previous=apply_v4(raw,user_turns=user_turns,reference=reference,server_time_trusted=server_time_trusted)
    current=previous.raw;reasons=list(previous.reasons)
    try:
        if not isinstance(current,str) or len(current.encode())>MAX_PAYLOAD_BYTES:raise ValueError('payload')
        value=json.loads(current,object_pairs_hook=_strict_pairs,parse_constant=_reject_constant)
        if not simple(value,user_turns):return Result(current,current!=raw,tuple(reasons))
        for rule in RULES:
            candidate=copy.deepcopy(value)
            try:
                reason=rule(candidate,user_turns[1],reference)
                if not reason:continue
                proposed=dump(candidate)
                _validate(proposed,user_turns,reference)
                current=proposed;value=candidate;reasons.append(reason)
            except (ValueError,KeyError,TypeError,IndexError,AttributeError):continue
    except (ValueError,KeyError,TypeError,IndexError,AttributeError,UnicodeError,RecursionError):pass
    return Result(current,current!=raw,tuple(reasons))
