"""Narrow source-grammar repairs on top of frozen v2. No case IDs or gold lookup.

Not a write authorization: normal backend validation, clarification and confirmation
must still run. Only entire supported command forms qualify; ambiguous forms pass.
"""
import copy
import json
import re
from dataclasses import dataclass
from .evidence_postprocess import apply_evidence_rules as apply_v2
from .category_postprocess import _strict_pairs,_reject_constant,_validate,MAX_PAYLOAD_BYTES,CUES
from app.modules.assistant.evidence.normalize import resolve_date,resolve_time,DatePurpose,resolve_category

VERSION='evidence-rules-v3'
DATE=r'(?:\d{4}년\s*\d{1,2}월\s*\d{1,2}일|\d{1,2}월\s*\d{1,2}일|(?:이번|다음|다다음)\s*주\s*[월화수목금토일]요일|오늘|내일|모레|글피)'
TIME=r'(?:(?:오전|오후)\s*)?\d{1,2}시(?:\s*(?:\d{1,2}분|반))?'
CREATE=r'(?:잡아|등록해|넣어)\s*(?:줘|주세요)'
CHANGE=r'(?:변경해|바꿔|옮겨)\s*(?:줘|주세요)'
DELETE=r'(?:삭제해|지워)\s*(?:줘|주세요)'
END=r'\s*[.!。]?\s*'
BLOCK=re.compile(r'아니|말고|대신|취소|또는|혹은|부터|까지|매주|매일|[?？"\'“”‘’]|(?:하지|지)\s*(?:마|말)|안\s*(?:잡|등록|넣)|보여|조회|알려|있어|있나요|확인해')

@dataclass(frozen=True)
class Result:
    raw:str
    changed:bool
    reasons:tuple[str,...]
    version:str=VERSION

def dump(v):return json.dumps(v,ensure_ascii=False,separators=(',',':'))
def single(spans):
    return isinstance(spans,list) and len(spans)==1 and isinstance(spans[0],dict) and spans[0].get('status')=='adopted'
def span(text,turn=1):return [{'turn':turn,'text':text,'status':'adopted'}]
def title(op,role,turns):
    values=op[role].get('title')
    if not single(values):return None
    value=values[0]
    if value.get('occurrence') is not None or value['turn']!=1:return None
    text=value['text']
    if not text or turns[1].count(text)!=1:return None
    return text
def simple(value,turns):
    ops=value['operations']
    if len(ops)!=1 or value['unsupportedEvidence'] or ops[0]['unsupportedEvidence']:return False
    return all(single(spans) for role in ('targetEvidence','changeEvidence','filterEvidence') for spans in ops[0][role].values())
def temporal_prefix():
    return rf'(?:(?P<date>{DATE})(?:에|에는)?\s*)?(?:(?P<time>{TIME})(?:에|에는)?\s*)?'

def explicit_create(value,turns,reference):
    op=value['operations'][0]
    if op['kind']!='list' or len(turns)>2 or op['targetEvidence'] or op['changeEvidence']:return None
    if set(op['filterEvidence'])-{'title','date','time','category'}:return None
    t=title(op,'filterEvidence',turns)
    if not t or any(BLOCK.search(x) for x in turns.values()):return None
    # First utterance must be a full date[/time] + exact extracted title + direct command.
    match=re.fullmatch(rf'\s*(?P<date>{DATE})(?:에|에는)?\s*(?:(?P<time>{TIME})(?:에|에는)?\s*)?{re.escape(t)}(?:을|를)?\s*(?:일정(?:으로|에)?\s*)?{CREATE}{END}',turns[1])
    if not match:return None
    if len(turns)==2:
        # Only a complete date/time clarification is allowed after the create request.
        if not re.fullmatch(rf'\s*{DATE}\s+{TIME}(?:로|으로|야|요)?{END}',turns[2]):return None
        for field in ('date','time'):
            if not single(op['filterEvidence'].get(field)) or op['filterEvidence'][field][0]['turn']!=2:return None
    _validate(dump(value),turns,reference)
    op['kind']='create';op['changeEvidence']=op['filterEvidence'];op['filterEvidence']={}
    return 'explicit_create_command'

def category_date(value,turns,reference):
    op=value['operations'][0]
    if len(turns)!=1 or op['kind']!='update' or re.search('분류|카테고리',turns[1]):return None
    ch=op['changeEvidence'];t=title(op,'targetEvidence',turns)
    if not t or set(ch)!={'category'}:return None
    old=ch['category'][0]['text']
    if not re.fullmatch(DATE,old) or resolve_category([old]) is not None:return None
    if not re.fullmatch(rf'\s*{re.escape(t)}(?:은|는|을|를)?\s*(?:일정\s*)?(?:날짜(?:를|는)?\s*)?{re.escape(old)}(?:으)?로\s*{CHANGE}{END}',turns[1]):return None
    if not resolve_date([old],reference,purpose=DatePurpose.SCHEDULE).resolved:return None
    ch['date']=ch.pop('category');return 'date_misplaced_in_category'

