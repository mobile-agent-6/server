"""Source-only list correction parser adapted from v18; accepts one correction.
Called only for list.time correction repair; complete-history consumption remains.
"""
import re,copy
from .evidence_postprocess_v15 import source_plan,NoPlan,MARK,UNCERTAIN,OUTSIDE,clean_title,QUERY,END
from .evidence_postprocess_v3 import DATE,TIME
from .evidence_postprocess_v18_candidate import sync_query_ampm

def list_correction_history(turns,trusted):
 if list(turns)!=list(range(1,len(turns)+1)) or not 2<=len(turns)<=8:raise NoPlan()
 if any(not isinstance(t,str) or len(t)>2000 or OUTSIDE.search(t) or UNCERTAIN.search(t) for t in turns.values()):raise NoPlan()
 first=source_plan({1:turns[1]},trusted)
 if first['operations'][0]['kind']!='list':raise NoPlan()
 value=copy.deepcopy(first);op=value['operations'][0];mapping=op['filterEvidence'];corrected=set()
 def span(text,n,status='adopted',start=None):
  positions=[m.start() for m in re.finditer(re.escape(text),turns[n])]
  if not positions or (len(positions)>1 and start is None):raise NoPlan()
  if start is not None and start not in positions:raise NoPlan()
  v={'turn':n,'text':text,'status':status}
  if len(positions)>1:v['occurrence']=positions.index(start)+1
  return v
 def check(slot,text):
  if slot in ('date','time'):
   if not re.fullmatch(DATE if slot=='date' else TIME,text):raise NoPlan()
  else:clean_title(text,explicit=True)
 def replace(slot,new,n,marker,markstart,newstart,old=None,oldstart=None):
  if slot in corrected:raise NoPlan() # Do not flatten a repeated same-slot history.
  ss=mapping.get(slot,[])
  if len(ss)!=1 or ss[0]['status']!='adopted':raise NoPlan()
  check(slot,new)
  if new==ss[0]['text']:raise NoPlan()
  if old is not None:
   if old!=ss[0]['text']:raise NoPlan()
   discarded=span(old,n,'superseded',oldstart)
  else:discarded=dict(ss[0],status='superseded')
  mapping[slot]=[discarded,span(marker,n,'correction_marker',markstart),span(new,n,'adopted',newstart)]
  corrected.add(slot)
 for n in range(2,len(turns)+1):
  text=turns[n];marks=list(MARK.finditer(text))
  if len(marks)>1:raise NoPlan()
  if marks:
   m=re.fullmatch(rf'\s*(?P<mark>아니|아니다)[,，]?\s+(?P<label>날짜|시간|시각|제목)(?:은|는) (?P<new>.+?)(?:으로|로) {QUERY}{END}',text)
   if m:
    slot={'날짜':'date','시간':'time','시각':'time','제목':'title'}[m['label']]
    replace(slot,m['new'],n,m['mark'],m.start('mark'),m.start('new'));continue
   m=re.fullmatch(rf'\s*(?P<old>.+?) (?P<mark>말고|대신) (?P<new>.+?)(?: 일정이야| 일정(?:을|를)? {QUERY}|(?:으로|로) {QUERY}){END}',text)
   if not m:raise NoPlan()
   slots=[slot for slot,ss in mapping.items() if slot in ('title','date','time') and len(ss)==1 and ss[0]['status']=='adopted' and ss[0]['text']==m['old']]
   if len(slots)!=1:raise NoPlan()
   replace(slots[0],m['new'],n,m['mark'],m.start('mark'),m.start('new'),m['old'],m.start('old'));continue
  m=re.fullmatch(rf'\s*(?P<label>제목|날짜|시간|시각)(?:은|는) (?P<new>.+?)(?:(?:으로|로) {QUERY}|이야|야){END}',text)
  if not m:raise NoPlan()
  slot={'제목':'title','날짜':'date','시간':'time','시각':'time'}[m['label']]
  if slot in mapping:raise NoPlan()
  check(slot,m['new']);mapping[slot]=[span(m['new'],n,start=m.start('new'))]
 if len(corrected)<1:raise NoPlan()
 sync_query_ampm(value)
 return value
