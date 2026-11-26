"""Reproduce and verify 440 existing + 560 synthetic title variants; no training."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import random
import re
import sys
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[3]
SOURCE = ROOT.parent / 'logistics440_v37'
sys.path.insert(0, str(REPO / 'backend'))
from app.modules.assistant.evidence.contract import parse_evidence_output
from app.modules.assistant.evidence.gate import check_roles
from app.modules.assistant.evidence.safety import check_role_span_overlaps, evidence_question

FEATURES = ('create', 'list', 'update', 'delete', 'completed_update')
ROLES = ('targetEvidence', 'changeEvidence', 'filterEvidence')
SEED = 20261112
CUES = re.compile('입고|출고|재고|피킹|포장|반품')
LOCATIONS = ('북측 창고', '남측 창고', '동측 창고', '서측 창고', '중앙 창고',
             '상온 구역', '저온 구역', '냉장 구역', '검수 구역', '집하 구역',
             '분류 구역', '하역 구역', '임시 보관 구역', '완충 구역')
TASKS = ('상자 수량', '보호재 상태', '상품 식별표', '보관 위치', '작업대 상태',
         '이동 통로', '파손 내역', '인계 문서', '운반 장비', '적치 높이',
         '물품 목록', '안내 표지', '바코드 인쇄 상태', '봉인 상태', '상자 규격',
         '수량 차이', '누락 상품', '운송 서류', '안전 표지', '분류 기준')
ENDINGS = {0: ('대조', '검토', '비교'), 8: ('진열', '배열'),
           'other': ('확인', '점검', '기록')}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def sha(value):
    return hashlib.sha256(value).hexdigest()


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def feature(row):
    op = json.loads(row['messages'][2]['content'])['operations'][0]
    return 'completed_update' if 'completed' in op['changeEvidence'] else op['kind']


def conversation(row):
    turns = json.loads(row['messages'][1]['content'])['userTurns']
    return tuple(re.sub(r'\s+', '', unicodedata.normalize('NFKC', t['text'])) for t in turns)


def validate(row):
    require(set(row) == {'messages'}, 'Unexpected row metadata')
    require([m['role'] for m in row['messages']] == ['system', 'user', 'assistant'], 'Message roles')
    user = json.loads(row['messages'][1]['content'])
    turns = {t['turn']: t['text'] for t in user['userTurns']}
    require(list(turns) == list(range(1, len(turns) + 1)), 'Turn numbering')
    require(len(turns) == len(user['userTurns']), 'Duplicate turn number')
    require(row['messages'][1]['content'] == json.dumps(user, ensure_ascii=False, sort_keys=True,
                                                     separators=(',', ':')), 'User rendering')
    target = parse_evidence_output(row['messages'][2]['content'], user_turns=turns)
    reference = datetime.fromisoformat(user['taskReferenceTime'])
    check_roles(target, reference=reference)
    check_role_span_overlaps(target, user_turns=turns)
    return [q.value if (q := evidence_question(op, turns=list(turns.values()), reference=reference,
                                             coverage_operations=target.operations)) else None
            for op in target.operations]


def source_title(row):
    target = json.loads(row['messages'][2]['content'])
    spans = [s for op in target['operations'] for role in ROLES for s in op[role].get('title', [])]
    titles = {s['text'] for s in spans}
    if len(titles) != 1 or any(s['status'] != 'adopted' for s in spans):
        return None
    title = next(iter(titles))
    # Keep explicit temporal and unsupported wording in its original context.
    if len(title) < 5 or re.search('오늘|내일|모레|알림|문자|완료', title):
        return None
    return title


def generate():
    rng = random.Random(SEED)
    rows, index, pools = [], [], {f: [] for f in FEATURES}
    sources = [SOURCE / 'data/training/train400.jsonl', SOURCE / 'data/training/new-prefix40.jsonl']
    for path in sources:
        for line, row in enumerate(read_rows(path), 1):
            validate(row)
            meta = dict(sourceFile=path.relative_to(REPO).as_posix(), sourceRow=line,
                        sourceInputSha256=sha(row['messages'][1]['content'].encode('utf-8')),
                        feature=feature(row), origin='existing440', reviewState='inherited',
                        lineage=f'{path.relative_to(REPO).as_posix()}:{line}')
            rows.append(row)
            index.append(meta)
            if title := source_title(row):
                pools[feature(row)].append((row, meta, title))
    require(len(rows) == 440 and Counter(feature(r) for r in rows) == dict.fromkeys(FEATURES, 88),
            'Expected original balanced 440 rows')
    seen = {conversation(r) for r in rows}
    used_titles = set()
    for name in FEATURES:
        require(pools[name], 'No source titles: ' + name)
        rng.shuffle(pools[name])
        for n in range(112):
            source, meta, old_title = pools[name][n % len(pools[name])]
            final = (ord(old_title[-1]) - 0xAC00) % 28 if '가' <= old_title[-1] <= '힣' else 0
            endings = ENDINGS.get(final, ENDINGS['other'])
            cue = ' '.join(dict.fromkeys(CUES.findall(old_title)))
            while True:
                title = ' '.join(filter(None, (cue, rng.choice(LOCATIONS), rng.choice(TASKS),
                                               rng.choice(endings))))
                if title not in used_titles:
                    used_titles.add(title)
                    break
            row = copy.deepcopy(source)
            user = json.loads(row['messages'][1]['content'])
            target = json.loads(row['messages'][2]['content'])
            for turn in user['userTurns']:
                turn['text'] = turn['text'].replace(old_title, title)
            for op in target['operations']:
                for role in ROLES:
                    for spans in op[role].values():
                        for span in spans:
                            span['text'] = span['text'].replace(old_title, title)
            row['messages'][1]['content'] = json.dumps(user, ensure_ascii=False, sort_keys=True,
                                                      separators=(',', ':'))
            row['messages'][2]['content'] = compact(target)
            require(validate(row) == validate(source), 'Title substitution changed safety question')
            key = conversation(row)
            require(key not in seen, 'Duplicate normalized conversation')
            seen.add(key)
            rows.append(row)
            index.append({**meta, 'origin': 'synthetic_title_variant', 'reviewState': 'PROPOSED_UNREVIEWED',
                          'replacements': {old_title: title},
                          'independentFamily': False})
    for n, (row, meta) in enumerate(zip(rows, index), 1):
        meta.update(row=n, caseId=f'logistics1000-{n:04}',
                    inputSha256=sha(row['messages'][1]['content'].encode('utf-8')),
                    userTurnCount=len(json.loads(row['messages'][1]['content'])['userTurns']))
    require(len(rows) == 1000 and Counter(feature(r) for r in rows) == dict.fromkeys(FEATURES, 200),
            'Expected 1000 rows, 200 per feature')
    require(len({r['messages'][1]['content'] for r in rows}) == 1000, 'Duplicate input')
    return rows, index, sources


def artifacts():
    rows, index, sources = generate()
    protected = sorted((SOURCE / 'evaluation').glob('*/inputs.jsonl'))
    protected += [SOURCE / 'data/training/valid20.jsonl', SOURCE / 'data/training/last-run/valid.jsonl']
    blocked = {conversation(r) for p in protected for r in read_rows(p)}
    require(not {conversation(r) for r in rows[440:]} & blocked,
            'New data duplicates v37 validation/development conversation')
    prompt = rows[0]['messages'][0]['content']
    require(all(r['messages'][0]['content'] == prompt for r in rows), 'System prompt changed')
    # Verify the validator rejects copied spans and operation-role mistakes.
    invalid = copy.deepcopy(rows[440])
    bad = json.loads(invalid['messages'][2]['content'])
    bad['operations'][0]['changeEvidence']['title'][0]['text'] = '__원문에없는검사용문자열__'
    invalid['messages'][2]['content'] = compact(bad)
    try:
        validate(invalid)
    except ValueError:
        pass
    else:
        raise ValueError('Validator accepted fabricated span')
    bad = json.loads(rows[440]['messages'][2]['content'])
    bad['operations'][0]['targetEvidence'] = bad['operations'][0]['changeEvidence']
    invalid['messages'][2]['content'] = compact(bad)
    try:
        validate(invalid)
    except ValueError:
        pass
    else:
        raise ValueError('Validator accepted create target evidence')
    outputs = {name: ''.join(compact(r) + '\n' for r in data).encode('utf-8')
               for name, data in [('train1000.jsonl', rows), ('synthetic560.jsonl', rows[440:]),
                                  ('train1000-index.jsonl', index)]}
    report = dict(status='PASS', createdDate='2026-11-12', datasetState='PROPOSED_UNREVIEWED',
                  seed=SEED, totalRows=1000, preservedRows=440, addedSyntheticRows=560,
                  rowsByFeature=dict(Counter(feature(r) for r in rows)),
                  addedByFeature=dict(Counter(feature(r) for r in rows[440:])),
                  addedUserTurnCounts=dict(Counter(m['userTurnCount'] for m in index[440:])),
                  uniqueInputs=1000, addedNormalizedConversationDuplicates=0,
                  addedExactOverlapWithV37ValidationAndDevelopment=0,
                  protectedFilesChecked=len(protected), contractRoleAndOverlapPasses=1000,
                  negativeValidatorChecks=2, preservedSafetyQuestionSignatures=560,
                  systemPromptSha256=sha(prompt.encode('utf-8')),
                  independentReviewPerformed=False, modelTrainingPerformed=False,
                  modelEvaluationPerformed=False, apiDatabaseReplayPerformed=False)
    outputs['validation.json'] = (json.dumps(report, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    dependencies = sources + protected + [Path(__file__)]
    dependencies += [REPO / 'backend/app/modules/assistant/evidence' / name for name in
                     ('contract.py', 'gate.py', 'safety.py', 'normalize.py', 'role_contract.py', 'texts.py')]
    manifest = dict(version='logistics1000-v0.1-proposed', seed=SEED,
                    dependencyHashFormat='UTF-8 with LF newlines',
                    dependencies={p.relative_to(REPO).as_posix(): sha(p.read_text(encoding='utf-8').encode('utf-8'))
                                  for p in dependencies},
                    outputs={name: dict(bytes=len(data), sha256=sha(data)) for name, data in outputs.items()})
    outputs['manifest.json'] = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    return outputs, report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('build', 'verify'))
    args = parser.parse_args()
    outputs, report = artifacts()
    for name, data in outputs.items():
        path = ROOT / name
        if args.action == 'build':
            path.write_bytes(data)
        else:
            require(path.read_bytes() == data, 'Reproduction mismatch: ' + name)
    print(json.dumps(report, ensure_ascii=False, indent=2))
