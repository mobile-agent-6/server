"""Conservative create-category correction, independent of model runtime and gold.

Call before normal backend validation. This helper never approves an operation or
executes a write. Unhandled or ambiguous cases return the exact original string.
"""
from __future__ import annotations
import copy
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping
from app.modules.assistant.evidence.contract import MAX_PAYLOAD_BYTES,parse_evidence_output
from app.modules.assistant.evidence.gate import check_roles,check_turn_prefix
from app.modules.assistant.evidence.safety import check_role_span_overlaps

VERSION='create-category-v1'
CUES={'입고':'INBOUND','재고':'INVENTORY','출고':'OUTBOUND','피킹':'OUTBOUND',
      '포장':'OUTBOUND','반품':'RETURN','기타':'OTHER'}
CUE_PATTERN=re.compile('|'.join(CUES))

@dataclass(frozen=True)
class CategoryResult:
    raw: str
    changed: bool
    reason: str
    before: Any=None
    after: Any=None
    version: str=VERSION

def _strict_pairs(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('duplicate JSON key')
        result[key]=value
    return result

def _reject_constant(value):raise ValueError('non-finite JSON number')

def _validate(raw,turns,reference):
    result=parse_evidence_output(raw,user_turns=turns)
    check_roles(result,reference=reference)
    check_role_span_overlaps(result,user_turns=turns)
    check_turn_prefix(list(turns),len(turns))
    return result

def apply_category_rules(raw: str, *, user_turns: Mapping[int,str],reference: datetime) -> CategoryResult:
    def unchanged(reason):return CategoryResult(raw,False,reason)
    if not isinstance(raw,str):
        return unchanged('invalid_or_oversized_payload')
    try:
        if len(raw.encode('utf-8'))>MAX_PAYLOAD_BYTES:return unchanged('invalid_or_oversized_payload')
    except UnicodeError:return unchanged('invalid_text_encoding')
    try:
        value=json.loads(raw,object_pairs_hook=_strict_pairs,parse_constant=_reject_constant)
        ops=value['operations']
        if not isinstance(ops,list) or len(ops)!=1 or ops[0]['kind']!='create':
            return unchanged('outside_single_create_scope')
        if not isinstance(ops[0]['changeEvidence'],dict):return unchanged('invalid_change_map')
        stripped=copy.deepcopy(value)
        before=stripped['operations'][0]['changeEvidence'].pop('category',None)
        # Category cannot mask invalid titles, dates, spans or other contract errors.
        checked=_validate(json.dumps(stripped,ensure_ascii=False),user_turns,reference)
        operation=checked.operations[0]
        title=operation.change.get('title',())
        if len(title)!=1 or title[0].status.value!='adopted':
            return unchanged('missing_multiple_or_corrected_title')
        title=title[0]
        original_turn=user_turns[title.turn]
        positions=list(re.finditer(re.escape(title.text),original_turn))
        if title.occurrence is not None:
            positions=positions[title.occurrence-1:title.occurrence]
        if len(positions)!=1:return unchanged('non_exact_or_ambiguous_title_location')
        title_start,title_end=positions[0].span()
        if any(re.search(r'분류|카테고리',text) for text in user_turns.values()):
            return unchanged('explicit_category_context')
        if operation.unsupported or checked.unsupported:
            return unchanged('unsupported_request_context')
        # A cue elsewhere may express an explicit category, old value, or another
        # operation. Do not overwrite it using only the final title.
        for number,text in user_turns.items():
            for match in CUE_PATTERN.finditer(text):
                if number!=title.turn or not (title_start<=match.start() and match.end()<=title_end):
                    return unchanged('category_cue_outside_adopted_title')
        matches=list(CUE_PATTERN.finditer(title.text))
        if len(matches)>1:return unchanged('multiple_category_cues')
        # Preserve every already-valid nonempty category. No reinterpretation of
        # a valid explicit span, historical correction, or occurrence annotation.
        if before is not None:
            try:
                _validate(raw,user_turns,reference)
                return unchanged('existing_category_valid')
            except (ValueError,KeyError,TypeError):pass
        after=None
        if matches:
            cue=matches[0].group()
            after=[{'turn':title.turn,'text':cue,'status':'adopted'}]
            stripped['operations'][0]['changeEvidence']['category']=after
        if before==after:return unchanged('already_correct_omission')
        output=json.dumps(stripped,ensure_ascii=False,separators=(',',':'))
        _validate(output,user_turns,reference)
        return CategoryResult(output,True,'fill_category_from_exact_title' if after else 'remove_unrecognized_category',before,after)
    except (ValueError,KeyError,TypeError,IndexError,AttributeError,RecursionError):
        return unchanged('invalid_noncategory_output_or_input')
