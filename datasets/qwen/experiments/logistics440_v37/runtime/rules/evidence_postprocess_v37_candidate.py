"""Source-verified linear correction histories. Fully consumed, bounded grammars only.
No case IDs, gold lookup, normalized dates, DB operations or implicit approvals.
"""
import re,copy
from dataclasses import dataclass
from evidence_postprocess_v36 import apply_rules as previous
from evidence_postprocess_v33_candidate import title,END
from evidence_postprocess_v29 import RECOVERY
from evidence_postprocess_v26 import preserve_context_hints
from evidence_postprocess_v15 import NoPlan,ClarificationRequired,OUTSIDE,UNCERTAIN,MARK
from evidence_postprocess_v3 import DATE,dump
from category_postprocess import CUES,_validate,MAX_PAYLOAD_BYTES
TIME=r'(?:(?:오전|오후|새벽|아침|저녁|밤)\s*)?\d{1,2}시(?:\s*(?:\d{1,2}분|반))?'
REQ=r'(?:등록해|조회해|찾아|삭제해|바꿔)\s*(?:줘|주세요)'
VERSION='evidence-rules-v37-candidate'
@dataclass(frozen=True)
class Result:
 raw:str
 changed:bool
 reasons:tuple[str,...]
 version:str=VERSION

def source_plan(turns,trusted):
 if not trusted or not 1<=len(turns)<=5 or list(turns)!=list(range(1,len(turns)+1)):raise NoPlan()
 if any(not isinstance(t,str) or len(t)>2000 or OUTSIDE.search(t) or UNCERTAIN.search(t) for t in turns.values()):raise NoPlan()
 # Keep offsets into the original source, including leading whitespace.
 texts=turns;target={};change={};filters={};corrected=set();kind=None
 def atom(n,value,start=None,status='adopted'):
  positions=[m.start() for m in re.finditer(re.escape(value),texts[n])]
  if not positions:raise NoPlan()
  if start is None:
   if len(positions)!=1:raise NoPlan()
   start=positions[0]
  if start not in positions:raise NoPlan()
  s={'turn':n,'text':value,'status':status}
  if len(positions)>1:s['occurrence']=positions.index(start)+1
  return s
 def checked(slot,value):
  if slot=='title':title(value)
  if slot in ('date','time') and not re.fullmatch(DATE if slot=='date' else TIME,value):raise NoPlan()
 def put(owner,slot,value,n,start=None):
  if slot in owner:raise NoPlan()
  checked(slot,value);owner[slot]=[atom(n,value,start)]
 def category(value,n,start):
  cues=[c for c in CUES if c in value]
  if len(cues)>1:raise NoPlan()
  change.pop('category',None)
  if cues:
   c=cues[0];change['category']=[atom(n,c,start+value.index(c))]
 def correct(owner,slot,value,n,marker,markerstart,newstart,oldvalue=None,oldstart=None):
  key=(id(owner),slot)
  if slot not in owner or key in corrected or len(owner[slot])!=1:raise NoPlan()
  checked(slot,value);old=copy.deepcopy(owner[slot][0])
  if old['text']==value:raise NoPlan()
  if oldvalue is not None:
   if oldvalue!=old['text']:raise NoPlan()
   old=atom(n,oldvalue,oldstart,'superseded')
  else:old['status']='superseded'
  owner[slot]=[old,atom(n,marker,markerstart,'correction_marker'),atom(n,value,newstart)];corrected.add(key)
 first=texts[1]
 m=re.fullmatch(rf'\s*(?P<date>{DATE}) (?P<time>{TIME}) 일정(?:을)? 조회해\s*(?:줘|주세요){END}',first)
 if m:
  kind='list'
  for k in ['date','time']:put(filters,k,m[k],1,m.start(k))
 if kind is None:
  m=re.fullmatch(rf'\s*(?P<date>{DATE})(?: (?P<time>{TIME}))? (?:일정 하나|그 일정(?:을)?) 삭제해\s*(?:줘|주세요){END}',first)
  if m:
   kind='delete'
   for k in ['date','time']:
    if m[k]:put(target,k,m[k],1,m.start(k))
 if kind is None:
  m=re.fullmatch(rf'\s*그 일정의 (?:시간|시각)을 (?P<time>{TIME})로 바꿔\s*(?:줘|주세요){END}',first)
  if m:kind='update';put(change,'time',m['time'],1,m.start('time'))
 if kind is None:
  m=re.fullmatch(rf'\s*(?:(?P<date>{DATE}) )?(?:(?P<time>{TIME})(?:에)? )?(?P<title>.+?)(?: 일정)? 등록해\s*(?:줘|주세요){END}',first)
  if m:
   kind='create';put(change,'title',m['title'],1,m.start('title'));category(m['title'],1,m.start('title'))
   for k in ['date','time']:
    if m[k]:put(change,k,m[k],1,m.start(k))
 if kind is None:raise NoPlan()
 owner={'create':change,'update':change,'list':filters,'delete':target}[kind]
 for n,text in list(texts.items())[1:]:
  # Explicit repeated old time is linked to this turn, including occurrence1.
  suffix={'create':r'로 등록해\s*(?:줘|주세요)','update':r'로 바꿔\s*(?:줘|주세요)','list':r' 일정(?:이야|이에요)','delete':r' 일정(?:이야|이에요)'}[kind]
  m=re.fullmatch(rf'\s*(?P<old>{TIME}) (?P<mark>말고|대신) (?P<new>{TIME}){suffix}{END}',text)
  if m:
   correct(owner,'time',m['new'],n,m['mark'],m.start('mark'),m.start('new'),m['old'],m.start('old'));continue
  suffix={'list':r'로 조회해\s*(?:줘|주세요)','delete':r'(?:이야|야)','create':r'(?:이야|야)','update':r'로 바꿔\s*(?:줘|주세요)'}[kind]
  m=re.fullmatch(rf'\s*(?P<mark>아니)\s*,?\s*(?:삭제할 )?날짜는 (?P<new>{DATE}){suffix}{END}',text)
  if m:
   if '삭제할' in text and kind!='delete':raise NoPlan()
   correct(owner,'date',m['new'],n,m['mark'],m.start('mark'),m.start('new'));continue
  if kind=='create':
   m=re.fullmatch(rf'\s*(?P<date>{DATE})(?:이야|야){END}',text)
   if m:put(change,'date',m['date'],n,m.start('date'));continue
   m=re.fullmatch(rf'\s*(?P<time>{TIME})로 등록해\s*(?:줘|주세요){END}',text)
   if m:put(change,'time',m['time'],n,m.start('time'));continue
   m=re.fullmatch(rf'\s*(?P<mark>아니)\s*,?\s*제목은 (?P<new>.+?)(?:이야|야){END}',text)
   if m:
    correct(change,'title',m['new'],n,m['mark'],m.start('mark'),m.start('new'));category(m['new'],n,m.start('new'));continue
  if kind=='list':
   m=re.fullmatch(rf'\s*제목은 (?P<title>.+?)(?:으로|로) 찾아\s*(?:줘|주세요){END}',text)
   if m:put(filters,'title',m['title'],n,m.start('title'));continue
   m=re.fullmatch(rf'\s*분류는 (?P<category>(?:{"|".join(CUES)}) 분류)로 조회해\s*(?:줘|주세요){END}',text)
   if m:put(filters,'category',m['category'],n,m.start('category'));continue
   m=re.fullmatch(rf'\s*(?P<old>.+?) (?P<mark>말고|대신) (?P<new>.+?) 일정을 찾아\s*(?:줘|주세요){END}',text)
   if m:
    correct(filters,'title',m['new'],n,m['mark'],m.start('mark'),m.start('new'),m['old'],m.start('old'));continue
  if kind in ('update','delete'):
   m=re.fullmatch(rf'\s*(?:일정 |대상 )?제목은 (?P<title>.+?)(?:이야|야){END}',text)
   if m:put(target,'title',m['title'],n,m.start('title'));continue
  raise NoPlan() # Unparsed follow-ups cannot be silently discarded.
 missing=[]
 if kind=='create':
  for k,label in [('title','task_or_title'),('date','date'),('time','time')]:
   if k not in change:missing.append(label)
 elif kind in ('update','delete') and 'title' not in target:missing.append('task_or_title')
 if kind in ('create','update','list') and 'time' in owner:
  t=owner['time'][-1]['text'];m=re.search(r'(\d{1,2})시',t)
  if m and 1<=int(m[1])<=12 and not re.search(r'오전|오후|새벽|아침|저녁|밤',t):missing.append('ampm')
 return {'operations':[{'index':1,'kind':kind,'targetEvidence':target,'changeEvidence':change,'filterEvidence':filters,'unsupportedEvidence':[],'missingInformation':missing.copy()}],'unsupportedEvidence':[],'missingInformation':missing.copy()}

def apply_rules(raw,*,user_turns,reference,server_time_trusted):
 kw=dict(user_turns=user_turns,reference=reference,server_time_trusted=server_time_trusted)
 prior=None;question=None
 try:prior=previous(raw,**kw)
 except ClarificationRequired as exc:
  if exc.reason not in RECOVERY:raise
  question=exc
 try:
  if not isinstance(raw,str) or len(raw.encode())>MAX_PAYLOAD_BYTES:raise NoPlan()
  plan=source_plan(user_turns,server_time_trusted);preserve_context_hints(plan,raw)
  if prior is not None:preserve_context_hints(plan,prior.raw)
  proposed=dump(plan);_validate(proposed,user_turns,reference)
  return Result(proposed,proposed!=raw,('source_verified_intermediate_v37',) if proposed!=raw else ())
 except (ValueError,KeyError,TypeError,IndexError,AttributeError,RecursionError):
  if question is not None:raise question
  return Result(prior.raw,prior.raw!=raw,tuple(prior.reasons))
