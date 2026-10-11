"""Qwen evaluation v2: actual observations, guarded writes and question checks.
Self-contained local proposal. All schedules are in memory. No oracle is passed to Qwen.
"""
from __future__ import annotations
import argparse,copy,hashlib,json,sys,time,urllib.request,urllib.error
from datetime import datetime,timezone
from pathlib import Path
from uuid import uuid4
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'runtime'))
from replay_multiturn_pilot import load_cases,MemorySchedules
from schedule_assistant.service import ConversationService
from schedule_assistant.actions import parse_action,ActionError
from schedule_assistant.clients import ScheduleError
from guards import evidence,validate_write,check_question,intent_from_users,task_evidence,query_scope,required_question,query_date_range
PROMPT=(ROOT/'prompt.txt').read_text(encoding='utf-8')
OPTIONS={'temperature':0,'seed':42,'num_predict':1024,'num_ctx':8192}

def obj(props,required=None):
 return {'type':'object','properties':props,'required':list(props) if required is None else required,'additionalProperties':False}
def branch(name,request,message=None):
 return obj({'action':{'type':'string','enum':[name]},'message':message or {'type':'null'},'request':request})
def schema_for(context):
 category={'type':'string','enum':['INBOUND','INVENTORY','OUTBOUND','RETURN','OTHER']}
 nullable_alarm={'anyOf':[{'type':'null'},{'type':'integer','enum':[10]}]}
 fields={'title':{'type':'string','minLength':1,'maxLength':100},'scheduledAt':{'type':'string'},'category':category,'reminderMinutesBefore':nullable_alarm}
 choices=[branch('ASK',{'type':'null'},{'type':'string','minLength':1,'maxLength':240}),branch('ANSWER',{'type':'null'})]
 if not context['lookup_completed_this_turn']:
  choices.append(branch('LIST',obj({'from':{'type':'string'},'to':{'type':'string'},'category':category,'completed':{'type':'boolean'}},['from','to'])))
 if context['verified_user_datetime'] and not context['user_time_in_past'] and context.get('task_evidence',{}).get('status')=='confirmed':
  choices.append(branch('CREATE',obj(fields)))
 ids=[x['id'] for x in context['schedule_candidates']]
 if ids:
  target={'type':'string','enum':ids}
  choices += [branch('CONFIRM_DELETE',obj({'targetId':target})),branch('CONFIRM_UPDATE',obj({'targetId':target,'changes':obj({**fields,'completed':{'type':'boolean'}},[])}))]
 # Restrict only supported explicit intents/stages. Unknown requests keep the
 # generic schema and receive manual review; this is a hybrid system experiment.
 intent=context.get('intent')
 allowed=None
 if intent=='create': allowed={'ASK','CREATE'}
 elif intent=='query': allowed={'ANSWER'} if context['lookup_completed_this_turn'] else ({'ASK'} if context['date_missing'] else {'LIST'})
 elif intent in {'update','delete'}:
  if ids: allowed={'ASK','CONFIRM_UPDATE' if intent=='update' else 'CONFIRM_DELETE'}
  elif context['lookup_completed_this_turn']: allowed={'ANSWER','ASK'}
  else: allowed={'ASK'} if context['date_missing'] else {'LIST'}
 scope=context.get('query_scope',{})
 for choice in choices:
  if choice['properties']['action']['enum'][0]=='LIST' and scope.get('known'):
   request=choice['properties']['request']
   if scope['category'] is None: request['properties'].pop('category',None)
   else:
    request['properties']['category']={'type':'string','enum':[scope['category']]}
    request['required'].append('category')
 bounds=context.get('query_date_range',{})
 for choice in choices:
  if choice['properties']['action']['enum'][0]=='LIST' and bounds.get('status')=='known':
   for key in ('from','to'): choice['properties']['request']['properties'][key]={'type':'string','enum':[bounds[key]]}
 if allowed is not None: choices=[b for b in choices if b['properties']['action']['enum'][0] in allowed]
 return {'anyOf':choices}

def request_json(route,payload=None,timeout=120):
 data=None if payload is None else json.dumps(payload,ensure_ascii=False).encode('utf-8')
 req=urllib.request.Request('http://127.0.0.1:11434'+route,data=data,headers={'Content-Type':'application/json'})
 try:
  with urllib.request.urlopen(req,timeout=timeout) as response: return json.load(response)
 except (urllib.error.URLError,TimeoutError,ValueError,UnicodeError) as exc: raise RuntimeError(str(exc)) from exc

