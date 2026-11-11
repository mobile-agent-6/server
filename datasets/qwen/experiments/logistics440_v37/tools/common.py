"""CPU-only contract and provenance helpers; no model loading or network access."""
from __future__ import annotations
import ast
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from collections.abc import Sequence
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / 'runtime/backend'
MODEL = ROOT / 'model/base'
sys.path.insert(0, str(BACKEND))
from app.modules.assistant.evidence.contract import parse_evidence_output
from app.modules.assistant.evidence.gate import check_roles, analyze_text
from app.modules.assistant.evidence.safety import check_role_span_overlaps, evidence_question

CLIENT = BACKEND / 'app/modules/assistant/evidence/model_client.py'
node = next(n for n in ast.parse(CLIENT.read_text()).body
            if isinstance(n, ast.FunctionDef) and n.name == 'render_user_message')
scope = dict(json=json, datetime=datetime, Sequence=Sequence, SEOUL=ZoneInfo('Asia/Seoul'))
exec(compile(ast.Module(body=[node], type_ignores=[]), str(CLIENT), 'exec'), scope)
render_user_message = scope['render_user_message']
PROMPT = CLIENT.with_name('prompt.txt').read_text()  # Preserve backend bytes, including newline.

def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def json_rows(path):
    return [json.loads(s) for s in Path(path).read_text().splitlines() if s.strip()]

def save_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')

def save_rows(path, rows):
    Path(path).write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))

ORDERS = {
    'root': ('operations', 'unsupportedEvidence', 'missingInformation'),
    'operation': ('index', 'kind', 'targetEvidence', 'changeEvidence', 'filterEvidence', 'unsupportedEvidence', 'missingInformation'),
    'role': ('title', 'date', 'time', 'category', 'completedState', 'completed', 'futureScope'),
    'span': ('turn', 'text', 'status', 'occurrence'),
}

def ordered(value, level='root'):
    if set(value) - set(ORDERS[level]):
        raise ValueError(f'Unexpected keys: {level}: {set(value) - set(ORDERS[level])}')
    result = {}
    for key in ORDERS[level]:
        if key not in value:
            continue
        x = value[key]
        if key == 'operations': x = [ordered(i, 'operation') for i in x]
        elif key in ('targetEvidence', 'changeEvidence', 'filterEvidence'):
            x = ordered(x, 'role')
        elif key == 'unsupportedEvidence' or level == 'role':
            x = [ordered(i, 'span') for i in x]
        result[key] = x
    return result

def feature(target):
    ops = target['operations']
    if len(ops) != 1: return 'mixed_or_unsupported'
    op = ops[0]
    return 'completed_update' if op['kind'] == 'update' and 'completed' in op['changeEvidence'] else op['kind']

def score_output(raw, user, target):
    """Strict JSON equality is conservative; no repair of model output."""
    result = dict(parser=False, roles=False, overlap=False, exact=False, kind=False)
    turns = {i['turn']: i['text'] for i in user['userTurns']}
    reference = datetime.fromisoformat(user['taskReferenceTime'])
    try:
        output = parse_evidence_output(raw, user_turns=turns)
        result['parser'] = True
        parsed = json.loads(raw)
        result['exact'] = parsed == target
        result['kind'] = [op['kind'] for op in parsed['operations']] == [op['kind'] for op in target['operations']]
        check_roles(output, reference=reference)
        result['roles'] = True
        check_role_span_overlaps(output, user_turns=turns)
        result['overlap'] = True
        result['safety_questions'] = [q.value for op in output.operations
            if (q := evidence_question(op, turns=list(turns.values()), reference=reference,
                                     coverage_operations=output.operations)) is not None]
    except (ValueError, KeyError, TypeError) as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
    result['contract_pass'] = result['parser'] and result['roles'] and result['overlap']
    return result
