"""Fixed-context validation diagnostic, not end-to-end conversation accuracy."""
import argparse
import collections
import gc
import hashlib
import json
import shlex
import sys
import time
from datetime import datetime
from pathlib import Path


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def input_identity(root, runtime):
    files = {}
    for directory, prefix, pattern in [(root / 'model', 'model', '*'),
                                        (root / 'adapters', 'adapters', 'adapter_config.json'),
                                        (runtime, 'runtime', '**/*.py')]:
        for path in sorted(directory.glob(pattern)):
            if path.is_file():
                files[f'{prefix}/{path.relative_to(directory)}'] = file_hash(path)
    return files


def validate_saved_rows(records, selected):
    if [r['row'] for r in records] != [i + 1 for i in selected[:len(records)]]:
        raise ValueError('Invalid saved row order or duplicate rows')


def validate_resume(previous, current):
    for key in ['data_sha256', 'adapter_sha256', 'temperature', 'max_tokens', 'input_identity']:
        if key not in previous or previous[key] != current[key]:
            raise ValueError(f'Cannot resume: {key} missing or changed')


def select_rows(rows, limit):
    if limit < 1:
        raise ValueError('limit must be positive')
    buckets = collections.defaultdict(list)
    for index, row in enumerate(rows):
        buckets[json.loads(row['messages'][-1]['content'])['action']].append(index)
    selected = []
    while len(selected) < min(limit, len(rows)):
        for key in sorted(buckets):
            if buckets[key] and len(selected) < limit:
                selected.append(buckets[key].pop(0))
    return selected


def resume_command(root, runtime, out):
    return shlex.join([sys.executable, str(Path(__file__).resolve()),
                       '--run-dir', str(root), '--runtime', str(runtime),
                       '--resume', str(out), '--stage', 'both'])


def save_records(path, records):
    # Commit a complete snapshot; interruption cannot leave a partial JSONL row
    # in the saved results. An unfinished temporary file is ignored on resume.
    temporary = path.with_suffix('.jsonl.tmp')
    temporary.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in records), encoding='utf-8')
    temporary.replace(path)


