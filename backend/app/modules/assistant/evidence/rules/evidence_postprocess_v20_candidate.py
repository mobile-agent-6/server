"""Candidate only: strictly consumed partial requests and explicit slot dialogue.

No case IDs, stored answers, schedule titles or database access. A source plan
must consume every turn; implicit overwrites, unknown clauses, repeated slot
corrections and ambiguous language fall back to the previous version. No writes.
"""
import json,re,copy
from dataclasses import dataclass
from .evidence_postprocess_v18_candidate import apply_rules as previous
from .evidence_postprocess_v15 import (source_plan,model_envelope,clean_title,NoPlan,
 ClarificationRequired,MARK,UNCERTAIN,OUTSIDE,STATE,COMPLETE,QUERY,CHANGE,DELETE,END)
from .evidence_postprocess_v3 import DATE,TIME,dump
from .category_postprocess import CUES,_validate,_strict_pairs,_reject_constant
from app.modules.assistant.evidence.normalize import resolve_time,TimeStatus
VERSION='evidence-rules-v20-candidate'
TIME=rf'(?:{TIME}|새벽 12시)'
COMP=rf'(?:{COMPLETE}|완료 (?:처리를 취소해|처리를 해제해|취소해)\s*(?:줘|주세요))'
ROLES=('targetEvidence','changeEvidence','filterEvidence')
LABEL={'제목':'title','날짜':'date','시간':'time','시각':'time'}
@dataclass(frozen=True)
class Result:
 raw:str
 changed:bool
 reasons:tuple[str,...]
 version:str=VERSION