class Client:
 def __init__(self,model): self.model=model;self.last_metrics={};self.context={};self.last_schema=None
 def generate(self,messages):
  self.last_metrics={};self.last_schema=schema_for(self.context)
  payload={'model':self.model,'messages':messages,'format':self.last_schema,'think':False,'stream':False,'options':OPTIONS}
  data=request_json('/api/chat',payload)
  msg=data.get('message') if isinstance(data,dict) else None
  if not isinstance(msg,dict) or not isinstance(msg.get('content'),str): raise RuntimeError('Missing message.content')
  self.last_metrics={k:data[k] for k in ('prompt_eval_count','eval_count','total_duration','load_duration','prompt_eval_duration','eval_duration') if type(data.get(k)) is int}
  return msg['content'].strip()

class RecordingModel:
 def __init__(self,client): self.client=client;self.calls=[];self.runtime_context={}
 def generate(self,messages):
  self.client.context=copy.deepcopy(self.runtime_context)
  started=time.perf_counter()
  call={'messages':copy.deepcopy(messages),'raw':None,'action':None,'parse_error':None,'error':None,'metrics':{},'runtime_context':copy.deepcopy(self.runtime_context)}
  try:
   raw=self.client.generate(messages);call['raw']=raw;call['metrics']=dict(self.client.last_metrics)
   try: call['action']=parse_action(raw).name
   except (ActionError,ValueError,TypeError) as exc: call['parse_error']=str(exc)
   return raw
  except RuntimeError as exc: call['error']=str(exc);raise
  finally:
   call['schema']=copy.deepcopy(getattr(self.client,'last_schema',None));call['wall_time_ms']=round((time.perf_counter()-started)*1000,1);self.calls.append(call)

class GuardedSchedules(MemorySchedules):
 def __init__(self,initial,created_id,now): super().__init__(initial,created_id);self.users=[];self.now=now;self.guard_events=[];self.filter_events=[]
 def guard(self,body,operation,current=None):
  error=validate_write(body,self.users,self.now,operation,current)
  if error:
   self.guard_events.append({'operation':operation,'body':copy.deepcopy(body),'reason':error})
   raise ScheduleError('USER_EVIDENCE_MISMATCH')
 def list(self,filters):
  effective=copy.deepcopy(filters);scope=query_scope(self.users)
  if scope['known']:
   if scope['category'] is None: effective.pop('category',None)
   else: effective['category']=scope['category']
  bounds=query_date_range(self.users,self.now)
  if bounds['status']!='known':
   self.filter_events.append({'proposed':copy.deepcopy(filters),'effective':None,'reason':'조회 날짜 범위를 명확히 확인하지 못해 호출 차단'})
   raise ScheduleError('QUERY_DATE_UNVERIFIED')
  effective.update({k:bounds[k] for k in ('from','to')})
  if effective!=filters: self.filter_events.append({'proposed':copy.deepcopy(filters),'effective':copy.deepcopy(effective),'reason':'사용자 발화에서 확인한 카테고리·날짜 범위 적용'})
  return super().list(effective)
 def create(self,body,key): self.guard(body,'create');return super().create(body,key)
 def update(self,schedule_id,changes): self.guard(changes,'update',self.items.get(schedule_id));return super().update(schedule_id,changes)

