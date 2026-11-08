"""Source-grounded repairs over v6; no labels, IDs, model calls or execution.

Each repair must pass the existing backend contract. Ambiguous forms abstain.
Normal backend clarification and confirmation still apply.
"""
import copy
import json
import re
from dataclasses import dataclass
from evidence_postprocess_v6 import apply_rules as apply_v6
from evidence_postprocess_v5 import QUERY
from evidence_postprocess_v3 import DATE,TIME,CHANGE,DELETE,END,dump,single,span
from category_postprocess import _validate,_strict_pairs,_reject_constant,MAX_PAYLOAD_BYTES

VERSION='evidence-rules-v7'
BLOCK=re.compile(r'[?？"\'“”‘’\n\r]|취소|또는|혹은|그리고|하지\s*(?:마|말)')
ROLES=('targetEvidence','changeEvidence','filterEvidence')

@dataclass(frozen=True)
class Result:
    raw:str
    changed:bool
    reasons:tuple[str,...]
    version:str=VERSION

def scope(value,turns):
    if list(turns) not in ([1],[1,2]) or any(BLOCK.search(t) for t in turns.values()):return False
    if set(value)!={'operations','unsupportedEvidence','missingInformation'}:return False
    if len(value['operations'])!=1 or value['unsupportedEvidence']:return False
    op=value['operations'][0]
    if set(op)!={'index','kind',*ROLES,'unsupportedEvidence','missingInformation'} or op['unsupportedEvidence']:return False
    for role in ROLES:
        for values in op[role].values():
            if not isinstance(values,list) or not values:return False
            for s in values:
                if set(s)!={'turn','text','status'} or type(s['turn']) is not int or s['turn'] not in turns:return False
                if not isinstance(s['text'],str) or not s['text']:return False
                if s['status'] not in ('adopted','superseded','correction_marker'):return False
    return True

def all_single(op):
    return all(single(v) for role in ROLES for v in op[role].values())

def unique_source_turn(value,turns,reference):
    if len(turns)!=2:return None
    changed=False
    for role in ROLES:
        for values in value['operations'][0][role].values():
            for s in values:
                if s['text'] in turns[s['turn']]:continue
                matches=[turn for turn,text in turns.items() for _ in range(text.count(s['text']))]
                if len(matches)!=1:return None
                s['turn']=matches[0];changed=True
    return 'unique_exact_source_turn' if changed else None

def future_query(value,turns,reference):
    if list(turns)!=[1]:return None
    op=value['operations'][0];f=op['filterEvidence']
    if op['kind']!='list' or op['targetEvidence'] or op['changeEvidence'] or set(f)!={'title','completed'} or not all_single(op):return None
    title=f['title'][0]['text']
    m=re.fullmatch(rf'\s*(?P<future>앞으로 남은) 일정 중 (?P<state>미완료인|완료된) {re.escape(title)}만\s+{QUERY}{END}',turns[1])
    if not m or f['title']!=span(title) or f['completed']!=span(m['state']):return None
    _validate(dump(value),turns,reference)
    f['futureScope']=span(m['future'])
    return 'explicit_future_query_scope'

def completion_action_not_target(value,turns,reference):
    if list(turns)!=[1,2]:return None
    op=value['operations'][0];t=op['targetEvidence'];c=op['changeEvidence']
    if op['kind']!='update' or op['filterEvidence'] or set(t)!={'title','completedState'} or set(c)!={'completed'} or not all_single(op):return None
    m=re.fullmatch(rf'\s*일정 하나 (?P<cue>미완료로 되돌려\s*(?:줘|주세요)){END}',turns[1])
    title=t['title'][0]['text']
    if not m or not re.fullmatch(rf'\s*{re.escape(title)}(?:야|이야|예요|이에요){END}',turns[2]):return None
    if t['title']!=span(title,2) or c['completed']!=span(m['cue']) or t['completedState']!=c['completed']:return None
    del t['completedState']
    return 'completion_action_is_not_target_state'

def explicit_time_correction(value,turns,reference):
    if list(turns)!=[1]:return None
    op=value['operations'][0];t=op['targetEvidence'];c=op['changeEvidence']
    if op['kind']!='update' or op['filterEvidence'] or set(t)!={'title'} or set(c)!={'time'} or not single(t['title']):return None
    title=t['title'][0]['text']
    m=re.fullmatch(rf'\s*{re.escape(title)} 시간을 (?P<old>{TIME}) (?P<marker>대신) (?P<new>{TIME})(?:으)?로\s*{CHANGE}{END}',turns[1])
    values=c['time']
    if not m or m['old']==m['new'] or t['title']!=span(title) or len(values)!=3:return None
    if values[0]!=span(m['new'])[0] or values[1]!={'turn':1,'text':m['old'],'status':'superseded'}:return None
    marker=values[2]
    if marker['status']!='correction_marker' or marker['turn']!=1 or marker['text'] in turns[1]:return None
    marker['text']=m['marker']
    return 'explicit_time_correction_source_marker'

def explicit_delete_title(value,turns,reference):
    if list(turns)!=[1,2]:return None
    op=value['operations'][0];t=op['targetEvidence']
    if op['kind']!='delete' or op['changeEvidence'] or op['filterEvidence'] or set(t)!={'title','date'} or not all_single(op):return None
    old=t['title'][0]
    if old['turn']!=1 or any(old['text'] in text for text in turns.values()):return None
    m=re.fullmatch(rf'\s*(?P<title>[가-힣A-Za-z0-9]+(?: [가-힣A-Za-z0-9]+)+) 일정을\s+{DELETE}{END}',turns[1])
    date=re.fullmatch(rf'\s*(?P<date>{DATE}) 걸 말하는 거야{END}',turns[2])
    if not m or not date or t['date']!=span(date['date'],2):return None
    if re.search(r'아니|말고|대신|부터|까지|일정|완료|분류|카테고리',m['title']):return None
    # Require a shared two-word anchor; never pick a merely similar unrelated title.
    if len(old['text'].split())<2 or old['text'].split()[:2]!=m['title'].split()[:2]:return None
    t['title']=span(m['title'])
    return 'explicit_delete_source_title'

RULES=(unique_source_turn,future_query,completion_action_not_target,explicit_time_correction,explicit_delete_title)

def apply_rules(raw,*,user_turns,reference,server_time_trusted):
    previous=apply_v6(raw,user_turns=user_turns,reference=reference,server_time_trusted=server_time_trusted)
    current=previous.raw;reasons=list(previous.reasons)
    try:
        if not isinstance(current,str) or len(current.encode())>MAX_PAYLOAD_BYTES:raise ValueError('payload')
        value=json.loads(current,object_pairs_hook=_strict_pairs,parse_constant=_reject_constant)
        if not scope(value,user_turns):return Result(current,current!=raw,tuple(reasons))
        for rule in RULES:
            candidate=copy.deepcopy(value)
            try:
                reason=rule(candidate,user_turns,reference)
                if not reason:continue
                proposed=dump(candidate);_validate(proposed,user_turns,reference)
                following=apply_v6(proposed,user_turns=user_turns,reference=reference,server_time_trusted=server_time_trusted)
                _validate(following.raw,user_turns,reference)
                current=following.raw;value=json.loads(current);reasons.extend([reason,*following.reasons])
            except (ValueError,KeyError,TypeError,IndexError,AttributeError):continue
    except (ValueError,KeyError,TypeError,IndexError,AttributeError,UnicodeError,RecursionError):pass
    return Result(current,current!=raw,tuple(reasons))
