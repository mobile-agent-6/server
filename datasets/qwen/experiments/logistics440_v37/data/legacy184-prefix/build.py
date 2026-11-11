"""Compile explicitly authored prefix annotations; never derive prefix gold from final gold."""
from pathlib import Path
from collections import Counter,defaultdict
import json,sys,copy,re,hashlib,unicodedata
ROOT=Path(__file__).resolve().parent
SOURCE=ROOT/'source/train400.jsonl'
sys.path.insert(0,str(ROOT/'tools'))
from common import score_output,PROMPT
ROLES=['targetEvidence','changeEvidence','filterEvidence']
def loadrows(p):return [json.loads(x) for x in p.read_text().splitlines() if x]
def canon(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def save(name,x):(ROOT/name).write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
def lines(name,x):(ROOT/name).write_text(''.join(json.dumps(v,ensure_ascii=False)+'\n' for v in x))
def annotations():
 out={}
 for n,line in enumerate((ROOT/'annotations.tsv').read_text().splitlines(),1):
  if not line or line.startswith('#'):continue
  parts=line.split('|');assert len(parts)==7,(n,len(parts),line)
  ids,turn,kind,target,change,filters,missing=parts
  for row in map(int,ids.split(',')):
   key=(row,int(turn));assert key not in out,key;out[key]=(kind,[target,change,filters],missing.split(',') if missing else [])
 return out

def prefix_target(annotation,user):
 kind,maps,missing=annotation;op={'index':1,'kind':kind,**{r:{} for r in ROLES},'unsupportedEvidence':[],'missingInformation':missing.copy()}
 turns={t['turn']:t['text'] for t in user['userTurns']}
 for role,spec in zip(ROLES,maps):
  for token in spec.split(';') if spec else []:
   slot,value=token.split('=',1);m=re.match(r'^(\d+):(.*)$',value)
   turn,text=(int(m[1]),m[2]) if m else (1,value)
   assert turn in turns,(turn,user)
   hits=list(re.finditer(re.escape(text),turns[turn]));assert len(hits)==1,('nonunique annotation',role,slot,text,turns[turn])
   op[role][slot]=[{'turn':turn,'text':text,'status':'adopted'}]
 return {'operations':[op],'unsupportedEvidence':[],'missingInformation':missing.copy()}

def normalize_final(target):
 out=copy.deepcopy(target);changes=[]
 for op in out['operations']:
  for role in ROLES:
   for slot,spans in op[role].items():
    states=[s['status'] for s in spans]
    if len(spans)==3 and sorted(states)==['adopted','correction_marker','superseded']:
     ordered=sorted(spans,key=lambda s:['superseded','correction_marker','adopted'].index(s['status']))
     if spans!=ordered:changes.append({'role':role,'slot':slot,'before':spans,'after':ordered})
     op[role][slot]=ordered
 return out,changes

def main():
 src=loadrows(SOURCE);ann=annotations();expected={(n,k) for n,r in enumerate(src,1) for k in range(1,len(json.loads(r['messages'][1]['content'])['userTurns']))}
 assert set(ann)==expected,{'missing':sorted(expected-set(ann)),'extra':sorted(set(ann)-expected)}
 allrows=[];idx=[];new=[];finals=[];norm=[];checks=[];views=[];alluser={};unique={};origins=defaultdict(list);transitions=[]
 for row,r in enumerate(src,1):
  full=json.loads(r['messages'][1]['content']);length=len(full['userTurns'])
  if length==1:continue
  finalraw=r['messages'][2]['content'];finaltarget=json.loads(finalraw);normalized,changes=normalize_final(finaltarget)
  if changes:norm.append({'sourceRow':row,'changes':changes})
  feature='completed_update' if finaltarget['operations'][0]['kind']=='update' and 'completed' in finaltarget['operations'][0]['changeEvidence'] else finaltarget['operations'][0]['kind']
  views.append(f'원본 {row}행 | {feature} | 사용자 발화 {length}개')
  previous=None
  for k in range(1,length+1):
   user=copy.deepcopy(full);user['userTurns']=user['userTurns'][:k]
   repaired=(row==124 and k==length)
   if repaired:
    assert user['userTurns'][-1]['text']=='파손 주의 표지 부착 확인이야, 아 오후 4시로'
    user['userTurns'][-1]['text']='파손 주의 표지 부착 확인이야, 아니 오후 4시로'
   target=prefix_target(ann[(row,k)],user) if k<length else copy.deepcopy(normalized)
   if repaired:
    target['operations'][0]['changeEvidence']['time']=[{'turn':1,'text':'오후 2시','status':'superseded'},{'turn':2,'text':'아니','status':'correction_marker'},{'turn':2,'text':'오후 4시','status':'adopted'}]
   score=score_output(json.dumps(target,ensure_ascii=False),user,target)
   assert score['exact'] and score['contract_pass'],(row,k,score)
   for op in target['operations']:
    assert op['missingInformation']==target['missingInformation']
    for role in ROLES:
     for slot,spans in op[role].items():
      if any(x['status']=='superseded' for x in spans):assert [x['status'] for x in spans]==['superseded','correction_marker','adopted'],(row,k,role,slot)
      for sp in spans:assert 1<=sp['turn']<=k
   message=canon(user);gold=canon(target);key=hashlib.sha256(message.encode()).hexdigest()
   assert key not in unique or unique[key]['messages'][2]['content']==gold,('contradictory prefix',row,k)
   out={'messages':[{'role':'system','content':r['messages'][0]['content']},{'role':'user','content':message},{'role':'assistant','content':gold}]}
   entry={'sourceRow':row,'modelPrefixLength':k,'sourceModelTurnCount':length,'apiDialogueTurn':None,'feature':feature,'inputSha256':key,'targetSha256':hashlib.sha256(gold.encode()).hexdigest(),'isNewIntermediate':k<length,'split':'existing_training_source_derivative_not_independent_test','sourceTextRepaired':repaired,'annotationMethod':'explicit_prefix_annotations_without_final_target' if k<length else 'original_final_with_documented_correction_order_normalization'}
   allrows.append(out);idx.append(entry);origins[key].append(entry);unique[key]=out
   if k<length:new.append(out)
   else:finals.append(out)
   views.extend([f'모델 호출 시점 {k}/{length}',*[f"사용자 {x['turn']}: {x['text']}" for x in user['userTurns']], '정답: '+json.dumps(target,ensure_ascii=False),''])
   if previous:
    # Expose slot changes for semantic review; presence alone is not correctness.
    deltas=[]
    for role in ROLES:
     a=previous['operations'][0][role];b=target['operations'][0][role]
     for slot in sorted(set(a)|set(b)):
      if a.get(slot)!=b.get(slot):deltas.append({'role':role,'slot':slot,'before':a.get(slot,[]),'after':b.get(slot,[])})
    transitions.append({'sourceRow':row,'fromPrefix':k-1,'toPrefix':k,'newText':user['userTurns'][-1]['text'],'slotChanges':deltas,'missingBefore':previous['missingInformation'],'missingAfter':target['missingInformation']})
   previous=target
 assert len(allrows)==381 and len(new)==197 and len(finals)==184
 lines('prefix-all381-candidate.jsonl',allrows);lines('prefix-all381-index.jsonl',idx);lines('new-intermediate197-candidate.jsonl',new);lines('final184-reviewed-candidate.jsonl',finals)
 lines('unique-prefix-candidate.jsonl',list(unique.values()));lines('unique-prefix-index.jsonl',[{'inputSha256':key,'origins':origins[key]} for key in unique])
 save('final-order-normalizations.json',norm);save('transition-review.json',transitions)
 (ROOT/'턴별정답_눈검수.txt').write_text('\n'.join(views)+'\n')
 guide=ROOT/'source/데이터셋_제작_가이드.txt'
 save('build-checks.json',{'status':'STRUCTURAL_PASS_SEE_VERIFICATION_JSON','sourceSha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),'guideSha256':hashlib.sha256(guide.read_bytes()).hexdigest(),'sourceDialogues':184,'newIntermediateOccurrences':len(new),'allSnapshotOccurrences':len(allrows),'uniqueInputs':len(unique),'uniqueIntermediateInputs':len({canon(json.loads(r['messages'][1]['content'])) for r in new}),'sourceTextRepairs':1,'finalRowsReordered':len(norm),'contractSourceRolePass':len(allrows),'sameInputConflictingTargets':0,'newTraining':False,'newInference':False,'apiReplay':False})
 print((ROOT/'build-checks.json').read_text())
if __name__=='__main__':main()