class Service(ConversationService):
 def handle(self,utterance):
  if self.state.last_result is not None and intent_from_users([utterance])=='create':
   self.schedules.users=[]
   self.reset()
  self.schedules.users.append(utterance)
  return super().handle(utterance)
 def _messages(self):
  now=self.clock(); facts=evidence(self.schedules.users,now)
  history=copy.deepcopy(self.state.history[-20:]); after_lookup=False
  if history and history[-1]['role']=='assistant':
   try: after_lookup=json.loads(history[-1]['content']).get('action')=='LIST'
   except (ValueError,AttributeError): pass
  context={'intent':intent_from_users(self.schedules.users),'reference_datetime':now.isoformat(timespec='seconds'),'lookup_completed_this_turn':after_lookup,'schedule_candidates':list(self.state.candidates.values()),'last_schedule_result':self.state.last_result,'verified_user_datetime':facts['datetime'].isoformat(timespec='seconds') if facts['datetime'] else None,'user_time_in_past':bool(facts['datetime'] and facts['datetime']<now),'requested_reminder_minutes':facts['reminder'],'date_missing':facts['day'] is None,'time_missing':facts['clock'] is None,'ampm_ambiguous':facts['ambiguous']}
  context['task_evidence']=task_evidence(self.schedules.users)
  context['query_scope']=query_scope(self.schedules.users)
  context['query_date_range']=query_date_range(self.schedules.users,now)
  if context['intent'] in {'query','update','delete'}:
   context['date_missing']=context['query_date_range']['status']!='known'
  context['required_question']=required_question(self.schedules.users,now) if context['intent']=='create' else None
  self.model.runtime_context=context
  observation='[프로그램 관측 데이터 — 사용자 발화 아님]\n'+json.dumps(context,ensure_ascii=False)+'\n'
  if after_lookup:
   observation+='조회가 완료됐습니다. 위 실제 결과를 사용해 다음 동작을 출력하세요. LIST를 반복하지 마세요.'
   history.append({'role':'user','content':observation})
  elif history and history[-1]['role']=='user':
   history[-1]['content']=observation+'\n[현재 사용자 발화]\n'+history[-1]['content']
  else: history.append({'role':'user','content':observation+'다음 동작을 출력하세요.'})
  return [{'role':'system','content':PROMPT}]+history

def check(name,expected,actual): return {'name':name,'expected':expected,'actual':actual,'match':expected==actual}
def final_checks(expected,actual):
 checks=[check('final_ids',sorted(x['id'] for x in expected),sorted(x['id'] for x in actual))];byid={x['id']:x for x in actual}
 for e in expected:
  for k,v in e.items():
   if k!='id': checks.append(check('final:'+e['id']+':'+k,v,byid.get(e['id'],{}).get(k)))
 return checks

class ReferenceClient:
 def __init__(self, outputs):
  self.outputs=copy.deepcopy(outputs);self.last_metrics={};self.last_schema=None;self.context={}
 def generate(self,messages):
  if not self.outputs: raise RuntimeError('Unexpected reference model call')
  return json.dumps(self.outputs.pop(0),ensure_ascii=False)

def business_state(items):
 # Server-maintained timestamps are outside the requested business change.
 return {x['id']:{k:v for k,v in x.items() if k not in {'createdAt','updatedAt','reminderAt'}} for x in items}

def pending_value(service):
 p=service.state.pending
 return None if p is None else {'operation':p.operation,'target_id':p.schedule_id,'changes':p.changes}

def reference_trace(case):
 """Independent scorer-only trace. Never provided to the live model/client."""
 client=ReferenceClient([a for t in case['turns'] for a in t['model_outputs']])
 schedules=MemorySchedules(case['initial_schedules'],case.get('created_id'))
 service=ConversationService(client,schedules,lambda:datetime.fromisoformat(case['reference_datetime']))
 result=[]
 for t in case['turns']:
  before=len(schedules.calls);reply=service.handle(t['user'])
  if reply.kind!=t['expect']['reply_kind']: raise ValueError('Reference path mismatch: '+case['id'])
  result.append({'state':business_state(schedules.items.values()),'pending':pending_value(service),
                 'selection':service.state.selection.candidate_ids if service.state.selection else None,
                 'writes':[{k:v for k,v in c.items() if k!='key'} for c in schedules.calls[before:] if c['method'] in {'POST','PATCH','DELETE'}]})
 if client.outputs: raise ValueError('Unused reference actions: '+case['id'])
 return result


