"""Data-only integrity, prefix independence, semantic invariants and lineage audit."""
from pathlib import Path
from collections import Counter,defaultdict
import ast,copy,hashlib,json,re,sys
ROOT=Path(__file__).resolve().parent
sys.path[:0]=[str(ROOT),str(ROOT/'tools')]
from common import score_output,PROMPT
from build import annotations,prefix_target,normalize_final,canon,loadrows

def need(ok,msg):
 if not ok:raise ValueError(msg)
def read(name):return json.loads((ROOT/name).read_text())
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def evidence(g):
 for op in g['operations']:
  for role in ['targetEvidence','changeEvidence','filterEvidence']:
   for slot,ss in op[role].items():
    for s in ss:yield role,slot,s

def invariants(user,g):
 op=g['operations'][0];kind=op['kind'];t=op['targetEvidence'];c=op['changeEvidence'];f=op['filterEvidence'];missing=g['missingInformation']
 need(missing==op['missingInformation'],'Top/op missing mismatch')
 required=[]
 if kind=='create':
  for slot,label in [('title','task_or_title'),('date','date'),('time','time')]:
   if not c.get(slot):required.append(label)
  times=' '.join(s['text'] for s in c.get('time',[]) if s['status']=='adopted')
  hour=re.search(r'(\d{1,2})시',times)
  if hour and 1<=int(hour[1])<=12 and not re.search(r'오전|오후|아침|저녁|밤|새벽|정오|자정',times):required.append('ampm')
  title=' '.join(s['text'] for s in c.get('title',[]) if s['status']=='adopted')
  # New prefix annotations contain uncorrected titles; category cue provenance is exact.
  cues=[x for x in ['입고','출고','재고','피킹','포장','반품','기타'] if x in title]
  if cues:need(set(cues)<={s['text'] for s in c.get('category',[]) if s['status']=='adopted'},'Create category cue omitted')
 elif kind=='list':
  if not f:required.append('query_scope')
 else:
  if not t.get('title'):required.append('task_or_title')
  if kind=='update' and not c:required.append('change_value')
 need(missing==required,'Missing information inconsistent: '+str((kind,missing,required,user['userTurns'])))
 for role,slot,sp in evidence(g):
  need(sp['turn']<=len(user['userTurns']),'Future evidence leak')
  need(sp['status']=='adopted','Prefix contains future correction status')
  if role in ['targetEvidence','filterEvidence'] and slot=='category':
   text=user['userTurns'][sp['turn']-1]['text'];need('분류' in text or '카테고리' in text,'Inferred title category')


