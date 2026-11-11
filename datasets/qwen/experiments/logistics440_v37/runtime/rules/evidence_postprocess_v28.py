"""Source-verified lookup followups, completion correction and undecided time guard."""
import re
from dataclasses import dataclass
from evidence_postprocess_v27 import apply_rules as previous,UNKNOWN_DATE
from evidence_postprocess_v26 import preserve_context_hints
from evidence_postprocess_v24 import RECOVERABLE_QUESTIONS
from evidence_postprocess_v15 import NoPlan,ClarificationRequired,clean_title,OUTSIDE,UNCERTAIN,MARK,END,QUERY,STATE,COMPLETE
from evidence_postprocess_v21 import QUOTED
from evidence_postprocess_v3 import DATE,TIME,dump
from category_postprocess import CUES,_validate,MAX_PAYLOAD_BYTES
VERSION='evidence-rules-v28-candidate'
RECOVERABLE=set(RECOVERABLE_QUESTIONS)|{'unresolved_source_copy'}
UNDECIDED=re.compile(r'(?<![가-힣])(?:아직 )?(?:못 정했(?:어|어요|습니다)|결정하지 못했(?:어|어요|습니다))\s*[.!。]?\s*$')
@dataclass(frozen=True)
class Result:
 raw:str
 changed:bool
 reasons:tuple[str,...]
 version:str=VERSION

def undecided_time(turns):
 for text in turns.values():
  if not isinstance(text,str):continue
  text=QUOTED.sub(lambda m:' '*len(m.group()),text)
  if UNDECIDED.search(text) and (len(set(re.findall(DATE,text)))>=2 or len(set(re.findall(TIME,text)))>=2):return True
 return False

def span(turns,n,text,status='adopted'):
 if len(list(re.finditer(re.escape(text),turns[n])))!=1:raise NoPlan()
 return {'turn':n,'text':text,'status':status}

def source_plan(turns,trusted):
 if not trusted or list(turns)!=list(range(1,len(turns)+1)) or not 2<=len(turns)<=6:raise NoPlan()
 if any(not isinstance(t,str) or len(t)>2000 or OUTSIDE.search(t) or UNCERTAIN.search(t) for t in turns.values()):raise NoPlan()
 target={};change={};filters={};unknown=False;corrected=False
 def put(owner,slot,value,n):
  if slot in owner:raise NoPlan()
  if slot=='title':clean_title(value,explicit=False)
  owner[slot]=[span(turns,n,value)]
 first=turns[1]
 m=re.fullmatch(rf'\s*(?P<title>.+?) 일정(?:을)? {QUERY}{END}',first)
 if m:
  if re.match(r'(?:앞으로 남은|지금 이후|물류센터 일정)',m['title']):raise NoPlan()
  kind='list';put(filters,'title',m['title'],1)
 else:
  m=re.fullmatch(rf'\s*(?P<date>{DATE}) 그 일정 (?P<cue>{COMPLETE}){END}',first)
  if m:kind='update';put(target,'date',m['date'],1);put(change,'completed',m['cue'],1)
  else:
   m=re.fullmatch(rf'\s*(?P<title>.+?) 일정 (?P<cue>{COMPLETE}){END}',first)
   if not m:raise NoPlan()
   kind='update';put(target,'title',m['title'],1);put(change,'completed',m['cue'],1)
 for n,text in list(turns.items())[1:]:
  if kind=='list':
   if re.fullmatch(rf'\s*{UNKNOWN_DATE}{END}',text):
    if unknown or 'date' in filters:raise NoPlan()
    unknown=True;continue
   m=re.fullmatch(rf'\s*(?:이제 기억났어\.\s*)?날짜(?:는|가) (?P<date>{DATE})(?:이야|야){END}',text)
   if m:
    if not unknown:raise NoPlan()
    put(filters,'date',m['date'],n);continue
   m=re.fullmatch(rf'\s*(?:그중 |그 중 )?(?P<state>{STATE}) (?:것만|일정만) {QUERY}{END}',text)
   if m:put(filters,'completed',m['state'],n);continue
   m=re.fullmatch(rf'\s*(?P<marker>아니)\s*,?\s*(?P<state>{STATE}) (?:것만|일정만) {QUERY}{END}',text)
   if m:
    if corrected or 'completed' not in filters:raise NoPlan()
    old=filters['completed'][0];old['status']='superseded'
    filters['completed']=[old,span(turns,n,m['marker'],'correction_marker'),span(turns,n,m['state'])];corrected=True;continue
   m=re.fullmatch(rf'\s*(?:분류|카테고리)(?:는|가) (?P<category>{"|".join(map(re.escape,CUES))})(?:이야|야){END}',text)
   if m:put(filters,'category',m['category'],n);continue
  else:
   m=re.fullmatch(rf'\s*대상 (?P<slot>제목|날짜|시간|시각)(?:은|는) (?P<value>.+?)(?:이야|야){END}',text)
   if m:
    slot={'제목':'title','날짜':'date','시간':'time','시각':'time'}[m['slot']]
    if slot!='title' and not re.fullmatch(DATE if slot=='date' else TIME,m['value']):raise NoPlan()
    put(target,slot,m['value'],n);continue
   m=re.fullmatch(rf'\s*(?P<marker>아니)\s*,?\s*(?P<cue>{COMPLETE}){END}',text)
   if m:
    if corrected:raise NoPlan()
    old=change['completed'][0];old['status']='superseded'
    change['completed']=[old,span(turns,n,m['marker'],'correction_marker'),span(turns,n,m['cue'])];corrected=True;continue
  raise NoPlan()
 if kind=='list' and not unknown:raise NoPlan()
 if kind=='update' and not corrected:raise NoPlan()
 missing=['task_or_title'] if kind=='update' and 'title' not in target else []
 op={'index':1,'kind':kind,'targetEvidence':target,'changeEvidence':change,'filterEvidence':filters,'unsupportedEvidence':[],'missingInformation':missing}
 return {'operations':[op],'unsupportedEvidence':[],'missingInformation':list(missing)}

def apply_rules(raw,*,user_turns,reference,server_time_trusted):
 if undecided_time(user_turns):
  exc=ClarificationRequired('undecided_temporal_choice')
  exc.question='변경할 날짜나 시각이 아직 정해지지 않았어요. 최종 날짜·시간과 대상 일정을 확정해서 다시 말씀해 주세요.'
  raise exc
 kw=dict(user_turns=user_turns,reference=reference,server_time_trusted=server_time_trusted)
 prior=None;question=None
 try:prior=previous(raw,**kw)
 except ClarificationRequired as exc:
  if exc.reason not in RECOVERABLE:raise
  question=exc
 try:
  if not isinstance(raw,str) or len(raw.encode())>MAX_PAYLOAD_BYTES:raise NoPlan()
  plan=source_plan(user_turns,server_time_trusted)
  preserve_context_hints(plan,raw)
  if prior is not None:preserve_context_hints(plan,prior.raw)
  proposed=dump(plan);_validate(proposed,user_turns,reference)
  return Result(proposed,proposed!=raw,('source_verified_followup_correction',) if proposed!=raw else ())
 except (ValueError,KeyError,TypeError,IndexError,AttributeError,RecursionError):
  if question is not None:raise question
  return Result(prior.raw,prior.raw!=raw,tuple(prior.reasons))
