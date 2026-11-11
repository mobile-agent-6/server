"""Narrow full-utterance grammar for intermediate requests. No dataset lookup."""
import re
from dataclasses import dataclass
from evidence_postprocess_v33 import apply_rules as previous
from evidence_postprocess_v33_candidate import title,END,CREATE,TEMP
from evidence_postprocess_v29 import span,RECOVERY
from evidence_postprocess_v26 import preserve_context_hints
from evidence_postprocess_v15 import NoPlan,ClarificationRequired,OUTSIDE,UNCERTAIN,MARK
from evidence_postprocess_v3 import DATE,TIME,dump
from category_postprocess import _validate,MAX_PAYLOAD_BYTES
VERSION='evidence-rules-v34-candidate'
@dataclass(frozen=True)
class Result:
 raw:str
 changed:bool
 reasons:tuple[str,...]
 version:str=VERSION

def source_plan(turns,trusted):
 if not trusted or list(turns)!=[1]:raise NoPlan()
 first=turns[1]
 if not isinstance(first,str) or len(first)>2000 or OUTSIDE.search(first) or UNCERTAIN.search(first) or MARK.search(first):raise NoPlan()
 first=first.strip();target={};change={};filters={};missing=[]
 def put(owner,key,value):
  if value is not None:
   if key=='title':title(value)
   owner[key]=[span(turns,1,value)]
 def finish(kind):
  return {'operations':[{'index':1,'kind':kind,'targetEvidence':target,'changeEvidence':change,'filterEvidence':filters,'unsupportedEvidence':[],'missingInformation':missing.copy()}],'unsupportedEvidence':[],'missingInformation':missing.copy()}
 m=re.fullmatch(rf'(?P<date>{DATE}) 일정(?:을)? (?:확인하려고 해|조회할게|조회하려고 해){END}',first)
 if m:put(filters,'date',m['date']);return finish('list')
 # Bare "하나" is a placeholder, not a task title or category.
 m=re.fullmatch(rf'{TEMP}하나 {CREATE}{END}',first)
 if m:
  for key in ['date','time']:put(change,key,m[key])
  missing=['task_or_title']
  if not m['date']:missing.append('date')
  if not m['time']:missing.append('time')
  elif re.search(r'(?:^|\s)(?:[1-9]|1[0-2])시',m['time']) and not re.search(r'오전|오후|아침|저녁|밤|새벽|정오|자정',m['time']):missing.append('ampm')
  return finish('create')
 m=re.fullmatch(rf'(?P<title>.+?)(?:을|를) (?P<time>{TIME})로 바꿔\s*(?:줘|주세요){END}',first)
 if m:
  put(target,'title',m['title']);put(change,'time',m['time'])
  if re.search(r'(?:^|\s)(?:[1-9]|1[0-2])시',m['time']) and not re.search(r'오전|오후|아침|저녁|밤|새벽|정오|자정',m['time']):missing=['ampm']
  return finish('update')
 m=re.fullmatch(rf'(?P<title>.+?)(?:을|를) 일정에서 지워\s*(?:줘|주세요){END}',first)
 if m:put(target,'title',m['title']);return finish('delete')
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
  plan=source_plan(user_turns,server_time_trusted);preserve_context_hints(plan,raw)
  if prior is not None:preserve_context_hints(plan,prior.raw)
  proposed=dump(plan);_validate(proposed,user_turns,reference)
  return Result(proposed,proposed!=raw,('source_verified_intermediate_v34',) if proposed!=raw else ())
 except (ValueError,KeyError,TypeError,IndexError,AttributeError,RecursionError):
  if question is not None:raise question
  return Result(prior.raw,prior.raw!=raw,tuple(prior.reasons))
