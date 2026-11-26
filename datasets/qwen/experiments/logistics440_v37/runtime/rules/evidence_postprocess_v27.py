"""Full-source optional-date lookup recovery over selected v26. No execution."""
import re
from dataclasses import dataclass
from evidence_postprocess_v26 import apply_rules as previous, preserve_context_hints
from evidence_postprocess_v24 import RECOVERABLE_QUESTIONS
from evidence_postprocess_v15 import NoPlan,ClarificationRequired,clean_title,OUTSIDE,UNCERTAIN,MARK,END,QUERY,STATE
from evidence_postprocess_v3 import dump
from category_postprocess import CUES,_validate,MAX_PAYLOAD_BYTES
VERSION='evidence-rules-v27-candidate'
UNKNOWN_DATE=r'(?:정확한 )?날짜(?:는|가) (?:기억이 안 (?:나|나요)|생각이 안 (?:나|나요))'
@dataclass(frozen=True)
class Result:
 raw:str
 changed:bool
 reasons:tuple[str,...]
 version:str=VERSION

def optional_date_lookup(turns,trusted):
 if not trusted or list(turns)!=list(range(1,len(turns)+1)) or not 2<=len(turns)<=5:raise NoPlan()
 if any(not isinstance(t,str) or len(t)>2000 or OUTSIDE.search(t) or UNCERTAIN.search(t) or MARK.search(t) for t in turns.values()):raise NoPlan()
 first=re.fullmatch(rf'\s*(?P<title>.+?) 일정(?:을)? {QUERY}{END}',turns[1])
 if not first:raise NoPlan()
 title=first['title'];clean_title(title,explicit=False)
 if re.match(r'(?:앞으로 남은|지금 이후|물류센터 일정)',title):raise NoPlan()
 evidence={};unknown=False
 def put(slot,value,n):
  if slot in evidence or len(list(re.finditer(re.escape(value),turns[n])))!=1:raise NoPlan()
  evidence[slot]=[{'turn':n,'text':value,'status':'adopted'}]
 put('title',title,1)
 for n,text in list(turns.items())[1:]:
  if re.fullmatch(rf'\s*{UNKNOWN_DATE}{END}',text):
   if unknown:raise NoPlan()
   unknown=True;continue
  m=re.fullmatch(rf'\s*(?:그중 |그 중 )?(?P<state>{STATE}) (?:것만|일정만) {QUERY}{END}',text)
  if m:put('completed',m['state'],n);continue
  m=re.fullmatch(rf'\s*(?:분류|카테고리)(?:는|가) (?P<category>{"|".join(map(re.escape,CUES))})(?:이야|야){END}',text)
  if m:put('category',m['category'],n);continue
  raise NoPlan()
 if not unknown:raise NoPlan()
 op={'index':1,'kind':'list','targetEvidence':{},'changeEvidence':{},'filterEvidence':evidence,'unsupportedEvidence':[],'missingInformation':[]}
 return {'operations':[op],'unsupportedEvidence':[],'missingInformation':[]}

def apply_rules(raw,*,user_turns,reference,server_time_trusted):
 kw=dict(user_turns=user_turns,reference=reference,server_time_trusted=server_time_trusted)
 prior=None;question=None
 try:prior=previous(raw,**kw)
 except ClarificationRequired as exc:
  if exc.reason not in RECOVERABLE_QUESTIONS:raise
  question=exc
 try:
  if not isinstance(raw,str) or len(raw.encode())>MAX_PAYLOAD_BYTES:raise NoPlan()
  plan=optional_date_lookup(user_turns,server_time_trusted)
  preserve_context_hints(plan,prior.raw if prior is not None else raw)
  proposed=dump(plan);_validate(proposed,user_turns,reference)
  return Result(proposed,proposed!=raw,('source_verified_optional_date_lookup',) if proposed!=raw else ())
 except (ValueError,KeyError,TypeError,IndexError,AttributeError,RecursionError):
  if question is not None:raise question
  return Result(prior.raw,prior.raw!=raw,tuple(prior.reasons))