def complete_weekday(value,turns,reference):
    if len(turns)!=1:return None
    _validate(dump(value),turns,reference)
    changed=False
    for role in ('targetEvidence','changeEvidence','filterEvidence'):
        evidence=value['operations'][0][role];spans=evidence.get('date')
        if not single(spans) or spans[0]['turn']!=1 or spans[0].get('occurrence') is not None:continue
        old=spans[0]['text']
        if not re.fullmatch(r'(?:이번|다음|다다음)\s*주',old):continue
        if turns[1].count(old)!=1:continue
        m=re.search(re.escape(old)+r'\s*[월화수목금토일]요일(?![가-힣]|\s*(?:부터|까지|또는|이나))',turns[1])
        # Korean case particles may follow a weekday; handle only a short allowlist.
        if not m:
            m=re.search(re.escape(old)+r'\s*[월화수목금토일]요일(?=(?:에|로|은|는)?(?:\s|$))',turns[1])
        if m and resolve_date([m.group()],reference,purpose=DatePurpose.SEARCH if role!='changeEvidence' else DatePurpose.SCHEDULE).resolved:
            spans[0]['text']=m.group();changed=True
    return 'complete_weekday_phrase' if changed else None

def explicit_category(value,turns,reference):
    op=value['operations'][0]
    if len(turns)!=1 or op['kind']!='update' or set(op['changeEvidence'])!={'category'}:return None
    t=title(op,'targetEvidence',turns)
    if not t:return None
    cue='|'.join(CUES)
    m=re.fullmatch(rf'\s*{re.escape(t)}(?:의)?\s*(?:일정\s*)?(?:분류|카테고리)(?:를|는|은)?\s*(?P<cue>{cue})(?:으)?로\s*{CHANGE}{END}',turns[1])
    if not m:return None
    existing=op['changeEvidence']['category']
    if all(s['text'] in turns[s['turn']] for s in existing):return None
    stripped=copy.deepcopy(value);stripped['operations'][0]['changeEvidence']={}
    _validate(dump(stripped),turns,reference)
    op['changeEvidence']['category']=span(m['cue']);return 'explicit_category_source_span'

def target_temporal(value,turns,reference):
    op=value['operations'][0]
    if len(turns)!=1 or op['kind'] not in ('delete','update'):return None
    t=title(op,'targetEvidence',turns)
    if not t or set(op['targetEvidence'])-{'title','date','time'}:return None
    if op['kind']=='delete':ending=DELETE
    else:
        if set(op['changeEvidence'])!={'completed'}:return None
        ending=re.escape(op['changeEvidence']['completed'][0]['text'])
    m=re.fullmatch(rf'\s*{temporal_prefix()}{re.escape(t)}(?:을|를|은|는)?\s*(?:일정\s*)?{ending}{END}',turns[1])
    if not m or not (m['date'] or m['time']):return None
    _validate(dump(value),turns,reference)
    changed=False
    for key in ('date','time'):
        captured=m[key]
        if not captured:continue
        if key in op['targetEvidence']:
            if op['targetEvidence'][key]!=span(captured):return None
        else:op['targetEvidence'][key]=span(captured);changed=True
    return 'explicit_target_datetime' if changed else None

def explicit_time_change(value,turns,reference):
    op=value['operations'][0]
    if len(turns)!=1 or op['kind']!='update':return None
    t=title(op,'targetEvidence',turns);ch=op['changeEvidence']
    if not t or 'time' not in ch or set(ch)-{'time','category'}:return None
    m=re.fullmatch(rf'\s*{re.escape(t)}(?:은|는|을|를)?\s*(?:일정\s*)?(?:시간(?:을|은)?\s*)?(?P<time>{TIME})(?:으)?로\s*{CHANGE}{END}',turns[1])
    if not m or not resolve_time([m['time']]).resolved:return None
    desired=span(m['time']);changed=ch['time']!=desired
    ch['time']=desired
    if 'category' in ch:
        c=ch['category'][0]
        if c['turn']!=1 or c.get('occurrence') is not None or c['text'] not in t:return None
        if turns[1].count(c['text'])!=1:return None
        del ch['category'];changed=True
    return 'explicit_time_change_span' if changed else None

RULES=(explicit_create,category_date,complete_weekday,explicit_category,target_temporal,explicit_time_change)

def apply_rules(raw,*,user_turns,reference,server_time_trusted):
    previous=apply_v2(raw,user_turns=user_turns,reference=reference,server_time_trusted=server_time_trusted)
    current=previous.raw;reasons=list(previous.reasons)
    try:
        if not isinstance(current,str) or len(current.encode())>MAX_PAYLOAD_BYTES:raise ValueError('payload')
        value=json.loads(current,object_pairs_hook=_strict_pairs,parse_constant=_reject_constant)
        if not simple(value,user_turns):raise ValueError('outside scope')
        # Broad ambiguity guard on source requests, including all later turns.
        # Status negation (미완료/안 끝난) remains allowed for completed updates.
        if any(BLOCK.search(t) for t in user_turns.values()):raise ValueError('ambiguous source')
        for rule in RULES:
            candidate=copy.deepcopy(value)
            try:
                reason=rule(candidate,user_turns,reference)
                if not reason:continue
                # Re-run the same v2 helpers after structural change, then fully validate.
                following=apply_v2(dump(candidate),user_turns=user_turns,reference=reference,server_time_trusted=server_time_trusted)
                _validate(following.raw,user_turns,reference)
                value=json.loads(following.raw);current=following.raw
                reasons.extend([reason,*following.reasons])
            except (ValueError,KeyError,TypeError,IndexError,AttributeError):continue
    except (ValueError,KeyError,TypeError,IndexError,AttributeError,UnicodeError,RecursionError):pass
    return Result(current,current!=raw,tuple(reasons))
