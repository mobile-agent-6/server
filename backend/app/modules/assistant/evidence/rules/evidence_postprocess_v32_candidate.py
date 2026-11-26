"""Explicit undecided choices and a narrow, fully consumed final-state dialogue.
No model training, approval, database access, or execution.
"""
import re
from dataclasses import dataclass
from .evidence_postprocess_v30 import apply_rules as previous, ClarificationRequired, CHOICE, STATE
from .evidence_postprocess_v21 import QUOTED
from .evidence_postprocess_v29 import RECOVERY, span
from .evidence_postprocess_v26 import preserve_context_hints
from .evidence_postprocess_v15 import NoPlan, clean_title, OUTSIDE
from .evidence_postprocess_v3 import dump
from .category_postprocess import _validate, MAX_PAYLOAD_BYTES
VERSION='evidence-rules-v32-candidate'
# End anchored: past hesitation, negated uncertainty and later decisions do not match.
UNDECIDED=re.compile(r'(?<![가-힣])(?:아직\s*)?(?:못\s*정했(?:어|어요|습니다)|결정하지\s*못했(?:어|어요|습니다)|결정을\s*못\s*내렸(?:어|어요|습니다)|선택(?:을)?\s*못\s*했(?:어|어요|습니다)|선택(?:을\s*)?하지\s*못했(?:어|어요|습니다)|고민\s*중(?:이야|이에요|입니다)|모르겠(?:어|어요|습니다)|확실하지\s*않(?:아|아요|습니다))\s*$')
# Korean conjunctions allow either ordering (삭제와 완료 취소 / 완료 취소와 삭제).
AFTER=r'(?=인지|일지|할지|할까|와|과|\s|$)'
UNDO=re.compile(r'(?<![가-힣])완료\s*(?:처리(?:를)?\s*)?(?:취소|해제)'+AFTER)
DELETE=re.compile(r'(?<![가-힣])(?:일정(?:을)?\s*)?삭제'+AFTER)

def unresolved_choice(turns):
 for text in turns.values():
  if not isinstance(text,str):continue
  text=QUOTED.sub(lambda m:' '*len(m.group()),text)
  clauses=[c.strip() for c in re.split(r'[.!?。;\n\r]+',text) if c.strip()]
  if not clauses:continue
  m=UNDECIDED.search(clauses[-1])
  if not m:continue
  alternatives=clauses[-1][:m.start()]
  if not CHOICE.search(alternatives):continue
  if UNDO.search(alternatives) and DELETE.search(alternatives):return 'undecided_operation_choice'
  if {m['state'] for m in STATE.finditer(alternatives)}=={'완료','미완료'}:return 'undecided_completion_choice'
 return None

@dataclass(frozen=True)
class Result:
 raw:str
 changed:bool
 reasons:tuple[str,...]
 version:str=VERSION

# This is deliberately a two-turn source grammar, not a last-keyword-wins rule.
OPTIONS=r'(?P<a>미완료|완료)로 할지 (?P<b>미완료|완료)로 할지 '
FINAL=r'(?P<cue>(?:미완료|완료)로 (?:바꿔|표시해) (?:주세요|줘))'
RESOLVED=[re.compile(OPTIONS+r'고민했지만 '+FINAL+r'[.!]?\s*'),
          re.compile(OPTIONS+r'(?:아직 )?못 정했어[.] 이제 '+FINAL+r'[.!]?\s*')]

def resolved_state_plan(turns,trusted):
 if not trusted or list(turns)!=[1,2]:raise NoPlan()
 if any(not isinstance(t,str) or len(t)>2000 or OUTSIDE.search(t) for t in turns.values()):raise NoPlan()
 first=re.fullmatch(r'\s*(?P<title>.+?) 일정의 상태를 바꿔 (?:주세요|줘)[.!]?\s*',turns[1])
 if not first:raise NoPlan()
 title=clean_title(first['title'],explicit=False)
 final=next((m for pat in RESOLVED if (m:=pat.fullmatch(turns[2].strip()))),None)
 if not final or final['a']==final['b']:raise NoPlan()
 op={'index':1,'kind':'update','targetEvidence':{'title':[span(turns,1,title)]},'changeEvidence':{'completed':[span(turns,2,final['cue'])]},'filterEvidence':{},'unsupportedEvidence':[],'missingInformation':[]}
 return {'operations':[op],'unsupportedEvidence':[],'missingInformation':[]}

def apply_rules(raw,*,user_turns,reference,server_time_trusted):
 reason=unresolved_choice(user_turns)
 if reason:
  exc=ClarificationRequired(reason)
  exc.question=('완료 상태를 미완료로 되돌리려는 건가요, 일정 자체를 삭제하려는 건가요? 대상 일정과 최종 작업을 한 문장으로 다시 말씀해 주세요.' if reason=='undecided_operation_choice' else '완료와 미완료 중 어떤 상태로 바꿀까요? 대상 일정과 최종 상태를 한 문장으로 다시 말씀해 주세요.')
  raise exc
 kw=dict(user_turns=user_turns,reference=reference,server_time_trusted=server_time_trusted)
 prior=None;question=None
 try:prior=previous(raw,**kw)
 except ClarificationRequired as exc:
  if exc.reason not in RECOVERY:raise
  question=exc
 try:
  if not isinstance(raw,str) or len(raw.encode())>MAX_PAYLOAD_BYTES:raise NoPlan()
  plan=resolved_state_plan(user_turns,server_time_trusted)
  preserve_context_hints(plan,raw)
  if prior is not None:preserve_context_hints(plan,prior.raw)
  proposed=dump(plan);_validate(proposed,user_turns,reference)
  return Result(proposed,proposed!=raw,('source_verified_resolved_state_choice',) if proposed!=raw else ())
 except (ValueError,KeyError,TypeError,IndexError,AttributeError,RecursionError):
  if question is not None:raise question
  return Result(prior.raw,prior.raw!=raw,tuple(prior.reasons))