def run_case(case,client,rubric):
 now=datetime.fromisoformat(case['reference_datetime']);schedules=GuardedSchedules(case['initial_schedules'],case.get('created_id'),now);model=RecordingModel(client);service=Service(model,schedules,lambda:now);turns=[];stop=None;reference=reference_trace(case)
 for index,turn in enumerate(case['turns'],1):
  a,b,g=len(schedules.calls),len(model.calls),len(schedules.guard_events);f=len(schedules.filter_events);started=time.perf_counter();crash=None
  try: reply=service.handle(turn['user'])
  except Exception as exc: crash=type(exc).__name__+': '+str(exc);reply=None
  calls=schedules.calls[a:];writes=[x for x in calls if x['method'] in {'POST','PATCH','DELETE'}];expected=turn['expect'];kind=reply.kind if reply else None;message=reply.message if reply else None
  checks=[check('reply_kind',expected['reply_kind'],kind),check('write_attempts',expected['writes'],len(writes))]
  for phrase in expected.get('reply_contains',[]): checks.append(check('reply_contains',True,phrase in (message or '')))
  if 'api_methods' in expected: checks.append(check('api_methods',expected['api_methods'],[x['method'] for x in calls]))
  if 'post_body' in expected: checks.append(check('post_body',True,any(x['method']=='POST' and x.get('body')==expected['post_body'] for x in calls)))
  if 'target_id' in expected: checks.append(check('write_target',True,all(x.get('id')==expected['target_id'] for x in writes)))
  qtype=rubric.get(case['id'],{}).get(str(index));qmatch=None
  if expected['reply_kind']=='ask':
   if not qtype: raise ValueError(f'Missing question rubric: {case["id"]}:{index}')
   qmatch=kind=='ask' and check_question(qtype,message,service.state.selection is not None)
   checks.append(check('question_content:'+qtype,True,qmatch))
  # Check the filters actually sent, including completed/category and date bounds.
  expected_lists=[x['request'] for x in turn['model_outputs'] if x['action']=='LIST']
  actual_lists=[x['filters'] for x in calls if 'filters' in x]
  if expected_lists:
   # Query correctness is the observed result set, not an arbitrary optional
   # filter spelling. Modification/deletion correctness uses target + final state.
   if expected['reply_kind']=='answer':
    probe=MemorySchedules(case['initial_schedules'],case.get('created_id'))
    probe.items=copy.deepcopy(schedules.items)
    wanted=[sorted(x['id'] for x in probe.list(f)) for f in expected_lists]
    observed=[sorted(x['id'] for x in probe.list(f)) for f in actual_lists]
    checks.append(check('query_result_ids',wanted,observed))
  gold=reference[index-1]
  checks.append(check('business_state',gold['state'],business_state(schedules.items.values())))
  checks.append(check('write_payloads',gold['writes'],[{k:v for k,v in c.items() if k!='key'} for c in writes]))
  if expected['reply_kind']=='confirm': checks.append(check('confirmation_target_changes',gold['pending'],pending_value(service)))
  if gold['selection'] is not None:
   checks.append(check('selection_candidates_order',gold['selection'],service.state.selection.candidate_ids if service.state.selection else None))
  events=schedules.guard_events[g:]
  if events: checks.append(check('unsupported_write_blocked',False,True))
  turns.append({'index':index,'user':turn['user'],'expected':expected,'expected_model_outputs':turn['model_outputs'],'reply':{'kind':kind,'message':message},'model_calls':model.calls[b:],'api_calls':calls,'guard_events':events,'filter_events':schedules.filter_events[f:],'wall_time_ms':round((time.perf_counter()-started)*1000,1),'checks':checks,'query_filter_diagnostic':{'expected':expected_lists,'actual':actual_lists},'crash':crash})
  if kind!=expected['reply_kind']: stop='reply_kind_mismatch';break
  if qmatch is False: stop='question_content_needs_review';break
  if events: stop='unsupported_write_blocked';break
  if any(not c['match'] and c['name'] in {'confirmation_target_changes','selection_candidates_order','business_state','write_payloads','query_result_ids'} for c in checks): stop='semantic_path_mismatch';break
 end=list(schedules.items.values());completed=len(turns)==len(case['turns']) and stop is None;fc=final_checks(case['expect_final'],end) if completed else []
 matched=completed and all(c['match'] for t in turns for c in t['checks']) and all(c['match'] for c in fc)
 return {'case_id':case['id'],'conversation_group_id':case['conversation_group_id'],'label_status':case['label_status'],'reference_datetime':case['reference_datetime'],'initial_schedules':case['initial_schedules'],'expected_final':case['expect_final'],'actual_final':end,'turns':turns,'final_checks':fc,'completed_turns':len(turns),'total_turns':len(case['turns']),'evaluation_version':'eval-v2.3-proposed','source_kind':'actual_model','stop_reason':stop,'provisional_match':matched,'status':'provisional_match' if matched else 'needs_review'}

