"""Guard against absorbing valid source conditions into a reconstructed title.

Contract validity is not semantic correctness. This is a narrow preservation
guard, not a blanket rule to trust all syntactically valid model output.
"""
import json
from dataclasses import dataclass
from evidence_postprocess_v15 import (
    apply_rules as apply_v15, source_plan, model_envelope, NoPlan,
    ClarificationRequired, MARK,
)
from category_postprocess import _validate, _strict_pairs, _reject_constant

VERSION='evidence-rules-v16'

@dataclass(frozen=True)
class Result:
    raw: str
    changed: bool
    reasons: tuple[str,...]
    version: str=VERSION

def absorbed_condition(before,after):
    if len(before['operations'])!=len(after['operations']):return False
    for old,new in zip(before['operations'],after['operations']):
        if (old['index'],old['kind'])!=(new['index'],new['kind']):continue
        for role in ('targetEvidence','filterEvidence'):
            a,b=old[role],new[role]
            titles=[s for s in a.get('title',[]) if s['status']=='adopted']
            for title in b.get('title',[]):
                if title['status']!='adopted':continue
                # A copied old title and a separately validated condition have
                # both been folded into the same new, wider title span.
                if not any(s['turn']==title['turn'] and s['text']!=title['text'] and s['text'] in title['text'] for s in titles):continue
                for slot in ('date','time','category','completed','completedState','futureScope'):
                    for condition in a.get(slot,[]):
                        if (condition['status']=='adopted' and condition not in b.get(slot,[])
                            and condition['turn']==title['turn'] and condition['text'] in title['text']
                            and not any(condition['text'] in s['text'] for s in titles if s['turn']==condition['turn'])):
                            return True
    return False

def apply_rules(raw, *, user_turns, reference, server_time_trusted):
    kw=dict(user_turns=user_turns,reference=reference,server_time_trusted=server_time_trusted)
    candidate=apply_v15(raw,**kw)  # Existing ambiguity exceptions must propagate.
    if candidate.raw!=raw and not any(MARK.search(t) for t in user_turns.values()):
        try:
            before=json.loads(raw,object_pairs_hook=_strict_pairs,parse_constant=_reject_constant)
            after=json.loads(candidate.raw,object_pairs_hook=_strict_pairs,parse_constant=_reject_constant)
            _validate(raw,user_turns,reference)
            _validate(candidate.raw,user_turns,reference)
            if absorbed_condition(before,after):
                return Result(raw,False,('preserve_source_condition_from_title_absorption',))
        except (ValueError,TypeError,KeyError,IndexError,AttributeError,UnicodeError,RecursionError):
            pass
    return Result(candidate.raw,candidate.raw!=raw,tuple(candidate.reasons))