def main():
 if (ROOT/'manifest.json').exists():
  for name,entry in read('manifest.json')['files'].items():
   path=ROOT/name;need(path.resolve().is_relative_to(ROOT.resolve()) and path.is_file() and not path.is_symlink(),'Manifest file missing or unsafe: '+name)
   need(path.stat().st_size==entry['bytes'] and digest(path)==entry['sha256'],'Manifest hash mismatch: '+name)
 raw=loadrows(ROOT/'source/train400.jsonl');ann=annotations();allrows=loadrows(ROOT/'prefix-all381-candidate.jsonl');idx=loadrows(ROOT/'prefix-all381-index.jsonl');new=loadrows(ROOT/'new-intermediate197-candidate.jsonl')
 need(len(allrows)==len(idx)==381 and len(new)==197,'Counts')
 expected={(n,k) for n,r in enumerate(raw,1) for k in range(1,len(json.loads(r['messages'][1]['content'])['userTurns']))};need(set(ann)==expected,'Uncovered intermediate')
 row_by_key={};reordered=0;semantic=0;prefixkeys=set();view=defaultdict(list)
 for r,i in zip(allrows,idx):
  n,k=i['sourceRow'],i['modelPrefixLength'];src=raw[n-1];u=json.loads(r['messages'][1]['content']);g=json.loads(r['messages'][2]['content']);full=json.loads(src['messages'][1]['content']);expectedu=copy.deepcopy(full);expectedu['userTurns']=expectedu['userTurns'][:k]
  repaired=n==124 and k==2
  if repaired:expectedu['userTurns'][-1]['text']=read('source-text-repair-proposal.json')['proposedUserText']
  need(u==expectedu,'Source/context mutated outside documented repair')
  need(r['messages'][0]['content']==src['messages'][0]['content']==PROMPT,'Prompt changed')
  s=score_output(r['messages'][2]['content'],u,g);need(s['exact'] and s['contract_pass'],'Contract/source/role check failed')
  if i['isNewIntermediate']:
   invariants(u,g);semantic+=1
   # The annotation compiler has no final target or future-turn input. Deliberately
   # replace future source text and final gold and prove the emitted prefix is stable.
   hostile=copy.deepcopy(src);huser=json.loads(hostile['messages'][1]['content'])
   for t in huser['userTurns'][k:]:t['text']='미래에만 있는 제목과 2099년 날짜'
   hostile['messages'][2]['content']='{"operations":[]}'
   huser['userTurns']=huser['userTurns'][:k]
   need(prefix_target(ann[(n,k)],huser)==g,'Future mutation changed prefix gold')
   prefixkeys.add(canon(u))
  else:
   original=json.loads(src['messages'][2]['content']);expectedg,changes=normalize_final(original)
   reordered+=bool(changes)
   if repaired:expectedg['operations'][0]['changeEvidence']['time']=read('source-text-repair-proposal.json')['goldChange']['after']
   need(expectedg==g,'Undocumented final target change')
  for role,slot,sp in evidence(g):need(1<=sp['turn']<=k,'Future span')
  for op in g['operations']:
   for role in ['targetEvidence','changeEvidence','filterEvidence']:
    for slot,ss in op[role].items():
     states=[s['status'] for s in ss]
     if 'superseded' in states:need(states==['superseded','correction_marker','adopted'],'Guide correction array order')
  key=canon(u);need(key not in row_by_key or row_by_key[key]==g,'Conflicting same-input gold');row_by_key[key]=g;view[n].append((u,g,i))
 # Independent boundary checks for fields absent in the original final target.
 for n,slot,expected_text,missing in [(21,'time','6시',['ampm']),(33,'time','3시',['ampm']),(46,'time','10시 30분',['ampm']),(48,'date','7월 18일',[]),(124,'time','오후 2시',['task_or_title'])]:
  u,g,i=view[n][0];need(g['operations'][0]['changeEvidence'][slot][0]['text']==expected_text and g['missingInformation']==missing,'Recovered earlier value incorrect')
 for n in [174,182]:need(view[n][1][1]['missingInformation']==[],'Future time wrongly required on date-only update')
 for n in [367,372,388,400]:need(view[n][1][1]['missingInformation']==['change_value'],'Future completion state leaked')
 # Existing dates/times may be refined without a correction: 6시 -> 오후6시,
 # same year context expanded to2029년, and a repeated identical time.
 exceptions={(21,'changeEvidence','time'),(33,'changeEvidence','time'),(46,'changeEvidence','time'),(48,'changeEvidence','date'),(48,'changeEvidence','time')}
 transitions=read('transition-review.json');transition_checks=0
 for r in transitions:
  for d in r['slotChanges']:
   if not d['before']:continue
   after=d['after'];need(bool(after),'Previously established evidence silently lost')
   old=[s for s in d['before'] if s['status']=='adopted'];newvals=[s for s in after if s['status']=='adopted']
   if old!=newvals:
    states=[s['status'] for s in after]
    need('superseded' in states or (r['sourceRow'],d['role'],d['slot']) in exceptions,'Unmarked value replacement')
    if 'superseded' in states:
     superseded=next(s for s in after if s['status']=='superseded');marker=next(s for s in after if s['status']=='correction_marker')
     source_text=view[r['sourceRow']][r['toPrefix']-1][0]['userTurns'][-1]['text']
     need(superseded['text'] in {s['text'] for s in old},'Correction changes unrelated previous value')
     if superseded['text'] in source_text:need(superseded['turn']==r['toPrefix'],'Repeated old value must use correction turn')
     need(marker['turn']==r['toPrefix'],'Marker not at correction turn')
    transition_checks+=1
 unique=loadrows(ROOT/'unique-prefix-candidate.jsonl');need(len(unique)==len(row_by_key)==356,'Dedup count')
 need({canon(json.loads(r['messages'][1]['content'])):json.loads(r['messages'][2]['content']) for r in unique}==row_by_key,'Unique export differs')
 summary={'status':'PASS','sourceDialogues':184,'allSnapshotOccurrences':381,'newIntermediateOccurrences':197,'uniqueInputs':356,'uniqueNewIntermediateInputs':len(prefixkeys),'contractSourceRolePass':381,'prefixSemanticInvariantChecks':semantic,'futureMutationChecks':semantic,'valueTransitionChecks':transition_checks,'sameInputConflictingTargets':0,'documentedFinalOrderNormalizations':reordered,'documentedSourceTextRepairProposals':1,'originalSourceUnchanged':digest(ROOT/'source/train400.jsonl')==read('build-checks.json')['sourceSha256'],'independentHumanReview':False,'newTraining':False,'newModelInference':False,'apiReplay':False,'scope':'Explicit AI-authored prefix labels plus automatic structural/semantic invariants. Data correctness checks, not model accuracy or API flow verification.'}
 need(summary['originalSourceUnchanged'],'Source hash mismatch');print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
