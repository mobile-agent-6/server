"""Source-verified intermediate completion/title accumulation and missing slots.
Only fully consumed1/2-turn grammars. No dataset lookup or backend state mutation.
"""
import re
from dataclasses import dataclass
from evidence_postprocess_v34 import apply_rules as previous
from evidence_postprocess_v33_candidate import title,END
from evidence_postprocess_v29 import span,RECOVERY
from evidence_postprocess_v26 import preserve_context_hints
from evidence_postprocess_v15 import NoPlan,ClarificationRequired,OUTSIDE,UNCERTAIN,MARK,STATE
from evidence_postprocess_v3 import DATE,dump
from category_postprocess import _validate,MAX_PAYLOAD_BYTES
VERSION='evidence-rules-v35-candidate'
@dataclass(frozen=True)
class Result:
 raw:str
 changed:bool
 reasons:tuple[str,...]
 version:str=VERSION

def source_plan(turns,trusted):
 if not trusted or list(turns) not in ([1],[1,2]):raise NoPlan()
 if any(not isinstance(t,str) or len(t)>2000 or OUTSIDE.search(t) or UNCERTAIN.search(t) or MARK.search(t) for t in turns.values()):raise NoPlan()
 first=turns[1].strip();target={};change={};filters={};missing=[]
 def put(owner,key,value,n=1):
  if key=='title':title(value)
  owner[key]=[span(turns,n,value)]
 def finish(kind):
  return {'operations':[{'index':1,'kind':kind,'targetEvidence':target,'changeEvidence':change,'filterEvidence':filters,'unsupportedEvidence':[],'missingInformation':missing.copy()}],'unsupportedEvidence':[],'missingInformation':missing.copy()}
 # A requested new state belongs to changeEvidence even when target arrives later.
 m=re.fullmatch(rf'(?P<request>(?:완료|미완료)로 (?:표시해|바꿔)\s*(?:줘|주세요)){END}',first)
 if m:
  put(change,'completed',m['request'])
  if len(turns)==1:missing=['task_or_title']
  else:
   n=re.fullmatch(rf'\s*(?:일정 )?제목은 (?P<title>.+?)(?:이고|이야|야|이에요){END}',turns[2])
   if not n:raise NoPlan()
   put(target,'title',n['title'],2)
  return finish('update')
 if len(turns)!=1:raise NoPlan()
 if re.fullmatch(rf'일정(?:을)?(?: 좀)? 찾아\s*(?:줘|주세요){END}',first) or re.fullmatch(rf'일정 목록을 보고 싶(?:은데|어|어요){END}',first):
  missing=['query_scope'];return finish('list')
 m=re.fullmatch(rf'(?P<title>.+?) 일정을 바꿀게\. (?P<state>{STATE}) 일정이 대상이야{END}',first)
 if m:
  put(target,'title',m['title']);put(target,'completedState',m['state']);missing=['change_value'];return finish('update')
 m=re.fullmatch(rf'삭제하려는 (?:물류 )?일정은 (?P<date>{DATE})에 있어{END}',first)
 if m:
  put(target,'date',m['date']);missing=['task_or_title'];return finish('delete')
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
  return Result(proposed,proposed!=raw,('source_verified_intermediate_v35',) if proposed!=raw else ())
 except (ValueError,KeyError,TypeError,IndexError,AttributeError,RecursionError):
  if question is not None:raise question
  return Result(prior.raw,prior.raw!=raw,tuple(prior.reasons))
