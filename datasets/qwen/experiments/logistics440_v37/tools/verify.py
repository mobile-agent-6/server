"""Portable CPU verification. No MLX import, model inference, API or DB access."""
from pathlib import Path
from collections import Counter,defaultdict
from datetime import datetime
import argparse,hashlib,json,sys
if sys.version_info < (3,11):raise SystemExit('Python 3.11 or newer is required (backend enum.StrEnum).')
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'runtime/rules')]
from common import digest,json_rows,score_output,PROMPT,render_user_message
from evidence_postprocess_v37 import apply_rules,ClarificationRequired

def require(ok,message):
 if not ok:raise ValueError(message)
def read(name):return json.loads((ROOT/name).read_text())
def canonical(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def sha_text(x):return hashlib.sha256(x.encode()).hexdigest()
def score_row(raw,user,target):
 kw=dict(user_turns={t['turn']:t['text'] for t in user['userTurns']},reference=datetime.fromisoformat(user['taskReferenceTime']),server_time_trusted=user['serverTimeTrusted'])
 try:
  p=apply_rules(raw,**kw);s=score_output(p.raw,user,target or {'operations':[]})
  require(apply_rules(p.raw,**kw).raw==p.raw,'Postprocessor not idempotent')
  return {'postRaw':p.raw,'score':s,'clarification':None}
 except ClarificationRequired as e:return {'postRaw':None,'score':None,'clarification':{'reason':e.reason,'question':e.question}}

def replay(save_expected=False):
 model=read('model-info.json');reports={};count=0
 for cohort in read('evaluation/cohorts.json'):
  group=cohort['name']
  base=ROOT/'evaluation'/group;ins=json_rows(base/'inputs.jsonl');golds=json_rows(base/'targets.jsonl');require(len(ins)==len(golds)==cohort['rows'],'Cohort row mismatch')
  evidence=Counter();boundary=Counter();cases=defaultdict(list);features=defaultdict(Counter);records=[]
  for n,(x,g) in enumerate(zip(ins,golds),1):
   c=json.loads((base/'raw'/f'{n:03}.json').read_text());u=json.loads(x['messages'][1]['content']);target=g['target']
   require([m['role'] for m in x['messages']]==['system','user'],'Evaluation gold leaked into model input')
   require(x['messages'][0]['content']==PROMPT,'Prompt mismatch')
   require(x['key']==g['key']==c['key']==sha_text(x['messages'][1]['content']),'Input identity mismatch')
   require(c['rawSha256']==sha_text(c['raw']),'Cached raw integrity mismatch')
   require(c['identity']['freezeSha256']==digest(base/'source-freeze.json') and c['identity']['adapterSha256']==model['adapterSha256'] and c['identity']['inference']==model['inference'],'Wrong model/inference identity')
   if target:
    gs=score_output(json.dumps(target,ensure_ascii=False),u,target);require(gs['exact'] and gs['contract_pass'],'Gold invalid')
   result=score_row(c['raw'],u,target);records.append({'key':x['key'],**result});count+=1
   if g['expectation']=='evidence':
    s=result['score'] or {};ok=bool(s.get('exact'));contract=bool(s.get('contract_pass'));ask=result['clarification'] is not None
    rawscore=score_output(c['raw'],u,target)
    evidence.update(rows=1,postExact=int(ok),postContract=int(contract),clarifications=int(ask),rawExact=int(rawscore['exact']),rawContract=int(rawscore['contract_pass']))
    for origin in x['origins']:
     cases[origin.get('dialogueId',('legacy-row'+str(origin['sourceRow'])) if 'sourceRow' in origin else origin['caseId'])].append(ok);features[origin['feature']].update(rows=1,exact=int(ok),contract=int(contract),clarifications=int(ask))
   else:
    require(g['expectation']=='clarification' and target is None,'Unknown expectation')
    boundary.update(rows=1,explicitClarifications=int(result['clarification'] is not None))
  reports[group]={'evidence':dict(evidence),'ambiguity':dict(boundary),'byFeatureSnapshots':{k:dict(v) for k,v in sorted(features.items())},'dialoguesWithAllScoredEvidenceCorrect':sum(all(v) for v in cases.values()),'evidenceDialogueCount':len(cases),'apiSuccessRate':None}
  path=base/'expected-v37.json'
  if save_expected:path.write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n')
  else:require(records==json.loads(path.read_text()),'Cached output reproduction mismatch: '+group)
 require(reports['full60']['evidence']['postExact']==132 and reports['full60']['evidence']['postContract']==132,'Full60 changed')
 require(reports['full60']['dialoguesWithAllScoredEvidenceCorrect']==60,'Full60 case count changed')
 require(reports['ambiguity24']['evidence']['postExact']==12 and reports['ambiguity24']['ambiguity']['explicitClarifications']==12,'Ambiguity24 changed')
 require(reports['resolved24']['evidence']['postExact']==12 and reports['resolved24']['ambiguity']['explicitClarifications']==12,'Resolved24 changed')
 for group,size in [('legacy-first30',30),('legacy-second30',30),('legacy-third30',30),('paired24',24),('paired16',16)]:
  require(reports[group]['evidence']['postExact']==size and reports[group]['evidence']['postContract']==size,'Focused cohort changed:'+group)
 require(reports['paired24']['dialoguesWithAllScoredEvidenceCorrect']==12 and reports['paired16']['dialoguesWithAllScoredEvidenceCorrect']==8,'Paired dialogue mismatch')
 return count,reports

def data_checks():
 cases=read('data/multiturn60/scenarios60.json')['cases'];gold=json_rows(ROOT/'data/multiturn60/prefix-all-candidate.jsonl');idx=json_rows(ROOT/'data/multiturn60/prefix-all-index.jsonl')
 require(len(cases)==60 and len(gold)==len(idx)==179,'Scenario or target count mismatch')
 origins={(i['caseId'],i['round']):r for i,r in zip(idx,gold)};known={};nonn=0;renderer=0
 for c in cases:
  require(c['rounds']==len(c['turns']),'Round mismatch')
  require([d['role'] for d in c['dialogue']]==['user','assistant']*c['rounds'],'Non-alternating dialogue')
  calls={x['round']:x for x in c['modelCalls']}
  for n,t in enumerate(c['turns'],1):
   require(t['modelCallExpected']==(n in calls),'Call routing mismatch')
   require(c['dialogue'][2*n-1]['content']==t['assistantUtterance'],'Assistant transcript mismatch')
   if not t['modelCallExpected']:nonn+=1;continue
   m=calls[n];r=origins[(c['caseId'],n)];u=r['messages'][1]['content'];target=json.loads(r['messages'][2]['content'])
   require(target==m['target'],'Scenario/export gold mismatch')
   rendered=render_user_message(m['userTurns'],reference_time=datetime.fromisoformat(c['referenceTime']),server_time_trusted=c['serverTimeTrusted'])
   require(u==rendered,'Renderer byte mismatch');renderer+=1
   s=score_output(r['messages'][2]['content'],json.loads(u),target);require(s['exact'] and s['contract_pass'],'Invalid prefix gold')
   require(u not in known or known[u]==target,'Conflicting gold for identical input');known[u]=target
 require(nonn==61 and len(known)==132,'Unique call count mismatch')
 unique=json_rows(ROOT/'data/multiturn60/unique-prefix-candidate.jsonl')
 require({r['messages'][1]['content']:json.loads(r['messages'][2]['content']) for r in unique}==known,'Unique export mismatch')
 train=json_rows(ROOT/'data/training/last-run/train.jsonl');ti=json_rows(ROOT/'data/training/last-run/train-index.jsonl');new=json_rows(ROOT/'data/training/new-prefix40.jsonl');old=json_rows(ROOT/'data/training/train400.jsonl')
 require(len(train)==len(ti)==60 and len(old)==400 and len(new)==40,'Training counts mismatch')
 require([r for r,i in zip(train,ti) if i['source']=='multiturn60_untrained']==new,'New40 differs from actual last run')
 old_inputs={r['messages'][1]['content'] for r in old};new_inputs={r['messages'][1]['content'] for r in new}
 require(len(old_inputs)==400 and len(new_inputs)==40 and not old_inputs&new_inputs,'Training uniqueness mismatch')
 membership=json_rows(ROOT/'data/multiturn60/current-training-membership132.jsonl')
 require(len(membership)==132,'Membership length mismatch')
 for r,m in zip(unique,membership):
  u=r['messages'][1]['content'];require(m['inputSha256']==sha_text(u) and m['usedInSelected440Training']==(u in new_inputs),'Training membership mismatch')
 require(sum(m['usedInSelected440Training'] for m in membership)==40,'New60 membership count mismatch')
 require(Counter(c['rounds'] for c in cases)=={3:20,4:20,5:20},'Round distribution mismatch')
 require(Counter(c['feature'] for c in cases)==dict.fromkeys(['create','list','update','delete','completed_update'],12),'Feature distribution mismatch')
 return {'dialogues':60,'roundDistribution':{'3':20,'4':20,'5':20},'features':dict(Counter(c['feature'] for c in cases)),'modelCallGolds':179,'uniqueModelInputs':132,'nonModelCallRounds':61,'rendererByteMatches':renderer,'uniqueTrainingExamples':440,'lastRunNewExamples':40,'lastRunReplayExamples':20}

def verify(base_model=None,artifact_bundle=None):
 manifest=read('manifest.json')
 for name,entry in manifest['files'].items():
  p=ROOT/name;require(p.resolve().is_relative_to(ROOT.resolve()),'Manifest escapes bundle');require(p.is_file() and not p.is_symlink(),'Missing or linked file: '+name);require(p.stat().st_size==entry['bytes'] and digest(p)==entry['sha256'],'Hash mismatch: '+name)
 actual={str(p.relative_to(ROOT)) for p in ROOT.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p!=ROOT/'manifest.json'}
 require(actual==set(manifest['files']),'Unexpected or missing bundle files')
 model=read('model-info.json');require(digest(ROOT/model['promptPath'])==model['promptSha256'],'Prompt changed')
 if artifact_bundle:
  artifact_bundle=Path(artifact_bundle)
  require(digest(artifact_bundle/model['adapterPath']/'adapters.safetensors')==model['adapterSha256'],'Adapter changed')
  require(digest(artifact_bundle/model['adapterPath']/'adapter_config.json')==model['adapterConfigSha256'],'Adapter config changed')
  for name,h in model['exactBaseMetadataHashes'].items():require(digest(artifact_bundle/model['baseModelPath']/name)==h,'Base metadata changed')
 if base_model:require(digest(base_model)==model['baseWeightSha256'],'External base weight SHA mismatch')
 data=data_checks()
 import subprocess
 legacy=subprocess.run([sys.executable,'-I','-S',str(ROOT/'data/legacy184-prefix/verify.py')],capture_output=True,text=True,check=True)
 legacy_report=json.loads(legacy.stdout);require(legacy_report['status']=='PASS','Legacy conversion failed')
 data['legacy184Conversion']=legacy_report
 n,metrics=replay();require(metrics==read('evaluation/selected-v37-summary.json'),'Summary mismatch')
 return {'status':'PASS','manifestChecked':True,'filesChecked':len(manifest['files']),'trainedAdapterChecked':bool(artifact_bundle),'exactBaseMetadataChecked':bool(artifact_bundle),'baseWeightsChecked':bool(base_model),'data':data,'cachedOutputsReproduced':n,'evaluation':{k:{'evidence':v['evidence'],'ambiguity':v['ambiguity']} for k,v in metrics.items()},'newModelInferencePerformed':False,'optionalProviderIntegrationPerformed':False,'apiDatabaseReplayPerformed':False,'orangePiVerified':False}
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--base-model',type=Path,help='Optional exact model.safetensors file to hash');ap.add_argument('--artifact-bundle',type=Path,help='Optional unzipped Release bundle containing weights/tokenizer');a=ap.parse_args()
 try:print(json.dumps(verify(a.base_model,a.artifact_bundle),ensure_ascii=False,indent=2))
 except Exception as e:raise SystemExit(f'FAIL: {type(e).__name__}: {e}')
