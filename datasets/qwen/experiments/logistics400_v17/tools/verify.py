"""Offline CPU verification; does not run inference or training."""
import sys,json
from pathlib import Path
from collections import Counter,defaultdict
sys.path.insert(0,str(Path(__file__).resolve().parent))
from common import ROOT,digest,json_rows,PROMPT,render_user_message,score_output,feature,datetime
sys.path.insert(0,str(ROOT/'runtime/rules'))
from evidence_postprocess_v17 import apply_rules,ClarificationRequired

def require(condition,message):
    if not condition: raise ValueError(message)

def apply(raw,user):
    return apply_rules(raw,user_turns={t['turn']:t['text'] for t in user['userTurns']},reference=datetime.fromisoformat(user['taskReferenceTime']),server_time_trusted=user['serverTimeTrusted']).raw

def main():
    manifest=json.loads((ROOT/'manifest.json').read_text())
    for name,meta in manifest['files'].items():
        p=ROOT/name
        require(p.is_file() and p.stat().st_size==meta['bytes'] and digest(p)==meta['sha256'],'File mismatch: '+name)
    train=json_rows(ROOT/'data/train400.jsonl');valid=json_rows(ROOT/'data/valid20.jsonl')
    require(train==json_rows(ROOT/'data/stage1-train222.jsonl')+json_rows(ROOT/'data/stage2-train178.jsonl'),'Training stages differ from cumulative data')
    require(len(train)==400 and len(valid)==20,'Dataset size mismatch')
    require(len({r['messages'][1]['content'] for r in train})==400,'Duplicate training input')
    require(not {r['messages'][1]['content'] for r in train}&{r['messages'][1]['content'] for r in valid},'Exact train/validation input overlap')
    counts=Counter();gold=0;renderer=0
    for r in train+valid:
        m=r['messages'];u=json.loads(m[1]['content']);target=json.loads(m[2]['content'])
        require(m[0]['content']==PROMPT,'System prompt mismatch')
        rendered=render_user_message([t['text'] for t in u['userTurns']],reference_time=datetime.fromisoformat(u['taskReferenceTime']),server_time_trusted=u['serverTimeTrusted'])
        require(rendered==m[1]['content'],'Renderer byte mismatch');renderer+=1
        score=score_output(m[2]['content'],u,target)
        require(score['contract_pass'] and score['exact'],'Invalid gold output')
        require(json.loads(apply(m[2]['content'],u))==target,'Postprocessor changed gold');gold+=1
    for r in train:counts[feature(json.loads(r['messages'][2]['content']))]+=1
    require(dict(counts)==dict(create=80,list=80,update=80,delete=80,completed_update=80),'Feature balance mismatch')
    inputs=json_rows(ROOT/'evaluation/regression-inputs150.jsonl')
    targets={r['key']:r['target'] for r in json_rows(ROOT/'evaluation/regression-targets150.jsonl')}
    results={r['key']:r for r in json_rows(ROOT/'evaluation/regression-results150.jsonl')}
    require(len(inputs)==len(targets)==len(results)==150,'Evaluation size mismatch')
    groups=defaultdict(Counter);replayed=0;idem=0
    for row in inputs:
        key=row['key'];u=json.loads(row['messages'][1]['content']);saved=results[key];g=groups[row['selectionEvaluationGroup']];g['rows']+=1
        require(row['messages'][0]['content']==PROMPT,'Evaluation system prompt mismatch')
        require(render_user_message([t['text'] for t in u['userTurns']],reference_time=datetime.fromisoformat(u['taskReferenceTime']),server_time_trusted=u['serverTimeTrusted'])==row['messages'][1]['content'],'Evaluation renderer mismatch');renderer+=1
        rawscore=score_output(saved['raw'],u,targets[key]);g['rawExact']+=int(rawscore['exact']);g['rawContract']+=int(rawscore['contract_pass'])
        try:
            post=apply(saved['raw'],u)
        except ClarificationRequired as exc:
            require(saved['v17Raw'] is None and saved['clarification']==exc.reason,'Clarification replay mismatch: '+key)
            g['clarifications']+=1;replayed+=1;continue
        require(post==saved['v17Raw'],'Output byte mismatch: '+key)
        score=score_output(post,u,targets[key]);require(score==saved['v17Score'],'Score mismatch: '+key)
        require(apply(post,u)==post,'Non-idempotent output: '+key);idem+=1;replayed+=1
        g['postExact']+=int(score['exact']);g['postContract']+=int(score['contract_pass'])
    expected=json.loads((ROOT/'evaluation/summary.json').read_text())['selected400DevelopmentGroups']
    for name,values in expected.items():
        for metric,value in values.items():require(groups[name][metric]==value,'Summary mismatch: '+name+'/'+metric)
    report=dict(status='PASS',manifestFiles=len(manifest['files']),baseWeightsChecked=False,trainingRows=400,byFeature=dict(counts),validationRows=20,goldPreserved=gold,rendererByteMatches=renderer,cachedOutputsReplayed=replayed,idempotentOutputs=idem,evaluation={k:dict(v) for k,v in groups.items()},gpuInferencePerformed=False,independentTest=False,apiDatabaseReplayPerformed=False,orangePiVerified=False)
    print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
