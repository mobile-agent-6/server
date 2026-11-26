"""Accumulate an explicit second-turn supplement without discarding first-turn evidence.
Only fully consumed source grammars; no dataset lookup, execution or persistent state.
"""
import re
from dataclasses import dataclass
from .evidence_postprocess_v35 import apply_rules as previous
from .evidence_postprocess_v35_candidate import source_plan as initial_plan
from .evidence_postprocess_v33_candidate import title,END
from .evidence_postprocess_v29 import span,RECOVERY
from .evidence_postprocess_v26 import preserve_context_hints
from .evidence_postprocess_v15 import NoPlan,ClarificationRequired,OUTSIDE,UNCERTAIN,MARK,STATE
from .evidence_postprocess_v3 import DATE,TIME,dump
from .category_postprocess import _validate,MAX_PAYLOAD_BYTES
VERSION='evidence-rules-v36-candidate'
@dataclass(frozen=True)
class Result:
 raw:str
 changed:bool
 reasons:tuple[str,...]
 version:str=VERSION

def source_plan(turns,trusted):
 if not trusted or list(turns)!=[1,2]:raise NoPlan()
 if any(not isinstance(t,str) or len(t)>2000 or OUTSIDE.search(t) or UNCERTAIN.search(t) or MARK.search(t) for t in turns.values()):raise NoPlan()
 plan=initial_plan({1:turns[1]},trusted);op=plan['operations'][0];second=turns[2].strip()
 def put(owner,key,value):
  if key=='title':
   title(value)
   if re.search(STATE,value) or re.match(r'(?:현재|대상은|대상이)\s',value):raise NoPlan()
  owner[key]=[span(turns,2,value)]
 def finish(missing=()):
  op['missingInformation']=list(missing);plan['missingInformation']=list(missing);return plan
 # Completion intent from turn1 stays in changeEvidence; title comes from turn2.
 if op['kind']=='update' and 'completed' in op['changeEvidence'] and not op['targetEvidence']:
  m=re.fullmatch(rf'(?:그 )?일정 이름은 (?P<title>.+?)(?:이고|이야|야|이에요){END}',second)
  if not m:m=re.fullmatch(rf'(?P<title>.+?) 일정(?:이야|이에요){END}',second)
  if not m:raise NoPlan()
  put(op['targetEvidence'],'title',m['title']);return finish()
 # No title should be fabricated from a generic lookup request.
 if op['kind']=='list' and not op['filterEvidence'] and op['missingInformation']==['query_scope']:
  m=re.fullmatch(rf'(?P<date>{DATE})(?: (?P<time>{TIME}))? 일정(?:만|을)? 보여\s*(?:줘|주세요){END}',second)
  if not m:raise NoPlan()
  put(op['filterEvidence'],'date',m['date'])
  if m['time']:put(op['filterEvidence'],'time',m['time'])
  return finish()
 # Keep the existing target title AND target state when a change value arrives.
 if op['kind']=='update' and set(op['targetEvidence'])=={'title','completedState'} and not op['changeEvidence'] and op['missingInformation']==['change_value']:
  m=re.fullmatch(rf'시간(?:을|은) (?P<time>{TIME})로 바꿔\s*(?:줘|주세요){END}',second)
  if m:
   put(op['changeEvidence'],'time',m['time']);missing=[]
   if re.search(r'(?:^|\s)(?:[1-9]|1[0-2])시',m['time']) and not re.search(r'오전|오후|아침|저녁|밤|새벽|정오|자정',m['time']):missing=['ampm']
   return finish(missing)
  m=re.fullmatch(rf'날짜(?:를|는) (?P<date>{DATE})로 바꿔\s*(?:줘|주세요){END}',second)
  if m:put(op['changeEvidence'],'date',m['date']);return finish()
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
  return Result(proposed,proposed!=raw,('source_verified_intermediate_v36',) if proposed!=raw else ())
 except (ValueError,KeyError,TypeError,IndexError,AttributeError,RecursionError):
  if question is not None:raise question
  return Result(prior.raw,prior.raw!=raw,tuple(prior.reasons))