def source_history(turns,trusted):
 if not trusted or list(turns)!=list(range(1,len(turns)+1)) or not 1<=len(turns)<=8:raise NoPlan()
 if any(not isinstance(t,str) or len(t)>2000 or OUTSIDE.search(t) or UNCERTAIN.search(t) for t in turns.values()):raise NoPlan()
 if MARK.search(turns[1]):raise NoPlan()
 op={'index':1,'kind':None,**{r:{} for r in ROLES},'unsupportedEvidence':[],'missingInformation':[]}
 def span(text,n,status='adopted',start=None):
  positions=[m.start() for m in re.finditer(re.escape(text),turns[n])]
  if not positions or (len(positions)>1 and start is None):raise NoPlan()
  if start is not None and start not in positions:raise NoPlan()
  out={'turn':n,'text':text,'status':status}
  if len(positions)>1:out['occurrence']=positions.index(start)+1
  return out
 def put(role,slot,text,n,start=None):
  if slot in op[role]:raise NoPlan()
  op[role][slot]=[span(text,n,start=start)]
 def head(text):
  """Existing target only. Prefix conditions then one explicit title/placeholder."""
  rest=text.strip();out={}
  while True:
   found=False
   for slot,pattern in [('date',DATE),('time',TIME),('completedState',STATE)]:
    m=re.match(rf'(?P<val>{pattern})(?:에|의)?\s+',rest)
    if m:
     if slot in out:raise NoPlan()
     out[slot]=[span(m['val'],1)];rest=rest[m.end():];found=True;break
   if not found:break
  if rest in ('그 일정','그 일정을','그 일정의','일정 하나','그 작업 일정'):return out
  rest=re.sub(r' 일정(?:을|를|의)?$','',rest)
  clean_title(rest);out['title']=[span(rest,1)];return out
 first=turns[1];enhanced=True
 # Explicit completed-state verbs override a model's mistaken delete kind only
 # after the complete target and all follow-ups have been parsed.
 m=re.fullmatch(rf'\s*(?P<head>.+?) (?P<cue>{COMP}){END}',first)
 explicit_completion=bool(m)
 if m:
  op['kind']='update';op['targetEvidence']=head(m['head']);put('changeEvidence','completed',m['cue'],1,m.start('cue'))
  enhanced=('취소' in m['cue'] or 'title' not in op['targetEvidence'])
 else:
  # Missing-title create commands are deliberately exact grammar, not removal
  # of arbitrary words such as "일정" from a guessed title.
  m=re.fullmatch(rf'\s*(?:(?P<date>{DATE}) )?(?:(?P<time>{TIME})(?:에)? )?(?:새 일정을|일정을|일정|일정 하나) (?:새로 )?(?:등록해|추가해|만들어)\s*(?:줘|주세요){END}',first)
  if m:
   op['kind']='create'
   for slot in ('date','time'):
    if m[slot]:put('changeEvidence',slot,m[slot],1,m.start(slot))
  else:
   m=re.fullmatch(rf'\s*(?P<head>(?:(?:{DATE}|{TIME}|{STATE})(?:에)? )*(?:그 일정|일정 하나))(?:을|를)? {DELETE}{END}',first)
   if m:op['kind']='delete';op['targetEvidence']=head(m['head'])
   else:
    m=re.fullmatch(rf'\s*그 일정(?:을|의)? (?:(?P<slot>제목|날짜|시간|시각|상태)(?:을|를) )?{CHANGE}{END}',first)
    if m:op['kind']='update'
    else:
     m=re.fullmatch(rf'\s*그 일정(?:을|의)? (?:(?:날짜|시간|시각)(?:을|를) )?(?P<val>{DATE})(?: (?P<time>{TIME}))?(?:으로|로) {CHANGE}{END}',first)
     if m:
      op['kind']='update';put('changeEvidence','date',m['val'],1,m.start('val'))
      if m['time']:put('changeEvidence','time',m['time'],1,m.start('time'))
     else:
      m=re.fullmatch(rf'\s*그 일정(?:을|의)? (?:(?:시간|시각)(?:을|를) )?(?P<val>{TIME})(?:으로|로) {CHANGE}{END}',first)
      if m:op['kind']='update';put('changeEvidence','time',m['val'],1,m.start('val'))
      else:op=copy.deepcopy(source_plan({1:first},trusted)['operations'][0]);enhanced=False
 role='changeEvidence' if op['kind']=='create' else 'filterEvidence' if op['kind']=='list' else 'targetEvidence'
 corrected=set()
 def typed(slot,val):
  if slot=='title':clean_title(val,explicit=True)
  elif slot in ('date','time','completed'):
   if not re.fullmatch({'date':DATE,'time':TIME,'completed':COMP}[slot],val):raise NoPlan()
  else:raise NoPlan()
 def correct(dest,slot,val,n,marker,markstart,valstart,old=None,oldstart=None):
  key=(dest,slot);ss=op[dest].get(slot,[])
  if key in corrected or len(ss)!=1 or ss[0]['status']!='adopted':raise NoPlan()
  typed(slot,val)
  if val==ss[0]['text'] or (old is not None and old!=ss[0]['text']):raise NoPlan()
  # Rebuilding a create-title correction also needs category history. Leave it
  # to previous rules rather than silently retaining a stale inferred category.
  if op['kind']=='create' and slot=='title':raise NoPlan()
  discarded=span(old,n,'superseded',oldstart) if old is not None else dict(ss[0],status='superseded')
  op[dest][slot]=[discarded,span(marker,n,'correction_marker',markstart),span(val,n,start=valstart)];corrected.add(key)
 for n in range(2,len(turns)+1):
  text=turns[n]
  action=QUERY if op['kind']=='list' else DELETE if op['kind']=='delete' else CHANGE
  if MARK.search(text):
   if len(MARK.findall(text))!=1:raise NoPlan()
   m=re.fullmatch(rf'\s*(?P<mark>아니|아니다)[,，]?\s+(?P<label>날짜|시간|시각|제목)(?:은|는) (?P<val>.+?)(?:으로|로) (?:{action}|해\s*(?:줘|주세요)){END}',text)
   if m:
    dest='changeEvidence' if op['kind']=='update' else role
    correct(dest,LABEL[m['label']],m['val'],n,m['mark'],m.start('mark'),m.start('val'));continue
   m=re.fullmatch(rf'\s*(?P<mark>아니|아니다)[,，]?\s+(?P<val>{COMP}){END}',text)
   if m and op['kind']=='update':correct('changeEvidence','completed',m['val'],n,m['mark'],m.start('mark'),m.start('val'));continue
   # All repeated-old corrections require one exact old-value match across roles.
   suffix=(rf' 일정이야| 일정(?:을|를)? {QUERY}|(?:으로|로) {QUERY}' if op['kind']=='list' else rf'(?:으로|로) {CHANGE}' if op['kind']=='update' else r'(?:으로|로) 등록해\s*(?:줘|주세요)' if op['kind']=='create' else rf'(?:을|를) {DELETE}')
   m=re.fullmatch(rf'\s*(?P<old>.+?) (?P<mark>말고|대신) (?P<val>.+?)(?:{suffix}){END}',text)
   if not m:raise NoPlan()
   choices=[(dest,slot) for dest in ROLES for slot,ss in op[dest].items() if slot in ('title','date','time') and len(ss)==1 and ss[0]['text']==m['old']]
   if len(choices)!=1:raise NoPlan()
   dest,slot=choices[0]
   if slot=='title':clean_title(m['val']) # Unlabelled date/time/state is not a proven title.
   correct(dest,slot,m['val'],n,m['mark'],m.start('mark'),m.start('val'),m['old'],m.start('old'));continue
  enhanced=True
  m=re.fullmatch(rf'\s*(?P<label>새 제목|대상 제목|일정 이름|제목|이름)(?:은|는) (?P<val>.+?)(?:이야|야){END}',text)
  if m:
   dest=role
   if m['label']=='새 제목':
    if op['kind']!='update':raise NoPlan()
    dest='changeEvidence'
   if m['label']=='대상 제목' and role!='targetEvidence':raise NoPlan()
   clean_title(m['val'],explicit=True);put(dest,'title',m['val'],n,m.start('val'));continue
  m=re.fullmatch(rf'\s*제목은 (?P<val>.+?)(?:으로|로) {QUERY}{END}',text)
  if m and op['kind']=='list':clean_title(m['val'],explicit=True);put(role,'title',m['val'],n,m.start('val'));continue
  m=re.fullmatch(rf'\s*(?P<target>대상 )?(?P<label>날짜|시간|시각)(?:은|는) (?P<val>.+?)(?:이야|야){END}',text)
  if m:
   if m['target'] and role!='targetEvidence':raise NoPlan()
   if op['kind']=='update' and not m['target']:raise NoPlan() # target/change ambiguity
   slot=LABEL[m['label']];typed(slot,m['val']);put(role,slot,m['val'],n,m.start('val'));continue
  m=re.fullmatch(rf'\s*(?:(?P<date>{DATE}) )?(?P<time>{TIME})에 시작해{END}',text)
  if m and op['kind']=='create':
   for slot in ('date','time'):
    if m[slot]:put(role,slot,m[slot],n,m.start(slot))
   continue
  m=re.fullmatch(rf'\s*(?P<label>날짜|시간|시각)(?:을|를) (?P<val>.+?)(?:으로|로) {CHANGE}{END}',text)
  if m and op['kind']=='update':slot=LABEL[m['label']];typed(slot,m['val']);put('changeEvidence',slot,m['val'],n,m.start('val'));continue
  m=re.fullmatch(rf'\s*(?P<val>{STATE}) 일정만 {QUERY}{END}',text)
  if m and op['kind']=='list':put(role,'completed',m['val'],n,m.start('val'));continue
  cat=rf'(?:{"|".join(map(re.escape,CUES))}) 분류'
  m=re.fullmatch(rf'\s*(?:분류는 )?(?P<val>{cat})(?:로 {QUERY}|에서 {QUERY}|의 일정이야){END}',text)
  if m and role in ('targetEvidence','filterEvidence'):
   if op['kind']!='list' and not text.endswith('의 일정이야'):raise NoPlan()
   put(role,'category',m['val'],n,m.start('val'));continue
  raise NoPlan()
 # Narrow entry: legacy single commands and single old/new corrections remain
 # on v18, which has existing title/time repairs and historical label order.
 # A single explicit lookup-title replacement also has a complete source
 # history. All other legacy single corrections keep their previous handling.
 single_lookup_title=(op['kind']=='list' and corrected=={('filterEvidence','title')})
 if not enhanced and len(corrected)<2 and not single_lookup_title:raise NoPlan()
 # Compute hints from the fully accumulated evidence; never use DB guesses.
 missing=[]
 if op['kind']!='list' and 'title' not in op[role]:missing.append('task_or_title')
 c=op['changeEvidence']
 if op['kind']=='create':
  if 'title' in c and 'category' not in c:
   ss=c['title']
   if len(ss)!=1:raise NoPlan()
   title=ss[0];matches=[m for m in re.finditer('|'.join(map(re.escape,CUES)),title['text'])]
   if len(matches)>1:raise NoPlan()
   if matches:
    positions=[m.start() for m in re.finditer(re.escape(title['text']),turns[title['turn']])]
    pos=positions[title.get('occurrence',1)-1];put('changeEvidence','category',matches[0].group(),title['turn'],pos+matches[0].start())
  for slot in ('date','time'):
   if slot not in c:missing.append(slot)
 if op['kind']=='update' and not c:missing.append('change_value')
 time_map=op['filterEvidence'] if op['kind']=='list' else c
 times=[s['text'] for s in time_map.get('time',[]) if s['status']=='adopted']
 if len(times)>1:raise NoPlan()
 if times and resolve_time(times).status is TimeStatus.NEEDS_AMPM:missing.append('ampm')
 if op['kind']=='list' and not op[role]:missing.append('query_scope')
 op['missingInformation']=missing
 return {'operations':[op],'unsupportedEvidence':[],'missingInformation':list(missing)},explicit_completion

def apply_rules(raw,*,user_turns,reference,server_time_trusted):
 kw=dict(user_turns=user_turns,reference=reference,server_time_trusted=server_time_trusted)
 try:
  kind=model_envelope(raw);json.loads(raw,object_pairs_hook=_strict_pairs,parse_constant=_reject_constant)
  value,completion=source_history(user_turns,server_time_trusted)
  if kind!=value['operations'][0]['kind'] and not (completion and kind=='delete'):raise NoPlan()
  proposed=dump(value);_validate(proposed,user_turns,reference)
  return Result(proposed,proposed!=raw,('source_verified_partial_dialogue',) if proposed!=raw else ())
 except (ValueError,KeyError,TypeError,IndexError,AttributeError,RecursionError):pass
 p=previous(raw,**kw)
 return Result(p.raw,p.raw!=raw,tuple(p.reasons))
