"""Fail closed on completion-undo versus delete conflicts. Candidate only.

Does not fabricate corrected evidence, infer a new target, approve or execute.
Unknown follow-up wording cannot make a completion cancellation executable as
schedule deletion. A later explicit deletion also requires restatement here.
"""
import json,re
from dataclasses import dataclass
from evidence_postprocess_v20_candidate import apply_rules as previous
from evidence_postprocess_v15 import ClarificationRequired
from category_postprocess import _strict_pairs,_reject_constant
VERSION='evidence-rules-v21-candidate'
UNDO=re.compile(r'(?<![가-힣])완료\s*(?:처리를\s*(?:취소해|해제해)|표시를\s*해제해|취소해)\s*(?:주세요|줘)(?![가-힣])')
QUOTED=re.compile(r'"[^"\n]*"|“[^”\n]*”|‘[^’\n]*’|\x27[^\x27\n]*\x27')
@dataclass(frozen=True)
class Result:
 raw:str
 changed:bool
 reasons:tuple[str,...]
 version:str=VERSION

def source_has_completion_undo(turns):
 for text in turns.values():
  if not isinstance(text,str):continue
  # Ignore quoted titles; preserve string positions rather than joining pieces.
  text=QUOTED.sub(lambda m:' '*len(m.group()),text)
  if UNDO.search(text):return True
 return False

def apply_rules(raw,*,user_turns,reference,server_time_trusted):
 p=previous(raw,user_turns=user_turns,reference=reference,server_time_trusted=server_time_trusted)
 if source_has_completion_undo(user_turns):
  try:
   value=json.loads(p.raw,object_pairs_hook=_strict_pairs,parse_constant=_reject_constant)
   deletes=any(o.get('kind')=='delete' for o in value.get('operations',[]) if isinstance(o,dict))
  except (ValueError,TypeError,AttributeError):deletes=False # Existing contract validation handles malformed output.
  if deletes:
   e=ClarificationRequired('completion_undo_delete_conflict')
   e.question='완료 상태를 미완료로 바꾸려는 건가요, 일정 자체를 삭제하려는 건가요? 대상 일정과 원하는 작업을 다시 말씀해 주세요.'
   raise e
 return Result(p.raw,p.raw!=raw,tuple(p.reasons))
