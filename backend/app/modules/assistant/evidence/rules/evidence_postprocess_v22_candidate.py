"""Candidate: conservative missing hints and source-proven completion repair.
No model inference, answer lookup, DB access, approval or execution.
"""
import copy
import json
from dataclasses import dataclass
from .evidence_postprocess_v21 import apply_rules as previous
from .evidence_postprocess_v20_candidate import source_history
from .evidence_postprocess_v15 import model_envelope, NoPlan, ClarificationRequired
from .evidence_postprocess_v3 import dump
from .category_postprocess import _validate, _strict_pairs, _reject_constant, MAX_PAYLOAD_BYTES
VERSION = 'evidence-rules-v22-candidate'

@dataclass(frozen=True)
class Result:
    raw: str
    changed: bool
    reasons: tuple[str, ...]
    version: str = VERSION

def repair_completion_slot(raw, turns, reference, trusted):
    """Discard a misplaced ampm slot ONLY with a fully parsed completion correction.

    ampm is a missing-information label, never an evidence slot. This is not a
    generic unknown-key deletion: every source turn must be consumed, and the
    only change must be an explicit completed-state correction on an update.
    """
    if not isinstance(raw, str) or len(raw.encode()) > MAX_PAYLOAD_BYTES:
        raise NoPlan()
    value = json.loads(raw, object_pairs_hook=_strict_pairs, parse_constant=_reject_constant)
    op = value['operations'][0]
    if len(value['operations']) != 1 or op['kind'] != 'update':
        raise NoPlan()
    if 'ampm' not in op['changeEvidence']:
        raise NoPlan()
    sanitized = copy.deepcopy(value)
    del sanitized['operations'][0]['changeEvidence']['ampm']
    if model_envelope(dump(sanitized)) != 'update':
        raise NoPlan()
    plan, completion = source_history(turns, trusted)
    change = plan['operations'][0]['changeEvidence']
    if not completion or set(change) != {'completed'}:
        raise NoPlan()
    if [s['status'] for s in change['completed']] != ['superseded', 'correction_marker', 'adopted']:
        raise NoPlan()
    proposed = dump(plan)
    _validate(proposed, turns, reference)
    return proposed

def add_proven_missing_hints(value):
    """Add only structural omissions; preserve all existing context-dependent hints.

    No guess about which DB candidate exists, whether consent was given, or
    whether an ambiguous time is morning/evening. Invalid outputs never enter.
    """
    if len(value['operations']) != 1 or value['unsupportedEvidence']:
        return False
    op = value['operations'][0]
    if op['unsupportedEvidence']:
        return False
    def adopted(mapping, slot):
        return any(s['status'] == 'adopted' for s in mapping.get(slot, []))
    c = op['changeEvidence']
    needed = []
    if op['kind'] == 'create':
        for slot, hint in [('title', 'task_or_title'), ('date', 'date'), ('time', 'time')]:
            if not adopted(c, slot):
                needed.append(hint)
    elif op['kind'] == 'update':
        if not op['targetEvidence']:
            needed.append('task_or_title')
        if not any(adopted(c, slot) for slot in c):
            needed.append('change_value')
    changed = False
    for owner in (op, value):
        for hint in needed:
            if hint not in owner['missingInformation']:
                owner['missingInformation'].append(hint)
                changed = True
    return changed

def apply_rules(raw, *, user_turns, reference, server_time_trusted):
    kw = dict(user_turns=user_turns, reference=reference, server_time_trusted=server_time_trusted)
    try:
        repaired = repair_completion_slot(raw, user_turns, reference, server_time_trusted)
    except (ValueError, KeyError, TypeError, IndexError, AttributeError, RecursionError):
        repaired = None
    # Always pass through the v21 completion-versus-delete guard, including repairs.
    result = previous(repaired if repaired is not None else raw, **kw)
    output = result.raw
    reasons = (('source_verified_completion_slot_repair',) if repaired is not None else ()) + tuple(result.reasons)
    try:
        _validate(output, user_turns, reference)
        value = json.loads(output, object_pairs_hook=_strict_pairs, parse_constant=_reject_constant)
        if add_proven_missing_hints(value):
            proposed = dump(value)
            _validate(proposed, user_turns, reference)
            output = proposed
            reasons += ('missing_information_structural_omissions',)
    except (ValueError, KeyError, TypeError, IndexError, AttributeError, RecursionError):
        pass
    return Result(output, output != raw, reasons)
