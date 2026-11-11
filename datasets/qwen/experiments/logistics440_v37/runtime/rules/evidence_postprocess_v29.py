"""Completion/reopening source histories with an initially unnamed target.
Only fully consumed, explicit completion requests are reconstructed. No execution.
"""
import re
from dataclasses import dataclass
from evidence_postprocess_v28 import apply_rules as previous,RECOVERABLE,span
from evidence_postprocess_v26 import preserve_context_hints
from evidence_postprocess_v15 import NoPlan,ClarificationRequired,clean_title,OUTSIDE,UNCERTAIN,END,STATE,COMPLETE
from evidence_postprocess_v3 import DATE,TIME,dump
from category_postprocess import CUES,_validate,MAX_PAYLOAD_BYTES
VERSION='evidence-rules-v29-candidate'
CUE=rf'(?:{COMPLETE}|완료 처리를 취소해\s*(?:줘|주세요))'
RECOVERY=set(RECOVERABLE)|{'multiple_corrections_need_restatement'}
@dataclass(frozen=True)
class Result:
 raw:str
 changed:bool
 reasons:tuple[str,...]
 version:str=VERSION

def completion_source_plan(turns,trusted):
 if not trusted or list(turns)!=list(range(1,len(turns)+1)) or not 2<=len(turns)<=6:raise NoPlan()
 if any(not isinstance(t,str) or len(t)>2000 or OUTSIDE.search(t) or UNCERTAIN.search(t) for t in turns.values()):raise NoPlan()
 first=re.fullmatch(rf'\s*(?:(?P<date>{DATE}) )?(?:(?P<state>{STATE}) )?그 일정(?:을)? (?P<cue>{CUE}){END}',turns[1])
 if not first:raise NoPlan()
 target={};change={};corrected=set()
 def put(owner,slot,value,n):
  if slot in owner:raise NoPlan()
  if slot=='title':clean_title(value,explicit=False)
  if slot in ('date','time') and not re.fullmatch(DATE if slot=='date' else TIME,value):raise NoPlan()
  owner[slot]=[span(turns,n,value)]
 def correct(owner,slot,value,n,marker):
  if slot not in owner or slot in corrected or len(owner[slot])!=1:raise NoPlan()
  if slot in ('date','time') and not re.fullmatch(DATE if slot=='date' else TIME,value):raise NoPlan()
  old=owner[slot][0];old['status']='superseded'
  owner[slot]=[old,span(turns,n,marker,'correction_marker'),span(turns,n,value)];corrected.add(slot)
 if first['date']:put(target,'date',first['date'],1)
 if first['state']:put(target,'completedState',first['state'],1)
 put(change,'completed',first['cue'],1)
 for n,text in list(turns.items())[1:]:
  m=re.fullmatch(rf'\s*(?P<date>{DATE}) 일정이 대상(?:이야|야){END}',text)
  if m:put(target,'date',m['date'],n);continue
  m=re.fullmatch(rf'\s*대상 (?P<slot>제목|날짜|시간|시각)(?:은|는) (?P<value>.+?)(?:이야|야){END}',text)
  if m:
   put(target,{'제목':'title','날짜':'date','시간':'time','시각':'time'}[m['slot']],m['value'],n);continue
  m=re.fullmatch(rf'\s*대상 분류(?:는|가) (?P<value>{"|".join(map(re.escape,CUES))})(?:이야|야){END}',text)
  if m:put(target,'category',m['value'],n);continue
  m=re.fullmatch(rf'\s*(?P<marker>아니)\s*,?\s*(?:대상 )?(?P<slot>날짜|시간|시각)(?:은|는) (?P<value>.+?)(?:이야|야){END}',text)
  if m:
   correct(target,{'날짜':'date','시간':'time','시각':'time'}[m['slot']],m['value'],n,m['marker']);continue
  m=re.fullmatch(rf'\s*(?P<marker>아니)\s*,?\s*(?P<cue>{CUE}){END}',text)
  if m:correct(change,'completed',m['cue'],n,m['marker']);continue
  m=re.fullmatch(rf'\s*(?P<marker>아니)\s*,?\s*(?P<title>.+?) 일정(?:을)? (?P<cue>{CUE}){END}',text)
  if m:
   put(target,'title',m['title'],n);correct(change,'completed',m['cue'],n,m['marker']);continue
  raise NoPlan()
 missing=['task_or_title'] if 'title' not in target else []
 op={'index':1,'kind':'update','targetEvidence':target,'changeEvidence':change,'filterEvidence':{},'unsupportedEvidence':[],'missingInformation':missing}
 return {'operations':[op],'unsupportedEvidence':[],'missingInformation':list(missing)}

def apply_rules(raw,*,user_turns,reference,server_time_trusted):
 kw=dict(user_turns=user_turns,reference=reference,server_time_trusted=server_time_trusted)
 prior=None;question=None
 try:prior=previous(raw,**kw)
 except ClarificationRequired as exc:
  if exc.reason not in RECOVERY:raise
  question=exc
 try:
  if not isinstance(raw,str) or len(raw.encode())>MAX_PAYLOAD_BYTES:raise NoPlan()
  plan=completion_source_plan(user_turns,server_time_trusted)
  preserve_context_hints(plan,raw)
  if prior is not None:preserve_context_hints(plan,prior.raw)
  proposed=dump(plan);_validate(proposed,user_turns,reference)
  return Result(proposed,proposed!=raw,('source_verified_completion_target_history',) if proposed!=raw else ())
 except (ValueError,KeyError,TypeError,IndexError,AttributeError,RecursionError):
  if question is not None:raise question
  return Result(prior.raw,prior.raw!=raw,tuple(prior.reasons))
