"""Role-labelled update accumulation and explicit uncertainty questions.
A fully consumed source history proves target vs change independently of model
JSON validity. No dataset lookup, schedule IDs, approvals, DB calls or execution.
"""
import json,re
from dataclasses import dataclass
from evidence_postprocess_v25 import apply_rules as previous
from evidence_postprocess_v24 import TASK_ENDINGS,RECOVERABLE_QUESTIONS
from evidence_postprocess_v15 import NoPlan,ClarificationRequired,clean_title,OUTSIDE,UNCERTAIN,MARK,END
from evidence_postprocess_v21 import QUOTED
from evidence_postprocess_v3 import DATE,TIME,dump
from category_postprocess import _validate,_strict_pairs,_reject_constant,MAX_PAYLOAD_BYTES
from app.modules.assistant.evidence.normalize import resolve_time,TimeStatus
VERSION='evidence-rules-v26-candidate'
UNCERTAIN_STATEMENT=re.compile(r'(?<![가-힣])(?:확실하지 않(?:아요|아|습니다)|기억이 안 (?:나요|나)|모르겠(?:어요|어|습니다))(?=[.!?。\s]|$)')
CONTEXT_HINTS={'candidate_choice','user_consent','absolute_datetime_due_to_untrusted_time'}

@dataclass(frozen=True)
class Result:
 raw:str
 changed:bool
 reasons:tuple[str,...]
 version:str=VERSION

def has_temporal_uncertainty(turns):
 for text in turns.values():
  if not isinstance(text,str):continue
  text=QUOTED.sub(lambda m:' '*len(m.group()),text)
  if not UNCERTAIN_STATEMENT.search(text):continue
  # An optional unknown lookup date is not a conflict. Require two explicit
  # competing values of the same temporal type before forcing restatement.
  if len(set(re.findall(DATE,text)))>=2 or len(set(re.findall(TIME,text)))>=2:return True
 return False

def labelled_update_plan(turns,trusted):
 if not trusted or list(turns)!=list(range(1,len(turns)+1)) or not 2<=len(turns)<=6:raise NoPlan()
 if any(not isinstance(t,str) or len(t)>2000 or OUTSIDE.search(t) or UNCERTAIN.search(t) or MARK.search(t) for t in turns.values()):raise NoPlan()
 # The initial request establishes an existing target. A possessive ending is
 # accepted only for complete known task nouns, as in the selected v24 policy.
 m=re.fullmatch(rf'\s*(?P<title>.+?)의 제목을 바꾸고 싶어{END}',turns[1])
 if not m or m['title'].split()[-1] not in TASK_ENDINGS:raise NoPlan()
 op={'index':1,'kind':'update','targetEvidence':{},'changeEvidence':{},'filterEvidence':{},'unsupportedEvidence':[],'missingInformation':[]}
 def put(role,slot,text,n):
  if slot in op[role]:raise NoPlan() # repeated or conflicting values need separate correction logic
  if slot=='title':clean_title(text,explicit=False)
  elif not re.fullmatch(DATE if slot=='date' else TIME,text):raise NoPlan()
  if len(list(re.finditer(re.escape(text),turns[n])))!=1:raise NoPlan()
  op[role][slot]=[{'turn':n,'text':text,'status':'adopted'}]
 put('targetEvidence','title',m['title'],1)
 for n in range(2,len(turns)+1):
  text=turns[n]
  m=re.fullmatch(rf'\s*새 제목으로 (?P<title>.+?)(?:을|를) 써\s*(?:줘|주세요){END}',text)
  if not m:m=re.fullmatch(rf'\s*새 제목은 (?P<title>.+?)(?:이야|야){END}',text)
  if m:put('changeEvidence','title',m['title'],n);continue
  m=re.fullmatch(rf'\s*(?P<role>바꿀|대상) (?P<slot>날짜|시간|시각)(?:은|는) (?P<value>.+?)(?:이야|야){END}',text)
  if not m:raise NoPlan()
  role='changeEvidence' if m['role']=='바꿀' else 'targetEvidence'
  slot='date' if m['slot']=='날짜' else 'time'
  put(role,slot,m['value'],n)
 if 'title' not in op['changeEvidence']:raise NoPlan() # the announced rename is still unresolved
 if 'time' in op['changeEvidence'] and resolve_time([op['changeEvidence']['time'][0]['text']]).status is TimeStatus.NEEDS_AMPM:op['missingInformation'].append('ampm')
 return {'operations':[op],'unsupportedEvidence':[],'missingInformation':list(op['missingInformation'])}

def preserve_context_hints(plan,raw):
 # Do not clear consent/candidate/time-trust needs originating outside this rule.
 try:
  value=json.loads(raw,object_pairs_hook=_strict_pairs,parse_constant=_reject_constant)
  if len(value['operations'])!=1:return
  hints=set(value['missingInformation'])|set(value['operations'][0]['missingInformation'])
  for hint in sorted(hints & CONTEXT_HINTS):
   for owner in (plan,plan['operations'][0]):
    if hint not in owner['missingInformation']:owner['missingInformation'].append(hint)
 except (ValueError,KeyError,TypeError,IndexError,AttributeError):pass

def apply_rules(raw,*,user_turns,reference,server_time_trusted):
 if has_temporal_uncertainty(user_turns):
  exc=ClarificationRequired('explicit_temporal_uncertainty')
  exc.question='날짜나 시각이 아직 확실하지 않아요. 최종 대상 일정과 원하는 날짜·시간을 확정해서 다시 말씀해 주세요.'
  raise exc
 kw=dict(user_turns=user_turns,reference=reference,server_time_trusted=server_time_trusted)
 prior=None;question=None
 try:prior=previous(raw,**kw)
 except ClarificationRequired as exc:
  if exc.reason not in RECOVERABLE_QUESTIONS:raise
  question=exc
 try:
  if not isinstance(raw,str) or len(raw.encode())>MAX_PAYLOAD_BYTES:raise NoPlan()
  plan=labelled_update_plan(user_turns,server_time_trusted)
  preserve_context_hints(plan,prior.raw if prior is not None else raw)
  proposed=dump(plan);_validate(proposed,user_turns,reference)
  return Result(proposed,proposed!=raw,('source_verified_target_change_roles',) if proposed!=raw else ())
 except (ValueError,KeyError,TypeError,IndexError,AttributeError,RecursionError):
  if question is not None:raise question
  return Result(prior.raw,prior.raw!=raw,tuple(prior.reasons))