def save_json(path,value): path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def save_jsonl(path,rows): path.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--scripted',action='store_true',help='Replay reference actions only; no Ollama call or model score');p.add_argument('--model',default='qwen3:4b');p.add_argument('--scenarios',type=Path,default=ROOT/'validation'/'scenarios_UNREVIEWED.jsonl');p.add_argument('--rubric',type=Path,default=ROOT/'question_rubric.json');p.add_argument('--case',action='append');p.add_argument('--output-dir',type=Path);args=p.parse_args()
 cases=load_cases(args.scenarios);rubric=json.loads(args.rubric.read_text())
 if args.case:
  missing=set(args.case)-{c['id'] for c in cases}
  if missing:p.error('Unknown case ID: '+str(missing))
  cases=[c for c in cases if c['id'] in args.case]
 # Validate rubric coverage before inference so a missing rule cannot silently pass.
 for c in cases:
  for i,t in enumerate(c['turns'],1):
   if t['expect']['reply_kind']=='ask' and str(i) not in rubric.get(c['id'],{}):p.error(f'Missing rubric: {c["id"]}:{i}')
 run_id=str(uuid4());out=args.output_dir or ROOT/'runs'/('eval_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+run_id[:6])
 if out.exists():p.error('Use a new output directory')
 if args.scripted: version=None;tags={}
 else:
  try: version=request_json('/api/version',timeout=10);tags=request_json('/api/tags',timeout=10)
  except RuntimeError as exc:p.error(str(exc))
 out.mkdir(parents=True)
 files=list(ROOT.glob('*.py'))+list((ROOT/'runtime').rglob('*.py'))+[ROOT/'prompt.txt',args.scenarios,args.rubric]
 manifest={'tool_version':'eval-v2.3-proposed','source_kind':'scripted_reference' if args.scripted else 'actual_model','run_id':run_id,'model':args.model,'model_metadata':[m for m in tags.get('models',[]) if m.get('name')==args.model],'ollama_version':version,'options':OPTIONS,'think':False,'scope':'Qwen + changed observation framing + state schema + evidence guard; NOT fine-tuned model','prompt':PROMPT,'case_ids':[c['id'] for c in cases],'file_sha256':{str(f.relative_to(ROOT)) if f.is_relative_to(ROOT) else str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in files},'status':'provisional_unreviewed','created_at':datetime.now(timezone.utc).isoformat()}
 save_json(out/'manifest.json',manifest);results=[];client=Client(args.model)
 for case in cases:
  active=ReferenceClient([a for t in case['turns'] for a in t['model_outputs']]) if args.scripted else client
  result=run_case(case,active,rubric);result['run_id']=run_id;result['source_kind']='scripted_reference' if args.scripted else 'actual_model';results.append(result)
  save_jsonl(out/'review_cases.jsonl',results);save_jsonl(out/'turn_results.jsonl',[{'run_id':run_id,'case_id':r['case_id'],**t} for r in results for t in r['turns']])
  print(case['id']+': '+result['status']+f' ({result["completed_turns"]}/{result["total_turns"]} turns)',flush=True)
 calls=[m for r in results for t in r['turns'] for m in t['model_calls']]
 summary={'run_id':run_id,'source_kind':'scripted_reference' if args.scripted else 'actual_model','cases_run':len(results),'provisional_matches':sum(r['provisional_match'] for r in results),'needs_review':sum(not r['provisional_match'] for r in results),'model_calls':len(calls),'parse_errors':sum(m['parse_error'] is not None for m in calls),'request_errors':sum(m['error'] is not None for m in calls),'query_date_blocks':sum(e.get('effective') is None for r in results for t in r['turns'] for e in t.get('filter_events',[])),'filter_corrections':sum(sum(e.get('effective') is not None for e in t.get('filter_events',[])) for r in results for t in r['turns']),'blocked_writes':sum(len(t['guard_events']) for r in results for t in r['turns']),'question_stops':sum(r['stop_reason']=='question_content_needs_review' for r in results),'controller_crashes':sum(t['crash'] is not None for r in results for t in r['turns']),'note':'Unreviewed draft + conservative lexical checks; not formal performance. Changes include controller/input/guard settings.'}
 save_json(out/'summary.json',summary);print(json.dumps(summary,ensure_ascii=False),flush=True);print('HTML에서 열 파일:',out/'review_cases.jsonl',flush=True)
if __name__=='__main__':main()
