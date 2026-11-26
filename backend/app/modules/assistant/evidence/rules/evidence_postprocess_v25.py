"""Candidate: typed missing-title request, role recovery, and labelled rename.
No inference, dataset lookup or execution. Full-history consumption required.
"""
import copy,json,re
from dataclasses import dataclass
from .evidence_postprocess_v24 import apply_rules as previous,narrow_source_plan,RECOVERABLE_QUESTIONS
from .evidence_postprocess_v15 import NoPlan,ClarificationRequired,clean_title,OUTSIDE,UNCERTAIN,MARK,STATE,QUERY,DELETE,END
from .evidence_postprocess_v3 import DATE,TIME,dump
from .category_postprocess import CUES,_validate,_strict_pairs,_reject_constant,MAX_PAYLOAD_BYTES
from app.modules.assistant.evidence.normalize import resolve_time,TimeStatus
VERSION='evidence-rules-v25-candidate'

@dataclass(frozen=True)
class Result:
 raw:str
 changed:bool
 reasons:tuple[str,...]
 version:str=VERSION

def source_plan(turns,trusted):
 if not trusted or list(turns) not in ([1],[1,2],[1,2,3]):raise NoPlan()
 if any(not isinstance(t,str) or len(t)>2000 or OUTSIDE.search(t) or UNCERTAIN.search(t) or MARK.search(t) for t in turns.values()):raise NoPlan()
 def span(text,n):
  if not text or len(list(re.finditer(re.escape(text),turns[n])))!=1:raise NoPlan()
  return {'turn':n,'text':text,'status':'adopted'}
 op={'index':1,'kind':None,'targetEvidence':{},'changeEvidence':{},'filterEvidence':{},'unsupportedEvidence':[],'missingInformation':[]}
 def put(role,slot,text,n,title=False):
  if title:clean_title(text,explicit=False)
  op[role][slot]=[span(text,n)]
 first=turns[1];rename=False
 if len(turns)==1:
  m=re.fullmatch(rf'\s*(?P<date>{DATE})(?:에)? 일정을 (?:추가해|등록해)\s*(?:줘|주세요){END}',first)
  if m:
   op['kind']='create';put('changeEvidence','date',m['date'],1);op['missingInformation']=['task_or_title','time']
  else:
   m=re.fullmatch(rf'\s*(?P<category>분류가 (?:{"|".join(map(re.escape,CUES))})인) (?P<title>.+?) 일정 {QUERY}{END}',first)
   if m:
    op['kind']='list';put('filterEvidence','title',m['title'],1,True);put('filterEvidence','category',m['category'],1)
   else:
    m=re.fullmatch(rf'\s*(?P<state>{STATE}) (?P<title>.+?) 일정 {DELETE}{END}',first)
    if not m:raise NoPlan()
    op['kind']='delete';put('targetEvidence','title',m['title'],1,True);put('targetEvidence','completedState',m['state'],1)
 else:
  # v24 proves the possessive boundary and explicit new-title instruction.
  plan=narrow_source_plan({1:turns[1],2:turns[2]},trusted);op=copy.deepcopy(plan['operations'][0])
  if op['kind']!='update' or set(op['changeEvidence'])!={'title'}:raise NoPlan()
  rename=True
  if len(turns)==3:
   m=re.fullmatch(rf'\s*바꿀 (?:시간|시각)은 (?P<time>{TIME})(?:이야|야){END}',turns[3])
   if not m:raise NoPlan()
   put('changeEvidence','time',m['time'],3)
   if resolve_time([m['time']]).status is TimeStatus.NEEDS_AMPM:op['missingInformation'].append('ampm')
 return {'operations':[op],'unsupportedEvidence':[],'missingInformation':list(op['missingInformation'])},rename

def only_instruction_prefix_difference(raw,plan):
 """All other evidence must exactly match a complete source-verified rename."""
 value=json.loads(raw,object_pairs_hook=_strict_pairs,parse_constant=_reject_constant)
 if len(value['operations'])!=1:return False
 prior=value['operations'][0]['changeEvidence'].get('title',[])
 desired=plan['operations'][0]['changeEvidence']['title']
 if len(prior)!=1 or len(desired)!=1:return False
 wanted=dict(desired[0],text='새 제목으로 '+desired[0]['text'])
 if prior[0]!=wanted:return False
 stripped=copy.deepcopy(value);stripped['operations'][0]['changeEvidence']['title']=copy.deepcopy(desired)
 return stripped==plan

def apply_rules(raw,*,user_turns,reference,server_time_trusted):
 kw=dict(user_turns=user_turns,reference=reference,server_time_trusted=server_time_trusted)
 prior=None;question=None;valid=False
 try:prior=previous(raw,**kw)
 except ClarificationRequired as exc:
  if exc.reason not in RECOVERABLE_QUESTIONS:raise
  question=exc
 if prior is not None:
  try:_validate(prior.raw,user_turns,reference);valid=True
  except (ValueError,KeyError,TypeError,IndexError,AttributeError,RecursionError):pass
 try:
  if not isinstance(raw,str) or len(raw.encode())>MAX_PAYLOAD_BYTES:raise NoPlan()
  plan,rename=source_plan(user_turns,server_time_trusted)
  if valid and not (rename and only_instruction_prefix_difference(prior.raw,plan)):raise NoPlan()
  proposed=dump(plan);_validate(proposed,user_turns,reference)
  reason='source_verified_rename_instruction_prefix' if valid else 'source_verified_typed_request_recovery'
  return Result(proposed,proposed!=raw,(reason,))
 except (ValueError,KeyError,TypeError,IndexError,AttributeError,RecursionError):
  if question is not None:raise question
  return Result(prior.raw,prior.raw!=raw,tuple(prior.reasons))
