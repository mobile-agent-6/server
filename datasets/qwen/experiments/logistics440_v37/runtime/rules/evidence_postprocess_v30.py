"""Require clarification for explicit undecided operation or completion choices.
No evidence rewriting, operation selection, approval or execution is performed.
"""
import re
from evidence_postprocess_v29 import apply_rules as previous,ClarificationRequired
from evidence_postprocess_v21 import QUOTED
VERSION='evidence-rules-v30-candidate'
UNDECIDED=re.compile(r'(?<![가-힣])(?:아직\s*)?(?:못\s*정했(?:어|어요|습니다)|결정하지\s*못했(?:어|어요|습니다)|고민\s*중(?:이야|이에요|입니다)|모르겠(?:어|어요|습니다)|확실하지\s*않(?:아|아요|습니다))\s*$')
CHOICE=re.compile(r'인지|일지|할지|할까|또는|혹은|둘\s*중|중\s*(?:어느|뭘|무엇)|(?<![가-힣])중(?![가-힣])')
STATE=re.compile(r'(?<![가-힣])(?P<state>미완료|완료)(?=(?:로)?(?:\s|인지|일지|할지|할까|$))')
UNDO=re.compile(r'(?<![가-힣])완료\s*(?:처리(?:를)?\s*)?(?:취소|해제)(?=인지|일지|할지|할까|\s|$)')
DELETE=re.compile(r'(?<![가-힣])(?:일정(?:을)?\s*)?삭제(?=인지|일지|할지|할까|\s|$)')

def unresolved_choice(turns):
 for text in turns.values():
  if not isinstance(text,str):continue
  text=QUOTED.sub(lambda m:' '*len(m.group()),text)
  # A later sentence in the same turn may settle the earlier hesitation.
  # Only the last non-empty sentence is an unresolved-choice candidate.
  clauses=[c.strip() for c in re.split(r'[.!?。;\n\r]+',text) if c.strip()]
  if not clauses:continue
  clause=clauses[-1]
  match=UNDECIDED.search(clause)
  if not match:continue
  alternatives=clause[:match.start()]
  if not CHOICE.search(alternatives):continue
  if UNDO.search(alternatives) and DELETE.search(alternatives):return 'undecided_operation_choice'
  if {m['state'] for m in STATE.finditer(alternatives)}=={'완료','미완료'}:return 'undecided_completion_choice'
 return None

def apply_rules(raw,*,user_turns,reference,server_time_trusted):
 reason=unresolved_choice(user_turns)
 if reason:
  exc=ClarificationRequired(reason)
  if reason=='undecided_operation_choice':
   exc.question='완료 상태를 미완료로 되돌리려는 건가요, 일정 자체를 삭제하려는 건가요? 대상 일정과 최종 작업을 한 문장으로 다시 말씀해 주세요.'
  else:
   exc.question='완료와 미완료 중 어떤 상태로 바꿀까요? 대상 일정과 최종 상태를 한 문장으로 다시 말씀해 주세요.'
  raise exc
 return previous(raw,user_turns=user_turns,reference=reference,server_time_trusted=server_time_trusted)