def diagnostic_scope(root):
    inspection = root / 'inspection.json'
    status = json.loads(inspection.read_text(encoding='utf-8')).get('status') if inspection.exists() else None
    label = {'REVIEWED_SMOKE_ONLY': 'REVIEWED', 'UNREVIEWED_SMOKE_ONLY': 'UNREVIEWED'}.get(status, 'UNSPECIFIED')
    return f'{label} fixed-reference-context diagnostic; no API calls or JSON constraints'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True, help='Prepared dataset/model/adapter directory')
    parser.add_argument('--runtime', type=Path, default=Path(__file__).resolve().parent.parent / 'multiturn_400_v0.1/runtime')
    parser.add_argument('--limit', type=int, default=10)
    parser.add_argument('--resume', type=Path, help='Resume a saved comparison directory; skip completed rows')
    parser.add_argument('--stage', choices=['both', 'before', 'after'], default='both')
    args = parser.parse_args()
    if args.stage == 'after' and not args.resume:
        parser.error('--stage after requires --resume')
    if args.limit < 1:
        parser.error('--limit must be positive')
    root = args.run_dir.expanduser().resolve()
    runtime = args.runtime.expanduser().resolve()
    sys.path.insert(0, str(runtime))

    data = (root / 'valid.jsonl').read_bytes()
    rows = [json.loads(line) for line in data.decode().splitlines() if line.strip()]
    selected = select_rows(rows, args.limit)
    out = args.resume.expanduser().resolve() if args.resume else root / ('comparison-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    if not args.resume:
        out.mkdir()
    meta = {'scope': diagnostic_scope(root),
            'rows_1_based': [i + 1 for i in selected], 'data_sha256': hashlib.sha256(data).hexdigest(),
            'temperature': 0, 'max_tokens': 512,
            'adapter_sha256': file_hash(root / 'adapters/adapters.safetensors'),
            'input_identity': input_identity(root, runtime)}
    if args.resume:
        previous = json.loads((out / 'manifest.json').read_text(encoding='utf-8'))
        validate_resume(previous, meta)
        meta = previous
        selected = [i - 1 for i in meta['rows_1_based']]
    else:
        (out / 'manifest.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')
    print(f'Comparison directory: {out}', flush=True)
    command = resume_command(root, runtime, out)
    print(f'Resume command (skips saved rows):\n{command}', flush=True)
    try:
        compare_stages(args, root, out, rows, selected, meta)
    except KeyboardInterrupt:
        print(f'\nStopped. Saved rows are preserved; the unfinished row will be retried.\nResume:\n{command}', flush=True)


def compare_stages(args, root, out, rows, selected, meta):
    import mlx.core as mx
    from mlx_lm import load, generate
    from mlx_lm.sample_utils import make_sampler
    from schedule_assistant.actions import parse_action

    results = {}
    prompts = {}
    for label, adapter in [('before', None), ('after', str(root / 'adapters'))]:
        path = out / (label + '.jsonl')
        results[label] = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()] if path.exists() else []
        existing = results[label]
        validate_saved_rows(existing, selected)
        if args.stage not in ['both', label] or len(existing) == len(selected):
            print(f'{label}: saved {len(existing)}/{len(selected)}; skipping', flush=True)
            continue
        if label == 'after' and len(results['before']) != len(selected):
            raise SystemExit('Complete before first: use --stage before with the same --resume path')
        print(f'Loading {label}...', flush=True)
        model, tokenizer = load(str(root / 'model'), adapter_path=adapter)
        completed = len(existing)
        for position, index in enumerate(selected, 1):
            if position <= completed:
                continue
            messages = rows[index]['messages']
            assert messages[-1]['role'] == 'assistant'
            expected = json.loads(messages[-1]['content'])
            prompt = tokenizer.apply_chat_template(messages[:-1], add_generation_prompt=True, tokenize=True, return_dict=False)
            if label == 'before':
                prompts[index] = prompt
            elif index in prompts:
                assert prompts[index] == prompt, 'Before/after input mismatch'
            mx.random.seed(42)
            start = time.perf_counter()
            raw = generate(model, tokenizer, prompt=prompt, max_tokens=512,
                           sampler=make_sampler(temp=0), verbose=False)
            seconds = time.perf_counter() - start
            error = None
            try:
                parsed = json.loads(raw)
                parse_action(raw)
                valid = True
            except (ValueError, TypeError, KeyError) as exc:
                parsed, valid, error = None, False, str(exc)
            record = {'row': index + 1, 'expected': expected, 'output': raw,
                      'schema_valid': valid, 'action_match': valid and parsed.get('action') == expected['action'],
                      'exact_json_match': valid and parsed == expected,
                      'seconds': round(seconds, 2), 'error': error}
            results[label].append(record)
            save_records(path, results[label])
            print(f"{label} {position}/{len(selected)} row={index+1} expected={expected['action']} valid={valid} exact={record['exact_json_match']} {seconds:.1f}s", flush=True)
        del model, tokenizer
        gc.collect()
        mx.clear_cache()
    if any(len(results[label]) != len(selected) for label in ['before', 'after']):
        print(f'Stage finished. Resume directory: {out}', flush=True)
        return
    summary = {'count': len(selected), 'scope': meta['scope']}
    for label, records in results.items():
        summary[label] = {key: sum(r[key] for r in records)
                          for key in ['schema_valid', 'action_match', 'exact_json_match']}
    summary['improved_exact'] = sum(not a['exact_json_match'] and b['exact_json_match'] for a, b in zip(results['before'], results['after']))
    summary['regressed_exact'] = sum(a['exact_json_match'] and not b['exact_json_match'] for a, b in zip(results['before'], results['after']))
    (out / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary, indent=2))
    print(f'Results: {out}', flush=True)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Stopped during setup. If a comparison directory with manifest.json exists, use it with --resume; otherwise restart the command.')
