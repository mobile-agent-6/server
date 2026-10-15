"""Prepare an explicitly UNREVIEWED pipeline smoke test; never uses test data."""
import argparse
import json
import hashlib
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='New local output directory')
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parent.parent / 'multiturn_400_v0.1')
    args = parser.parse_args()
    from transformers import AutoTokenizer
    from mlx_lm.tuner.datasets import ChatDataset

    model = args.model.expanduser().resolve()
    source = args.source.expanduser().resolve()
    output = args.output.expanduser().resolve()
    if not model.is_dir():
        raise SystemExit('Model directory does not exist')
    output.mkdir(parents=True, exist_ok=False)
    config_file = output / 'smoke.yaml'
    if config_file.exists():
        raise SystemExit('Existing smoke.yaml found. Preserve the previous run before preparing again.')
    tokenizer = AutoTokenizer.from_pretrained(model, local_files_only=True)
    template = tokenizer.chat_template
    assert isinstance(template, str), 'Expected one chat template'
    # Save a separate tokenizer; the downloaded checkpoint remains unchanged.
    prefix = '{% set enable_thinking = false %}'
    tokenizer.chat_template = prefix + template
    tokenizer_dir = output / 'tokenizer'
    tokenizer.save_pretrained(tokenizer_dir)
    # Use a local model view with original weights and the non-thinking tokenizer.
    view = output / 'model'
    view.mkdir(exist_ok=True)
    for p in model.iterdir():
        if p.is_file() and not (tokenizer_dir / p.name).exists():
            dest = view / p.name
            if not dest.exists():
                dest.symlink_to(p)
    for p in tokenizer_dir.iterdir():
        dest = view / p.name
        if not dest.exists():
            dest.symlink_to(p)
    tokenizer = AutoTokenizer.from_pretrained(view, local_files_only=True)
    stats = {}
    maximum = 0
    for split, filename in [('train', 'train/training_UNREVIEWED.jsonl'),
                            ('valid', 'validation/validation_UNREVIEWED.jsonl')]:
        path = source / filename
        rows = [json.loads(s) for s in path.read_text().splitlines() if s.strip()]
        ds = ChatDataset(rows, tokenizer, mask_prompt=True)
        lengths = []
        for i, row in enumerate(rows):
            messages = row['messages']
            assert messages[-1]['role'] == 'assistant', (split, i)
            json.loads(messages[-1]['content'])
            tokens, offset = ds.process(row)
            prompt = tokenizer.apply_chat_template(messages[:-1], add_generation_prompt=True, return_dict=False)
            assert tokens[:offset] == prompt, ('Mask prefix mismatch', split, i)
            assert 0 < offset < len(tokens), ('Empty target', split, i)
            supervised = tokenizer.decode(tokens[offset:], skip_special_tokens=True).strip()
            assert supervised == messages[-1]['content'].strip(), ('Unexpected supervised text', split, i, supervised)
            lengths.append(len(tokens))
        maximum = max(maximum, max(lengths))
        stats[split] = {'rows': len(rows), 'min_tokens': min(lengths), 'max_tokens': max(lengths),
                        'mean_tokens': round(sum(lengths) / len(lengths)),
                        'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        (output / (split + '.jsonl')).write_bytes(path.read_bytes())
    limit = ((maximum + 255) // 256) * 256
    assert limit <= 8192, f'Long context ({limit}); inspect before training'
    settings = {'model': str(view), 'data': str(output), 'train': True,
                'fine_tune_type': 'lora', 'mask_prompt': True, 'batch_size': 1,
                'num_layers': 4, 'learning_rate': 1e-5, 'iters': 20,
                'max_seq_length': limit, 'grad_checkpoint': True,
                'steps_per_report': 1, 'steps_per_eval': 10, 'val_batches': 2,
                'save_every': 10, 'seed': 42,
                'adapter_path': str(output / 'adapters')}
    import yaml
    config_file.write_text(yaml.safe_dump(settings, sort_keys=False))
    (output / 'inspection.json').write_text(json.dumps({'status': 'UNREVIEWED_SMOKE_ONLY',
        'mask_check': 'PASS', 'max_seq_length': limit, 'splits': stats}, indent=2))
    print(json.dumps(stats, indent=2))
    print(f'PASS: final-assistant mask verified; no truncation at {limit} tokens.')
    print(f'Config: {config_file}')


if __name__ == '__main__':
    main()
