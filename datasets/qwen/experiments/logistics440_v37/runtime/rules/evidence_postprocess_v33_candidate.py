"""Source-verified intermediate requests with missing title/scope/change values.
Fully matched grammar only; no golden dataset lookup, approvals or execution.
"""
import re
from dataclasses import dataclass
from evidence_postprocess_v32_candidate import apply_rules as previous
from evidence_postprocess_v29 import span,RECOVERY
from evidence_postprocess_v26 import preserve_context_hints
from evidence_postprocess_v15 import NoPlan,ClarificationRequired,clean_title,OUTSIDE,UNCERTAIN,MARK,STATE
from evidence_postprocess_v3 import DATE,TIME,dump
from category_postprocess import CUES,_validate,MAX_PAYLOAD_BYTES
VERSION='evidence-rules-v33-candidate'
END=r'[.!]?\s*'
CREATE=r'(?:등록해|넣어|추가해|잡아)\s*(?:주세요|줘)'
DELETE=r'(?:삭제해\s*(?:주세요|줘)|삭제하고 싶(?:어|어요)|삭제할래|지워\s*(?:주세요|줘))'
TEMP=rf'(?:(?P<date>{DATE})(?:에 있는|에)?\s+)?(?:(?P<time>{TIME})(?:에)?\s+)?'
@dataclass(frozen=True)
class Result:
 raw:str
 changed:bool
 reasons:tuple[str,...]
 version:str=VERSION

def title(value):
 # Explicit title boundary allows records as names; date/state conditions still
 # cannot be swallowed into a title, even if the model mislabeled them.
 clean_title(value,explicit=True)
 if re.search(DATE,value) or re.search(TIME,value) or re.match(STATE,value):raise NoPlan()
 if re.search(r'정오|자정|새벽|아침|저녁|오전|오후|밤',value):raise NoPlan()
 if re.match(r'(?:할|있는|예정인|진행할|열릴|하는|해둘)\s',value):raise NoPlan()
 return value

def source_plan(turns,trusted):
 if not trusted or list(turns) not in ([1],[1,2]):raise NoPlan()
 if any(not isinstance(t,str) or len(t)>2000 or OUTSIDE.search(t) or UNCERTAIN.search(t) or MARK.search(t) for t in turns.values()):raise NoPlan()
 first=turns[1].strip();target={};change={};filters={};missing=[]
 def put(owner,key,value,n=1):
  if value is not None:
   if key=='title':title(value)
   owner[key]=[span(turns,n,value)]
 def temporal(owner,m):
  for key in ['date','time']:put(owner,key,m.groupdict().get(key))
 def finish(kind):
  op={'index':1,'kind':kind,'targetEvidence':target,'changeEvidence':change,'filterEvidence':filters,'unsupportedEvidence':[],'missingInformation':missing.copy()}
  return {'operations':[op],'unsupportedEvidence':[],'missingInformation':missing.copy()}
 # Missing new state: the existing target state is a filter, never the new value.
 m=re.fullmatch(rf'(?P<title>.+?)(?: 일정을| 일정의|의)?\s+상태(?:를)?\s+(?:변경하려고 해|변경할게|바꿀게){END}',first)
 if m:
  put(target,'title',m['title']);missing=['change_value']
  if len(turns)==2:
   n=re.fullmatch(rf'\s*현재 대상은 (?P<state>{STATE}) 일정(?:이야|이에요){END}',turns[2])
   if n:put(target,'completedState',n['state'],2)
   else:
    n=re.fullmatch(rf'\s*대상 날짜(?:는|가) (?P<date>{DATE})(?:이야|야){END}',turns[2])
    if not n:raise NoPlan()
    put(target,'date',n['date'],2)
  return finish('update')
 if len(turns)!=1:raise NoPlan() # Never discard an unparsed follow-up.
 if re.fullmatch(rf'(?:조건을 정해서 |조건을 정해 )일정(?:을)? (?:조회할게|조회하고 싶어|검색할게){END}',first):
  missing=['query_scope'];return finish('list')
 m=re.fullmatch(rf'(?P<date>{DATE}) 일정 목록(?:을)? (?:보고 싶(?:어|어요)|보여\s*(?:주세요|줘)|조회해\s*(?:주세요|줘)){END}',first)
 if m:put(filters,'date',m['date']);return finish('list')
 m=re.fullmatch(rf'{TEMP}일정(?:을)?(?: 하나)? {CREATE}{END}',first)
 if m:
  temporal(change,m);missing=['task_or_title']
  if 'date' not in change:missing.append('date')
  if 'time' not in change:missing.append('time')
  else:
   text=m['time'];hour=re.search(r'(\d{1,2})시',text)
   if hour and 1<=int(hour[1])<=12 and not re.search(r'오전|오후|아침|저녁|밤|새벽|정오|자정',text):missing.append('ampm')
  return finish('create')
 m=re.fullmatch(rf'{TEMP}일정(?:을)?(?: 하나)? {DELETE}{END}',first)
 if m:
  temporal(target,m);missing=['task_or_title'];return finish('delete')
 m=re.fullmatch(rf'(?P<date>{DATE})(?:에 있는)? (?P<title>.+?) 일정(?:을)? {DELETE}{END}',first)
 if m:
  put(target,'date',m['date']);put(target,'title',m['title']);return finish('delete')
 m=re.fullmatch(rf'(?P<date>{DATE}) (?P<time>{TIME})(?:에)? (?P<title>.+?)(?: 일정)?(?:을|를)? {CREATE}{END}',first)
 if m:
  temporal(change,m);put(change,'title',m['title'])
  cues=[c for c in CUES if c in m['title']]
  if len(cues)>1:raise NoPlan()
  if cues:put(change,'category',cues[0])
  hour=re.search(r'(\d{1,2})시',m['time'])
  if hour and 1<=int(hour[1])<=12 and not re.search(r'오전|오후|아침|저녁|밤|새벽|정오|자정',m['time']):missing=['ampm']
  return finish('create')
 raise NoPlan()

def apply_rules(raw,*,user_turns,reference,server_time_trusted):
 kw=dict(user_turns=user_turns,reference=reference,server_time_trusted=server_time_trusted)
 prior=None;question=None
 try:prior=previous(raw,**kw)
 except ClarificationRequired as exc:
  if exc.reason not in RECOVERY:raise
  question=exc
 try:
  if not isinstance(raw,str) or len(raw.encode())>MAX_PAYLOAD_BYTES:raise NoPlan()
  plan=source_plan(user_turns,server_time_trusted)
  preserve_context_hints(plan,raw)
  if prior is not None:preserve_context_hints(plan,prior.raw)
  proposed=dump(plan);_validate(proposed,user_turns,reference)
  return Result(proposed,proposed!=raw,('source_verified_intermediate_request',) if proposed!=raw else ())
 except (ValueError,KeyError,TypeError,IndexError,AttributeError,RecursionError):
  if question is not None:raise question
  return Result(prior.raw,prior.raw!=raw,tuple(prior.reasons))
