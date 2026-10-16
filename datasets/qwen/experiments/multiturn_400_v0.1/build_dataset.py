"""Deterministic, unreviewed dialogue drafts. Never consumes live model answers."""
import copy, csv, hashlib, json, sys, shutil, re
from pathlib import Path
from datetime import datetime, timedelta
from uuid import uuid5, NAMESPACE_URL
from collections import Counter, defaultdict
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
import run_eval as app

class ReferenceClient(app.ReferenceClient):
 def generate(self,messages):
  self.last_schema=app.schema_for(self.context)
  return super().generate(messages)

from runtime.replay_multiturn_pilot import validate_scenario
CATS=['INBOUND','INVENTORY','OUTBOUND','RETURN','OTHER']
KO=dict(zip(CATS,['입고','재고','출고','반품','기타']))
TITLES={
'INBOUND':['입고 상품 검수','입고 수량 확인','입고 파손 확인','입고 서류 확인','입고 물량 정리','입고 라벨 점검','입고 박스 분류','입고 상품 촬영'],
'INVENTORY':['재고 실사','재고 수량 확인','재고 위치 조사','재고 부족 수량 확인','재고 선반 점검','재고 상품 분류','재고 목록 정리','재고 불량 확인'],
'OUTBOUND':['출고 준비','출고 수량 확인','주문 상품 피킹','출고 포장 점검','출고 상차 준비','출고 상품 검수','출고 서류 확인','출고 박스 정리'],
'RETURN':['반품 검수','반품 수량 확인','반품 사유 확인','반품 상품 촬영','반품 박스 분류','반품 서류 정리','반품 라벨 점검','반품 파손 확인'],
'OTHER':['팀 회의','교대 인수인계','안전 점검','장비 점검','운영 회의','주간 회의','야간 인수인계','현장 안전 점검']}
DATES=['2026-10-10','2026-10-31','2026-12-31','2027-01-30','2028-02-28','2028-02-29','2027-04-30','2027-06-15']
DAYS={0:'오늘',1:'내일',2:'모레'}
Q={'task':'어떤 업무를 등록할까요?','date':'어느 날짜의 일정인가요?','time':'어떤 시각에 등록할까요?','ampm':'오전인가요, 오후인가요?','past_time':'말씀하신 시각은 이미 지났어요. 몇 시로 등록할까요?'}
ALL=[];META=[];RUBRIC={}
def uid(s):return str(uuid5(NAMESPACE_URL,'qwen400:v1:'+s))
def particle_for(text,original):
 """Match a Korean particle to the final syllable after title substitution."""
 code=ord(text[-1]);jong=(code-0xAC00)%28 if 0xAC00<=code<=0xD7A3 else None
 if jong is None:return original
 if original in ('으로','로'):return '로' if jong in (0,8) else '으로'
 choices={'을':('을','를'),'를':('을','를'),'이':('이','가'),'가':('이','가'),
          '은':('은','는'),'는':('은','는'),'과':('과','와'),'와':('과','와'),
          '이야':('이야','야'),'야':('이야','야')}
 return choices[original][0 if jong else 1] if original in choices else original
def act(name,request=None,message=None):return {'action':name,'message':message,'request':request}
def turn(user,outputs,kind,methods=(),writes=0,**extra):return {'user':user,'model_outputs':outputs,'expect':{'reply_kind':kind,'writes':writes,'api_methods':list(methods),**extra}}
def clean(items):return [{k:v for k,v in x.items() if k not in ('createdAt','updatedAt','reminderAt')} for x in items]
def register(c,split,cat,family,origin,source_id=None):
 validate_scenario(c);ALL.append(c)
 META.append({'case_id':c['id'],'split':split,'category':cat,'intent':('query' if source_id.split('-')[1]=='list' else source_id.split('-')[1]) if source_id else family.split(':')[0],'family':family,'conversation_group_id':c['conversation_group_id'],'origin':origin,'source_case_id':source_id,'label_status':'proposed_unreviewed','reviewer':None,'review_note':None})

# All previous, development-exposed cases remain in TRAIN; variants stay together.
original=json.loads('[{"schema_version": "pilot-v0.1", "id": "in-create-past", "conversation_group_id": "in-create-past", "reference_datetime": "2026-10-10T09:00:00+09:00", "initial_schedules": [], "turns": [{"user": "오늘 오전 8시에 입고 물량 확인 등록해줘.", "model_outputs": [{"action": "ASK", "message": "말씀하신 시각이 이미 지났어요. 언제로 등록할까요?", "request": null}], "expect": {"reply_kind": "ask", "writes": 0, "api_methods": []}}, {"user": "그럼 오늘 오전 11시.", "model_outputs": [{"action": "CREATE", "message": null, "request": {"title": "입고 물량 확인", "scheduledAt": "2026-10-10T11:00:00+09:00", "category": "INBOUND", "reminderMinutesBefore": null}}], "expect": {"reply_kind": "created", "writes": 1, "api_methods": ["POST"], "post_body": {"title": "입고 물량 확인", "scheduledAt": "2026-10-10T11:00:00+09:00", "category": "INBOUND", "reminderMinutesBefore": null}}}], "label_status": "proposed_unreviewed", "expect_final": [{"id": "9f98d3e6-aeb3-53b1-bc8b-6f565f820739", "title": "입고 물량 확인", "scheduledAt": "2026-10-10T11:00:00+09:00", "category": "INBOUND", "reminderMinutesBefore": null, "completed": false}], "created_id": "9f98d3e6-aeb3-53b1-bc8b-6f565f820739"}, {"schema_version": "pilot-v0.1", "id": "in-create-time", "conversation_group_id": "in-create-time", "reference_datetime": "2026-10-10T09:00:00+09:00", "initial_schedules": [], "turns": [{"user": "내일 입고 상품 검수 잡아줘.", "model_outputs": [{"action": "ASK", "message": "내일 몇 시에 등록할까요?", "request": null}], "expect": {"reply_kind": "ask", "writes": 0, "api_methods": []}}, {"user": "오전 10시로 해줘.", "model_outputs": [{"action": "CREATE", "message": null, "request": {"title": "입고 상품 검수", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "INBOUND", "reminderMinutesBefore": null}}], "expect": {"reply_kind": "created", "writes": 1, "api_methods": ["POST"], "post_body": {"title": "입고 상품 검수", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "INBOUND", "reminderMinutesBefore": null}}}], "label_status": "proposed_unreviewed", "expect_final": [{"id": "78c2d01d-9f9e-53ce-9cb0-d4ea3ad0d07b", "title": "입고 상품 검수", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "INBOUND", "reminderMinutesBefore": null, "completed": false}], "created_id": "78c2d01d-9f9e-53ce-9cb0-d4ea3ad0d07b"}, {"schema_version": "pilot-v0.1", "id": "in-delete-approve", "conversation_group_id": "in-delete-approve", "reference_datetime": "2026-10-10T09:00:00+09:00", "initial_schedules": [{"id": "c64037e4-d96d-5922-9c45-37963ba9080e", "title": "입고 상품 검수", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "INBOUND", "completed": false, "reminderMinutesBefore": null, "createdAt": "2026-10-09T09:00:00+09:00", "updatedAt": "2026-10-09T09:00:00+09:00"}], "turns": [{"user": "내일 입고 상품 검수 일정 삭제해줘.", "model_outputs": [{"action": "LIST", "message": null, "request": {"from": "2026-10-11T00:00:00+09:00", "to": "2026-10-12T00:00:00+09:00", "category": "INBOUND"}}, {"action": "CONFIRM_DELETE", "message": null, "request": {"targetId": "c64037e4-d96d-5922-9c45-37963ba9080e"}}], "expect": {"reply_kind": "confirm", "writes": 0, "api_methods": ["GET", "GET"]}}, {"user": "응", "model_outputs": [], "expect": {"reply_kind": "deleted", "writes": 1, "api_methods": ["GET", "DELETE"], "target_id": "c64037e4-d96d-5922-9c45-37963ba9080e"}}], "label_status": "proposed_unreviewed", "expect_final": []}, {"schema_version": "pilot-v0.1", "id": "in-list-range", "conversation_group_id": "in-list-range", "reference_datetime": "2026-10-10T09:00:00+09:00", "initial_schedules": [{"id": "c64037e4-d96d-5922-9c45-37963ba9080e", "title": "입고 상품 검수", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "INBOUND", "completed": false, "reminderMinutesBefore": null, "createdAt": "2026-10-09T09:00:00+09:00", "updatedAt": "2026-10-09T09:00:00+09:00"}], "turns": [{"user": "입고 일정 보여줘.", "model_outputs": [{"action": "ASK", "message": "어느 날짜의 입고 일정을 볼까요?", "request": null}], "expect": {"reply_kind": "ask", "writes": 0, "api_methods": []}}, {"user": "내일 일정.", "model_outputs": [{"action": "LIST", "message": null, "request": {"from": "2026-10-11T00:00:00+09:00", "to": "2026-10-12T00:00:00+09:00", "category": "INBOUND"}}, {"action": "ANSWER", "message": null, "request": null}], "expect": {"reply_kind": "answer", "writes": 0, "api_methods": ["GET"]}}], "label_status": "proposed_unreviewed", "expect_final": [{"id": "c64037e4-d96d-5922-9c45-37963ba9080e", "title": "입고 상품 검수", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "INBOUND", "completed": false, "reminderMinutesBefore": null}]}, {"schema_version": "pilot-v0.1", "id": "in-update-approve", "conversation_group_id": "in-update-time-family", "reference_datetime": "2026-10-10T09:00:00+09:00", "initial_schedules": [{"id": "c64037e4-d96d-5922-9c45-37963ba9080e", "title": "입고 상품 검수", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "INBOUND", "completed": false, "reminderMinutesBefore": null, "createdAt": "2026-10-09T09:00:00+09:00", "updatedAt": "2026-10-09T09:00:00+09:00"}], "turns": [{"user": "내일 입고 상품 검수를 오후 2시로 바꿔줘.", "model_outputs": [{"action": "LIST", "message": null, "request": {"from": "2026-10-11T00:00:00+09:00", "to": "2026-10-12T00:00:00+09:00", "category": "INBOUND"}}, {"action": "CONFIRM_UPDATE", "message": null, "request": {"targetId": "c64037e4-d96d-5922-9c45-37963ba9080e", "changes": {"scheduledAt": "2026-10-11T14:00:00+09:00"}}}], "expect": {"reply_kind": "confirm", "writes": 0, "api_methods": ["GET", "GET"]}}, {"user": "네", "model_outputs": [], "expect": {"reply_kind": "updated", "writes": 1, "api_methods": ["GET", "PATCH"], "target_id": "c64037e4-d96d-5922-9c45-37963ba9080e"}}], "label_status": "proposed_unreviewed", "expect_final": [{"id": "c64037e4-d96d-5922-9c45-37963ba9080e", "title": "입고 상품 검수", "scheduledAt": "2026-10-11T14:00:00+09:00", "category": "INBOUND", "completed": false, "reminderMinutesBefore": null}]}, {"schema_version": "pilot-v0.1", "id": "in-update-reject", "conversation_group_id": "in-update-time-family", "reference_datetime": "2026-10-10T09:00:00+09:00", "initial_schedules": [{"id": "c64037e4-d96d-5922-9c45-37963ba9080e", "title": "입고 상품 검수", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "INBOUND", "completed": false, "reminderMinutesBefore": null, "createdAt": "2026-10-09T09:00:00+09:00", "updatedAt": "2026-10-09T09:00:00+09:00"}], "turns": [{"user": "내일 입고 상품 검수를 오후 2시로 바꿔줘.", "model_outputs": [{"action": "LIST", "message": null, "request": {"from": "2026-10-11T00:00:00+09:00", "to": "2026-10-12T00:00:00+09:00", "category": "INBOUND"}}, {"action": "CONFIRM_UPDATE", "message": null, "request": {"targetId": "c64037e4-d96d-5922-9c45-37963ba9080e", "changes": {"scheduledAt": "2026-10-11T14:00:00+09:00"}}}], "expect": {"reply_kind": "confirm", "writes": 0, "api_methods": ["GET", "GET"]}}, {"user": "아니요", "model_outputs": [], "expect": {"reply_kind": "cancelled", "writes": 0, "api_methods": []}}], "label_status": "proposed_unreviewed", "expect_final": [{"id": "c64037e4-d96d-5922-9c45-37963ba9080e", "title": "입고 상품 검수", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "INBOUND", "completed": false, "reminderMinutesBefore": null}]}, {"schema_version": "pilot-v0.1", "id": "inv-create-date", "conversation_group_id": "inv-create-date", "reference_datetime": "2026-10-10T09:00:00+09:00", "initial_schedules": [], "turns": [{"user": "오전 10시에 재고 실사 등록해줘.", "model_outputs": [{"action": "ASK", "message": "재고 실사를 어느 날짜에 등록할까요?", "request": null}], "expect": {"reply_kind": "ask", "writes": 0, "api_methods": []}}, {"user": "내일이야.", "model_outputs": [{"action": "CREATE", "message": null, "request": {"title": "재고 실사", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "INVENTORY", "reminderMinutesBefore": null}}], "expect": {"reply_kind": "created", "writes": 1, "api_methods": ["POST"], "post_body": {"title": "재고 실사", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "INVENTORY", "reminderMinutesBefore": null}}}], "label_status": "proposed_unreviewed", "expect_final": [{"id": "c5d941af-27eb-583c-baa7-a6ab54fc9273", "title": "재고 실사", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "INVENTORY", "reminderMinutesBefore": null, "completed": false}], "created_id": "c5d941af-27eb-583c-baa7-a6ab54fc9273"}, {"schema_version": "pilot-v0.1", "id": "inv-create-reminder", "conversation_group_id": "inv-create-reminder", "reference_datetime": "2026-10-10T09:00:00+09:00", "initial_schedules": [], "turns": [{"user": "내일 재고 부족 수량 확인 넣고 10분 전에 알려줘.", "model_outputs": [{"action": "ASK", "message": "내일 몇 시에 등록할까요?", "request": null}], "expect": {"reply_kind": "ask", "writes": 0, "api_methods": []}}, {"user": "오후 2시.", "model_outputs": [{"action": "CREATE", "message": null, "request": {"title": "재고 부족 수량 확인", "scheduledAt": "2026-10-11T14:00:00+09:00", "category": "INVENTORY", "reminderMinutesBefore": 10}}], "expect": {"reply_kind": "created", "writes": 1, "api_methods": ["POST"], "post_body": {"title": "재고 부족 수량 확인", "scheduledAt": "2026-10-11T14:00:00+09:00", "category": "INVENTORY", "reminderMinutesBefore": 10}}}], "label_status": "proposed_unreviewed", "expect_final": [{"id": "7904244f-262b-5844-b9c9-ab1423da7599", "title": "재고 부족 수량 확인", "scheduledAt": "2026-10-11T14:00:00+09:00", "category": "INVENTORY", "reminderMinutesBefore": 10, "completed": false}], "created_id": "7904244f-262b-5844-b9c9-ab1423da7599"}, {"schema_version": "pilot-v0.1", "id": "inv-delete-approve", "conversation_group_id": "inv-delete-family", "reference_datetime": "2026-10-10T09:00:00+09:00", "initial_schedules": [{"id": "01a070f2-1f10-5c57-b670-1ac0e8e64339", "title": "재고 실사", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "INVENTORY", "completed": false, "reminderMinutesBefore": null, "createdAt": "2026-10-09T09:00:00+09:00", "updatedAt": "2026-10-09T09:00:00+09:00"}], "turns": [{"user": "내일 재고 실사 일정 삭제해줘.", "model_outputs": [{"action": "LIST", "message": null, "request": {"from": "2026-10-11T00:00:00+09:00", "to": "2026-10-12T00:00:00+09:00", "category": "INVENTORY"}}, {"action": "CONFIRM_DELETE", "message": null, "request": {"targetId": "01a070f2-1f10-5c57-b670-1ac0e8e64339"}}], "expect": {"reply_kind": "confirm", "writes": 0, "api_methods": ["GET", "GET"]}}, {"user": "네", "model_outputs": [], "expect": {"reply_kind": "deleted", "writes": 1, "api_methods": ["GET", "DELETE"], "target_id": "01a070f2-1f10-5c57-b670-1ac0e8e64339"}}], "label_status": "proposed_unreviewed", "expect_final": []}, {"schema_version": "pilot-v0.1", "id": "inv-delete-reject", "conversation_group_id": "inv-delete-family", "reference_datetime": "2026-10-10T09:00:00+09:00", "initial_schedules": [{"id": "01a070f2-1f10-5c57-b670-1ac0e8e64339", "title": "재고 실사", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "INVENTORY", "completed": false, "reminderMinutesBefore": null, "createdAt": "2026-10-09T09:00:00+09:00", "updatedAt": "2026-10-09T09:00:00+09:00"}], "turns": [{"user": "내일 재고 실사 일정 삭제해줘.", "model_outputs": [{"action": "LIST", "message": null, "request": {"from": "2026-10-11T00:00:00+09:00", "to": "2026-10-12T00:00:00+09:00", "category": "INVENTORY"}}, {"action": "CONFIRM_DELETE", "message": null, "request": {"targetId": "01a070f2-1f10-5c57-b670-1ac0e8e64339"}}], "expect": {"reply_kind": "confirm", "writes": 0, "api_methods": ["GET", "GET"]}}, {"user": "취소", "model_outputs": [], "expect": {"reply_kind": "cancelled", "writes": 0, "api_methods": []}}], "label_status": "proposed_unreviewed", "expect_final": [{"id": "01a070f2-1f10-5c57-b670-1ac0e8e64339", "title": "재고 실사", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "INVENTORY", "completed": false, "reminderMinutesBefore": null}]}, {"schema_version": "pilot-v0.1", "id": "inv-list-completed", "conversation_group_id": "inv-list-completed", "reference_datetime": "2026-10-10T09:00:00+09:00", "initial_schedules": [{"id": "01a070f2-1f10-5c57-b670-1ac0e8e64339", "title": "재고 실사", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "INVENTORY", "completed": false, "reminderMinutesBefore": null, "createdAt": "2026-10-09T09:00:00+09:00", "updatedAt": "2026-10-09T09:00:00+09:00"}, {"id": "62e5c382-79e4-5aa7-b5ba-cecae621a6f8", "title": "재고 부족 수량 확인", "scheduledAt": "2026-10-11T14:00:00+09:00", "category": "INVENTORY", "completed": true, "reminderMinutesBefore": null, "createdAt": "2026-10-09T09:00:00+09:00", "updatedAt": "2026-10-09T09:00:00+09:00"}], "turns": [{"user": "내일 끝내지 않은 재고 일정 보여줘.", "model_outputs": [{"action": "LIST", "message": null, "request": {"from": "2026-10-11T00:00:00+09:00", "to": "2026-10-12T00:00:00+09:00", "category": "INVENTORY", "completed": false}}, {"action": "ANSWER", "message": null, "request": null}], "expect": {"reply_kind": "answer", "writes": 0, "api_methods": ["GET"]}}, {"user": "이번에는 내일 완료한 재고 일정만 보여줘.", "model_outputs": [{"action": "LIST", "message": null, "request": {"from": "2026-10-11T00:00:00+09:00", "to": "2026-10-12T00:00:00+09:00", "category": "INVENTORY", "completed": true}}, {"action": "ANSWER", "message": null, "request": null}], "expect": {"reply_kind": "answer", "writes": 0, "api_methods": ["GET"]}}], "label_status": "proposed_unreviewed", "expect_final": [{"id": "01a070f2-1f10-5c57-b670-1ac0e8e64339", "title": "재고 실사", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "INVENTORY", "completed": false, "reminderMinutesBefore": null}, {"id": "62e5c382-79e4-5aa7-b5ba-cecae621a6f8", "title": "재고 부족 수량 확인", "scheduledAt": "2026-10-11T14:00:00+09:00", "category": "INVENTORY", "completed": true, "reminderMinutesBefore": null}]}, {"schema_version": "pilot-v0.1", "id": "inv-update-alarm-off", "conversation_group_id": "inv-update-alarm-off", "reference_datetime": "2026-10-10T09:00:00+09:00", "initial_schedules": [{"id": "01a070f2-1f10-5c57-b670-1ac0e8e64339", "title": "재고 실사", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "INVENTORY", "completed": false, "reminderMinutesBefore": 10, "createdAt": "2026-10-09T09:00:00+09:00", "updatedAt": "2026-10-09T09:00:00+09:00"}], "turns": [{"user": "내일 재고 실사 일정의 알림만 꺼줘.", "model_outputs": [{"action": "LIST", "message": null, "request": {"from": "2026-10-11T00:00:00+09:00", "to": "2026-10-12T00:00:00+09:00", "category": "INVENTORY"}}, {"action": "CONFIRM_UPDATE", "message": null, "request": {"targetId": "01a070f2-1f10-5c57-b670-1ac0e8e64339", "changes": {"reminderMinutesBefore": null}}}], "expect": {"reply_kind": "confirm", "writes": 0, "api_methods": ["GET", "GET"]}}, {"user": "응", "model_outputs": [], "expect": {"reply_kind": "updated", "writes": 1, "api_methods": ["GET", "PATCH"], "target_id": "01a070f2-1f10-5c57-b670-1ac0e8e64339"}}], "label_status": "proposed_unreviewed", "expect_final": [{"id": "01a070f2-1f10-5c57-b670-1ac0e8e64339", "title": "재고 실사", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "INVENTORY", "completed": false, "reminderMinutesBefore": null}]}, {"schema_version": "pilot-v0.1", "id": "out-create-ampm", "conversation_group_id": "out-create-ampm", "reference_datetime": "2026-10-10T09:00:00+09:00", "initial_schedules": [], "turns": [{"user": "내일 3시에 출고 준비 등록해줘.", "model_outputs": [{"action": "ASK", "message": "오전 3시인가요, 오후 3시인가요?", "request": null}], "expect": {"reply_kind": "ask", "writes": 0, "api_methods": []}}, {"user": "오후 3시야.", "model_outputs": [{"action": "CREATE", "message": null, "request": {"title": "출고 준비", "scheduledAt": "2026-10-11T15:00:00+09:00", "category": "OUTBOUND", "reminderMinutesBefore": null}}], "expect": {"reply_kind": "created", "writes": 1, "api_methods": ["POST"], "post_body": {"title": "출고 준비", "scheduledAt": "2026-10-11T15:00:00+09:00", "category": "OUTBOUND", "reminderMinutesBefore": null}}}], "label_status": "proposed_unreviewed", "expect_final": [{"id": "67b88330-0181-528d-bd4f-005b762b3f10", "title": "출고 준비", "scheduledAt": "2026-10-11T15:00:00+09:00", "category": "OUTBOUND", "reminderMinutesBefore": null, "completed": false}], "created_id": "67b88330-0181-528d-bd4f-005b762b3f10"}, {"schema_version": "pilot-v0.1", "id": "out-create-task", "conversation_group_id": "out-create-task", "reference_datetime": "2026-10-10T09:00:00+09:00", "initial_schedules": [], "turns": [{"user": "내일 오전 10시에 일정 하나 등록해줘.", "model_outputs": [{"action": "ASK", "message": "어떤 업무를 등록할까요?", "request": null}], "expect": {"reply_kind": "ask", "writes": 0, "api_methods": []}}, {"user": "주문 상품 피킹.", "model_outputs": [{"action": "CREATE", "message": null, "request": {"title": "주문 상품 피킹", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "OUTBOUND", "reminderMinutesBefore": null}}], "expect": {"reply_kind": "created", "writes": 1, "api_methods": ["POST"], "post_body": {"title": "주문 상품 피킹", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "OUTBOUND", "reminderMinutesBefore": null}}}], "label_status": "proposed_unreviewed", "expect_final": [{"id": "56464c04-9e2b-593b-a0ae-ee713c186dad", "title": "주문 상품 피킹", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "OUTBOUND", "reminderMinutesBefore": null, "completed": false}], "created_id": "56464c04-9e2b-593b-a0ae-ee713c186dad"}, {"schema_version": "pilot-v0.1", "id": "out-delete-invalid-number", "conversation_group_id": "out-delete-invalid-number", "reference_datetime": "2026-10-10T09:00:00+09:00", "initial_schedules": [{"id": "fd4e07d6-fba0-583d-8d95-abc01844b634", "title": "주문 상품 피킹", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "OUTBOUND", "completed": false, "reminderMinutesBefore": null, "createdAt": "2026-10-09T09:00:00+09:00", "updatedAt": "2026-10-09T09:00:00+09:00"}, {"id": "7846db16-57bd-54d6-a09b-e5b0bfaf48e3", "title": "출고 준비", "scheduledAt": "2026-10-11T14:00:00+09:00", "category": "OUTBOUND", "completed": false, "reminderMinutesBefore": null, "createdAt": "2026-10-09T09:00:00+09:00", "updatedAt": "2026-10-09T09:00:00+09:00"}], "turns": [{"user": "내일 출고 일정 하나 지울게.", "model_outputs": [{"action": "LIST", "message": null, "request": {"from": "2026-10-11T00:00:00+09:00", "to": "2026-10-12T00:00:00+09:00", "category": "OUTBOUND"}}, {"action": "CONFIRM_DELETE", "message": null, "request": {"targetId": "7846db16-57bd-54d6-a09b-e5b0bfaf48e3"}}], "expect": {"reply_kind": "ask", "writes": 0, "api_methods": ["GET"]}}, {"user": "3번", "model_outputs": [], "expect": {"reply_kind": "ask", "writes": 0, "api_methods": []}}, {"user": "2번", "model_outputs": [], "expect": {"reply_kind": "confirm", "writes": 0, "api_methods": ["GET"]}}, {"user": "응", "model_outputs": [], "expect": {"reply_kind": "deleted", "writes": 1, "api_methods": ["GET", "DELETE"], "target_id": "7846db16-57bd-54d6-a09b-e5b0bfaf48e3"}}], "label_status": "proposed_unreviewed", "expect_final": [{"id": "fd4e07d6-fba0-583d-8d95-abc01844b634", "title": "주문 상품 피킹", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "OUTBOUND", "completed": false, "reminderMinutesBefore": null}]}, {"schema_version": "pilot-v0.1", "id": "out-list-date-change", "conversation_group_id": "out-list-date-change", "reference_datetime": "2026-10-10T09:00:00+09:00", "initial_schedules": [{"id": "fd4e07d6-fba0-583d-8d95-abc01844b634", "title": "주문 상품 피킹", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "OUTBOUND", "completed": false, "reminderMinutesBefore": null, "createdAt": "2026-10-09T09:00:00+09:00", "updatedAt": "2026-10-09T09:00:00+09:00"}, {"id": "9cf22c7c-e662-5420-80cd-39ed4a4d373d", "title": "출고 준비", "scheduledAt": "2026-10-12T14:00:00+09:00", "category": "OUTBOUND", "completed": false, "reminderMinutesBefore": null, "createdAt": "2026-10-09T09:00:00+09:00", "updatedAt": "2026-10-09T09:00:00+09:00"}], "turns": [{"user": "내일 출고 일정 알려줘.", "model_outputs": [{"action": "LIST", "message": null, "request": {"from": "2026-10-11T00:00:00+09:00", "to": "2026-10-12T00:00:00+09:00", "category": "OUTBOUND"}}, {"action": "ANSWER", "message": null, "request": null}], "expect": {"reply_kind": "answer", "writes": 0, "api_methods": ["GET"]}}, {"user": "모레 출고 일정도 알려줘.", "model_outputs": [{"action": "LIST", "message": null, "request": {"from": "2026-10-12T00:00:00+09:00", "to": "2026-10-13T00:00:00+09:00", "category": "OUTBOUND"}}, {"action": "ANSWER", "message": null, "request": null}], "expect": {"reply_kind": "answer", "writes": 0, "api_methods": ["GET"]}}], "label_status": "proposed_unreviewed", "expect_final": [{"id": "fd4e07d6-fba0-583d-8d95-abc01844b634", "title": "주문 상품 피킹", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "OUTBOUND", "completed": false, "reminderMinutesBefore": null}, {"id": "9cf22c7c-e662-5420-80cd-39ed4a4d373d", "title": "출고 준비", "scheduledAt": "2026-10-12T14:00:00+09:00", "category": "OUTBOUND", "completed": false, "reminderMinutesBefore": null}]}, {"schema_version": "pilot-v0.1", "id": "out-update-complete", "conversation_group_id": "out-update-complete", "reference_datetime": "2026-10-10T09:00:00+09:00", "initial_schedules": [{"id": "fd4e07d6-fba0-583d-8d95-abc01844b634", "title": "주문 상품 피킹", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "OUTBOUND", "completed": false, "reminderMinutesBefore": null, "createdAt": "2026-10-09T09:00:00+09:00", "updatedAt": "2026-10-09T09:00:00+09:00"}], "turns": [{"user": "내일 주문 상품 피킹 일정을 완료로 표시해줘.", "model_outputs": [{"action": "LIST", "message": null, "request": {"from": "2026-10-11T00:00:00+09:00", "to": "2026-10-12T00:00:00+09:00", "category": "OUTBOUND"}}, {"action": "CONFIRM_UPDATE", "message": null, "request": {"targetId": "fd4e07d6-fba0-583d-8d95-abc01844b634", "changes": {"completed": true}}}], "expect": {"reply_kind": "confirm", "writes": 0, "api_methods": ["GET", "GET"]}}, {"user": "응", "model_outputs": [], "expect": {"reply_kind": "updated", "writes": 1, "api_methods": ["GET", "PATCH"], "target_id": "fd4e07d6-fba0-583d-8d95-abc01844b634"}}], "label_status": "proposed_unreviewed", "expect_final": [{"id": "fd4e07d6-fba0-583d-8d95-abc01844b634", "title": "주문 상품 피킹", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "OUTBOUND", "completed": true, "reminderMinutesBefore": null}]}, {"schema_version": "pilot-v0.1", "id": "out-update-title", "conversation_group_id": "out-update-title", "reference_datetime": "2026-10-10T09:00:00+09:00", "initial_schedules": [{"id": "fd4e07d6-fba0-583d-8d95-abc01844b634", "title": "주문 상품 피킹", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "OUTBOUND", "completed": false, "reminderMinutesBefore": null, "createdAt": "2026-10-09T09:00:00+09:00", "updatedAt": "2026-10-09T09:00:00+09:00"}], "turns": [{"user": "내일 주문 상품 피킹 일정 제목을 주문 상품 피킹 점검으로 바꿔줘.", "model_outputs": [{"action": "LIST", "message": null, "request": {"from": "2026-10-11T00:00:00+09:00", "to": "2026-10-12T00:00:00+09:00", "category": "OUTBOUND"}}, {"action": "CONFIRM_UPDATE", "message": null, "request": {"targetId": "fd4e07d6-fba0-583d-8d95-abc01844b634", "changes": {"title": "주문 상품 피킹 점검"}}}], "expect": {"reply_kind": "confirm", "writes": 0, "api_methods": ["GET", "GET"]}}, {"user": "응", "model_outputs": [], "expect": {"reply_kind": "updated", "writes": 1, "api_methods": ["GET", "PATCH"], "target_id": "fd4e07d6-fba0-583d-8d95-abc01844b634"}}], "label_status": "proposed_unreviewed", "expect_final": [{"id": "fd4e07d6-fba0-583d-8d95-abc01844b634", "title": "주문 상품 피킹 점검", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "OUTBOUND", "completed": false, "reminderMinutesBefore": null}]}, {"schema_version": "pilot-v0.1", "id": "ret-create-correct", "conversation_group_id": "ret-create-correct", "reference_datetime": "2026-10-10T09:00:00+09:00", "initial_schedules": [], "turns": [{"user": "내일 반품 검수 등록해줘.", "model_outputs": [{"action": "ASK", "message": "내일 몇 시에 등록할까요?", "request": null}], "expect": {"reply_kind": "ask", "writes": 0, "api_methods": []}}, {"user": "아니, 모레 오전 11시로 해줘.", "model_outputs": [{"action": "CREATE", "message": null, "request": {"title": "반품 검수", "scheduledAt": "2026-10-12T11:00:00+09:00", "category": "RETURN", "reminderMinutesBefore": null}}], "expect": {"reply_kind": "created", "writes": 1, "api_methods": ["POST"], "post_body": {"title": "반품 검수", "scheduledAt": "2026-10-12T11:00:00+09:00", "category": "RETURN", "reminderMinutesBefore": null}}}], "label_status": "proposed_unreviewed", "expect_final": [{"id": "633bb167-4e00-54d6-a8d4-0a9d454b1ed8", "title": "반품 검수", "scheduledAt": "2026-10-12T11:00:00+09:00", "category": "RETURN", "reminderMinutesBefore": null, "completed": false}], "created_id": "633bb167-4e00-54d6-a8d4-0a9d454b1ed8"}, {"schema_version": "pilot-v0.1", "id": "ret-delete-select-approve", "conversation_group_id": "ret-delete-select-family", "reference_datetime": "2026-10-10T09:00:00+09:00", "initial_schedules": [{"id": "69294e8f-f5fc-5f15-83e9-3394754b4b2a", "title": "반품 검수", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "RETURN", "completed": false, "reminderMinutesBefore": null, "createdAt": "2026-10-09T09:00:00+09:00", "updatedAt": "2026-10-09T09:00:00+09:00"}, {"id": "8f65bb1c-cea4-59a7-8082-f848a3079d4a", "title": "반품 수량 확인", "scheduledAt": "2026-10-11T14:00:00+09:00", "category": "RETURN", "completed": false, "reminderMinutesBefore": null, "createdAt": "2026-10-09T09:00:00+09:00", "updatedAt": "2026-10-09T09:00:00+09:00"}], "turns": [{"user": "내일 반품 일정 중 하나를 삭제하고 싶어.", "model_outputs": [{"action": "LIST", "message": null, "request": {"from": "2026-10-11T00:00:00+09:00", "to": "2026-10-12T00:00:00+09:00", "category": "RETURN"}}, {"action": "CONFIRM_DELETE", "message": null, "request": {"targetId": "8f65bb1c-cea4-59a7-8082-f848a3079d4a"}}], "expect": {"reply_kind": "ask", "writes": 0, "api_methods": ["GET"]}}, {"user": "두 번째", "model_outputs": [], "expect": {"reply_kind": "confirm", "writes": 0, "api_methods": ["GET"]}}, {"user": "응", "model_outputs": [], "expect": {"reply_kind": "deleted", "writes": 1, "api_methods": ["GET", "DELETE"], "target_id": "8f65bb1c-cea4-59a7-8082-f848a3079d4a"}}], "label_status": "proposed_unreviewed", "expect_final": [{"id": "69294e8f-f5fc-5f15-83e9-3394754b4b2a", "title": "반품 검수", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "RETURN", "completed": false, "reminderMinutesBefore": null}]}, {"schema_version": "pilot-v0.1", "id": "ret-delete-select-reject", "conversation_group_id": "ret-delete-select-family", "reference_datetime": "2026-10-10T09:00:00+09:00", "initial_schedules": [{"id": "69294e8f-f5fc-5f15-83e9-3394754b4b2a", "title": "반품 검수", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "RETURN", "completed": false, "reminderMinutesBefore": null, "createdAt": "2026-10-09T09:00:00+09:00", "updatedAt": "2026-10-09T09:00:00+09:00"}, {"id": "8f65bb1c-cea4-59a7-8082-f848a3079d4a", "title": "반품 수량 확인", "scheduledAt": "2026-10-11T14:00:00+09:00", "category": "RETURN", "completed": false, "reminderMinutesBefore": null, "createdAt": "2026-10-09T09:00:00+09:00", "updatedAt": "2026-10-09T09:00:00+09:00"}], "turns": [{"user": "내일 반품 일정 중 하나를 삭제하고 싶어.", "model_outputs": [{"action": "LIST", "message": null, "request": {"from": "2026-10-11T00:00:00+09:00", "to": "2026-10-12T00:00:00+09:00", "category": "RETURN"}}, {"action": "CONFIRM_DELETE", "message": null, "request": {"targetId": "8f65bb1c-cea4-59a7-8082-f848a3079d4a"}}], "expect": {"reply_kind": "ask", "writes": 0, "api_methods": ["GET"]}}, {"user": "두 번째", "model_outputs": [], "expect": {"reply_kind": "confirm", "writes": 0, "api_methods": ["GET"]}}, {"user": "아니", "model_outputs": [], "expect": {"reply_kind": "cancelled", "writes": 0, "api_methods": []}}], "label_status": "proposed_unreviewed", "expect_final": [{"id": "69294e8f-f5fc-5f15-83e9-3394754b4b2a", "title": "반품 검수", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "RETURN", "completed": false, "reminderMinutesBefore": null}, {"id": "8f65bb1c-cea4-59a7-8082-f848a3079d4a", "title": "반품 수량 확인", "scheduledAt": "2026-10-11T14:00:00+09:00", "category": "RETURN", "completed": false, "reminderMinutesBefore": null}]}, {"schema_version": "pilot-v0.1", "id": "ret-list-empty", "conversation_group_id": "ret-list-empty", "reference_datetime": "2026-10-10T09:00:00+09:00", "initial_schedules": [{"id": "69294e8f-f5fc-5f15-83e9-3394754b4b2a", "title": "반품 검수", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "RETURN", "completed": false, "reminderMinutesBefore": null, "createdAt": "2026-10-09T09:00:00+09:00", "updatedAt": "2026-10-09T09:00:00+09:00"}], "turns": [{"user": "오늘 반품 일정 있어?", "model_outputs": [{"action": "LIST", "message": null, "request": {"from": "2026-10-10T00:00:00+09:00", "to": "2026-10-11T00:00:00+09:00", "category": "RETURN"}}, {"action": "ANSWER", "message": null, "request": null}], "expect": {"reply_kind": "answer", "writes": 0, "api_methods": ["GET"]}}, {"user": "그럼 내일 반품 일정 알려줘.", "model_outputs": [{"action": "LIST", "message": null, "request": {"from": "2026-10-11T00:00:00+09:00", "to": "2026-10-12T00:00:00+09:00", "category": "RETURN"}}, {"action": "ANSWER", "message": null, "request": null}], "expect": {"reply_kind": "answer", "writes": 0, "api_methods": ["GET"]}}], "label_status": "proposed_unreviewed", "expect_final": [{"id": "69294e8f-f5fc-5f15-83e9-3394754b4b2a", "title": "반품 검수", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "RETURN", "completed": false, "reminderMinutesBefore": null}]}, {"schema_version": "pilot-v0.1", "id": "ret-list-scope", "conversation_group_id": "ret-list-scope", "reference_datetime": "2026-10-10T09:00:00+09:00", "initial_schedules": [{"id": "69294e8f-f5fc-5f15-83e9-3394754b4b2a", "title": "반품 검수", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "RETURN", "completed": false, "reminderMinutesBefore": null, "createdAt": "2026-10-09T09:00:00+09:00", "updatedAt": "2026-10-09T09:00:00+09:00"}, {"id": "c64037e4-d96d-5922-9c45-37963ba9080e", "title": "입고 상품 검수", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "INBOUND", "completed": false, "reminderMinutesBefore": null, "createdAt": "2026-10-09T09:00:00+09:00", "updatedAt": "2026-10-09T09:00:00+09:00"}], "turns": [{"user": "내일 일정 전부 알려줘.", "model_outputs": [{"action": "LIST", "message": null, "request": {"from": "2026-10-11T00:00:00+09:00", "to": "2026-10-12T00:00:00+09:00"}}, {"action": "ANSWER", "message": null, "request": null}], "expect": {"reply_kind": "answer", "writes": 0, "api_methods": ["GET"]}}, {"user": "그중 내일 반품 일정만 다시 보여줘.", "model_outputs": [{"action": "LIST", "message": null, "request": {"from": "2026-10-11T00:00:00+09:00", "to": "2026-10-12T00:00:00+09:00", "category": "RETURN"}}, {"action": "ANSWER", "message": null, "request": null}], "expect": {"reply_kind": "answer", "writes": 0, "api_methods": ["GET"]}}], "label_status": "proposed_unreviewed", "expect_final": [{"id": "69294e8f-f5fc-5f15-83e9-3394754b4b2a", "title": "반품 검수", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "RETURN", "completed": false, "reminderMinutesBefore": null}, {"id": "c64037e4-d96d-5922-9c45-37963ba9080e", "title": "입고 상품 검수", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "INBOUND", "completed": false, "reminderMinutesBefore": null}]}, {"schema_version": "pilot-v0.1", "id": "ret-update-correction", "conversation_group_id": "ret-update-correction", "reference_datetime": "2026-10-10T09:00:00+09:00", "initial_schedules": [{"id": "69294e8f-f5fc-5f15-83e9-3394754b4b2a", "title": "반품 검수", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "RETURN", "completed": false, "reminderMinutesBefore": null, "createdAt": "2026-10-09T09:00:00+09:00", "updatedAt": "2026-10-09T09:00:00+09:00"}], "turns": [{"user": "내일 반품 검수를 오후 2시로 변경해줘.", "model_outputs": [{"action": "LIST", "message": null, "request": {"from": "2026-10-11T00:00:00+09:00", "to": "2026-10-12T00:00:00+09:00", "category": "RETURN"}}, {"action": "CONFIRM_UPDATE", "message": null, "request": {"targetId": "69294e8f-f5fc-5f15-83e9-3394754b4b2a", "changes": {"scheduledAt": "2026-10-11T14:00:00+09:00"}}}], "expect": {"reply_kind": "confirm", "writes": 0, "api_methods": ["GET", "GET"]}}, {"user": "아니, 오후 4시로 바꿔줘.", "model_outputs": [{"action": "LIST", "message": null, "request": {"from": "2026-10-11T00:00:00+09:00", "to": "2026-10-12T00:00:00+09:00", "category": "RETURN"}}, {"action": "CONFIRM_UPDATE", "message": null, "request": {"targetId": "69294e8f-f5fc-5f15-83e9-3394754b4b2a", "changes": {"scheduledAt": "2026-10-11T16:00:00+09:00"}}}], "expect": {"reply_kind": "confirm", "writes": 0, "api_methods": ["GET", "GET"]}}, {"user": "응", "model_outputs": [], "expect": {"reply_kind": "updated", "writes": 1, "api_methods": ["GET", "PATCH"], "target_id": "69294e8f-f5fc-5f15-83e9-3394754b4b2a"}}], "label_status": "proposed_unreviewed", "expect_final": [{"id": "69294e8f-f5fc-5f15-83e9-3394754b4b2a", "title": "반품 검수", "scheduledAt": "2026-10-11T16:00:00+09:00", "category": "RETURN", "completed": false, "reminderMinutesBefore": null}]}, {"schema_version": "pilot-v0.1", "id": "other-create-clarify", "conversation_group_id": "other-create-clarify", "reference_datetime": "2026-10-10T09:00:00+09:00", "initial_schedules": [], "turns": [{"user": "내일 상품 확인 일정 잡아줘.", "model_outputs": [{"action": "ASK", "message": "어떤 업무의 상품 확인인가요?", "request": null}], "expect": {"reply_kind": "ask", "writes": 0, "api_methods": []}}, {"user": "상품 확인 말고 팀 회의로 할게.", "model_outputs": [{"action": "ASK", "message": "내일 몇 시에 팀 회의를 등록할까요?", "request": null}], "expect": {"reply_kind": "ask", "writes": 0, "api_methods": []}}, {"user": "오전 11시.", "model_outputs": [{"action": "CREATE", "message": null, "request": {"title": "팀 회의", "scheduledAt": "2026-10-11T11:00:00+09:00", "category": "OTHER", "reminderMinutesBefore": null}}], "expect": {"reply_kind": "created", "writes": 1, "api_methods": ["POST"], "post_body": {"title": "팀 회의", "scheduledAt": "2026-10-11T11:00:00+09:00", "category": "OTHER", "reminderMinutesBefore": null}}}], "label_status": "proposed_unreviewed", "expect_final": [{"id": "f071dc58-2e69-53b1-b915-f2b531ff62dd", "title": "팀 회의", "scheduledAt": "2026-10-11T11:00:00+09:00", "category": "OTHER", "reminderMinutesBefore": null, "completed": false}], "created_id": "f071dc58-2e69-53b1-b915-f2b531ff62dd"}, {"schema_version": "pilot-v0.1", "id": "other-delete-reject", "conversation_group_id": "other-delete-reject", "reference_datetime": "2026-10-10T09:00:00+09:00", "initial_schedules": [{"id": "6c99a773-24d3-523e-8149-bd2b6b79d16b", "title": "팀 회의", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "OTHER", "completed": false, "reminderMinutesBefore": null, "createdAt": "2026-10-09T09:00:00+09:00", "updatedAt": "2026-10-09T09:00:00+09:00"}], "turns": [{"user": "내일 팀 회의 일정 삭제해줘.", "model_outputs": [{"action": "LIST", "message": null, "request": {"from": "2026-10-11T00:00:00+09:00", "to": "2026-10-12T00:00:00+09:00", "category": "OTHER"}}, {"action": "CONFIRM_DELETE", "message": null, "request": {"targetId": "6c99a773-24d3-523e-8149-bd2b6b79d16b"}}], "expect": {"reply_kind": "confirm", "writes": 0, "api_methods": ["GET", "GET"]}}, {"user": "그만", "model_outputs": [], "expect": {"reply_kind": "cancelled", "writes": 0, "api_methods": []}}], "label_status": "proposed_unreviewed", "expect_final": [{"id": "6c99a773-24d3-523e-8149-bd2b6b79d16b", "title": "팀 회의", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "OTHER", "completed": false, "reminderMinutesBefore": null}]}, {"schema_version": "pilot-v0.1", "id": "other-list-followup", "conversation_group_id": "other-list-followup", "reference_datetime": "2026-10-10T09:00:00+09:00", "initial_schedules": [{"id": "6c99a773-24d3-523e-8149-bd2b6b79d16b", "title": "팀 회의", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "OTHER", "completed": false, "reminderMinutesBefore": null, "createdAt": "2026-10-09T09:00:00+09:00", "updatedAt": "2026-10-09T09:00:00+09:00"}, {"id": "e279fd79-71a5-5a01-9fee-809ae2aabb9c", "title": "교대 인수인계", "scheduledAt": "2026-10-11T14:00:00+09:00", "category": "OTHER", "completed": false, "reminderMinutesBefore": null, "createdAt": "2026-10-09T09:00:00+09:00", "updatedAt": "2026-10-09T09:00:00+09:00"}, {"id": "69294e8f-f5fc-5f15-83e9-3394754b4b2a", "title": "반품 검수", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "RETURN", "completed": false, "reminderMinutesBefore": null, "createdAt": "2026-10-09T09:00:00+09:00", "updatedAt": "2026-10-09T09:00:00+09:00"}], "turns": [{"user": "내일 기타 일정 알려줘.", "model_outputs": [{"action": "LIST", "message": null, "request": {"from": "2026-10-11T00:00:00+09:00", "to": "2026-10-12T00:00:00+09:00", "category": "OTHER"}}, {"action": "ANSWER", "message": null, "request": null}], "expect": {"reply_kind": "answer", "writes": 0, "api_methods": ["GET"]}}, {"user": "내일은 다른 종류도 포함해서 전부 알려줘.", "model_outputs": [{"action": "LIST", "message": null, "request": {"from": "2026-10-11T00:00:00+09:00", "to": "2026-10-12T00:00:00+09:00"}}, {"action": "ANSWER", "message": null, "request": null}], "expect": {"reply_kind": "answer", "writes": 0, "api_methods": ["GET"]}}], "label_status": "proposed_unreviewed", "expect_final": [{"id": "6c99a773-24d3-523e-8149-bd2b6b79d16b", "title": "팀 회의", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "OTHER", "completed": false, "reminderMinutesBefore": null}, {"id": "e279fd79-71a5-5a01-9fee-809ae2aabb9c", "title": "교대 인수인계", "scheduledAt": "2026-10-11T14:00:00+09:00", "category": "OTHER", "completed": false, "reminderMinutesBefore": null}, {"id": "69294e8f-f5fc-5f15-83e9-3394754b4b2a", "title": "반품 검수", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "RETURN", "completed": false, "reminderMinutesBefore": null}]}, {"schema_version": "pilot-v0.1", "id": "other-list-range", "conversation_group_id": "other-list-range", "reference_datetime": "2026-10-10T09:00:00+09:00", "initial_schedules": [{"id": "6c99a773-24d3-523e-8149-bd2b6b79d16b", "title": "팀 회의", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "OTHER", "completed": false, "reminderMinutesBefore": null, "createdAt": "2026-10-09T09:00:00+09:00", "updatedAt": "2026-10-09T09:00:00+09:00"}], "turns": [{"user": "회의나 인수인계 같은 기타 일정 보여줘.", "model_outputs": [{"action": "ASK", "message": "어느 날짜의 기타 일정을 볼까요?", "request": null}], "expect": {"reply_kind": "ask", "writes": 0, "api_methods": []}}, {"user": "내일.", "model_outputs": [{"action": "LIST", "message": null, "request": {"from": "2026-10-11T00:00:00+09:00", "to": "2026-10-12T00:00:00+09:00", "category": "OTHER"}}, {"action": "ANSWER", "message": null, "request": null}], "expect": {"reply_kind": "answer", "writes": 0, "api_methods": ["GET"]}}], "label_status": "proposed_unreviewed", "expect_final": [{"id": "6c99a773-24d3-523e-8149-bd2b6b79d16b", "title": "팀 회의", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "OTHER", "completed": false, "reminderMinutesBefore": null}]}, {"schema_version": "pilot-v0.1", "id": "other-update-different-target", "conversation_group_id": "other-update-different-target", "reference_datetime": "2026-10-10T09:00:00+09:00", "initial_schedules": [{"id": "6c99a773-24d3-523e-8149-bd2b6b79d16b", "title": "팀 회의", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "OTHER", "completed": false, "reminderMinutesBefore": null, "createdAt": "2026-10-09T09:00:00+09:00", "updatedAt": "2026-10-09T09:00:00+09:00"}, {"id": "e279fd79-71a5-5a01-9fee-809ae2aabb9c", "title": "교대 인수인계", "scheduledAt": "2026-10-11T14:00:00+09:00", "category": "OTHER", "completed": false, "reminderMinutesBefore": null, "createdAt": "2026-10-09T09:00:00+09:00", "updatedAt": "2026-10-09T09:00:00+09:00"}], "turns": [{"user": "내일 기타 일정 하나를 오후 5시로 바꾸고 싶어.", "model_outputs": [{"action": "LIST", "message": null, "request": {"from": "2026-10-11T00:00:00+09:00", "to": "2026-10-12T00:00:00+09:00", "category": "OTHER"}}, {"action": "CONFIRM_UPDATE", "message": null, "request": {"targetId": "6c99a773-24d3-523e-8149-bd2b6b79d16b", "changes": {"scheduledAt": "2026-10-11T17:00:00+09:00"}}}], "expect": {"reply_kind": "ask", "writes": 0, "api_methods": ["GET"]}}, {"user": "2번", "model_outputs": [], "expect": {"reply_kind": "ask", "writes": 0, "api_methods": []}}, {"user": "선택한 교대 인수인계를 오후 6시로 변경해줘.", "model_outputs": [{"action": "CONFIRM_UPDATE", "message": null, "request": {"targetId": "e279fd79-71a5-5a01-9fee-809ae2aabb9c", "changes": {"scheduledAt": "2026-10-11T18:00:00+09:00"}}}], "expect": {"reply_kind": "confirm", "writes": 0, "api_methods": ["GET"]}}, {"user": "진행해", "model_outputs": [], "expect": {"reply_kind": "updated", "writes": 1, "api_methods": ["GET", "PATCH"], "target_id": "e279fd79-71a5-5a01-9fee-809ae2aabb9c"}}], "label_status": "proposed_unreviewed", "expect_final": [{"id": "6c99a773-24d3-523e-8149-bd2b6b79d16b", "title": "팀 회의", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "OTHER", "completed": false, "reminderMinutesBefore": null}, {"id": "e279fd79-71a5-5a01-9fee-809ae2aabb9c", "title": "교대 인수인계", "scheduledAt": "2026-10-11T18:00:00+09:00", "category": "OTHER", "completed": false, "reminderMinutesBefore": null}]}, {"schema_version": "pilot-v0.1", "id": "other-update-reminder", "conversation_group_id": "other-update-reminder", "reference_datetime": "2026-10-10T09:00:00+09:00", "initial_schedules": [{"id": "6c99a773-24d3-523e-8149-bd2b6b79d16b", "title": "팀 회의", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "OTHER", "completed": false, "reminderMinutesBefore": null, "createdAt": "2026-10-09T09:00:00+09:00", "updatedAt": "2026-10-09T09:00:00+09:00"}], "turns": [{"user": "내일 팀 회의 10분 전 알림을 켜줘.", "model_outputs": [{"action": "LIST", "message": null, "request": {"from": "2026-10-11T00:00:00+09:00", "to": "2026-10-12T00:00:00+09:00", "category": "OTHER"}}, {"action": "CONFIRM_UPDATE", "message": null, "request": {"targetId": "6c99a773-24d3-523e-8149-bd2b6b79d16b", "changes": {"reminderMinutesBefore": 10}}}], "expect": {"reply_kind": "confirm", "writes": 0, "api_methods": ["GET", "GET"]}}, {"user": "응", "model_outputs": [], "expect": {"reply_kind": "updated", "writes": 1, "api_methods": ["GET", "PATCH"], "target_id": "6c99a773-24d3-523e-8149-bd2b6b79d16b"}}], "label_status": "proposed_unreviewed", "expect_final": [{"id": "6c99a773-24d3-523e-8149-bd2b6b79d16b", "title": "팀 회의", "scheduledAt": "2026-10-11T10:00:00+09:00", "category": "OTHER", "completed": false, "reminderMinutesBefore": 10}]}]')
oldrub=json.loads('{"in-create-time": {"1": "time"}, "in-create-past": {"1": "past_time"}, "inv-create-date": {"1": "date"}, "inv-create-reminder": {"1": "time"}, "out-create-ampm": {"1": "ampm"}, "out-create-task": {"1": "task"}, "ret-create-correct": {"1": "time"}, "other-create-clarify": {"1": "category", "2": "time"}, "in-list-range": {"1": "date"}, "other-list-range": {"1": "date"}, "other-update-different-target": {"1": "selection", "2": "change"}, "out-delete-invalid-number": {"1": "selection", "2": "selection"}, "ret-delete-select-approve": {"1": "selection"}, "ret-delete-select-reject": {"1": "selection"}}')
def migrate_multi_candidate_selection(case):
 """Keep the original drafts as lineage, but never teach an ID before selection."""
 for turn in case['turns']:
  outputs=turn['model_outputs']
  if len(outputs)<2 or outputs[0]['action']!='LIST':continue
  filters=outputs[0]['request']
  matches=[item for item in case['initial_schedules']
           if filters['from']<=item['scheduledAt']<filters['to']
           and ('category' not in filters or item['category']==filters['category'])
           and ('completed' not in filters or item['completed']==filters['completed'])]
  if len(matches)<2:continue
  for index,action in enumerate(outputs[1:],1):
   if action['action'] not in ('CONFIRM_UPDATE','CONFIRM_DELETE'):continue
   operation='update' if action['action']=='CONFIRM_UPDATE' else 'delete'
   request={'operation':operation}
   if operation=='update':request['changes']=action['request']['changes']
   outputs[index]=act('SELECT_TARGET',request)

for case in original:migrate_multi_candidate_selection(case)
# A different selected target now receives an immediate confirmation. The later
# correction names that selected schedule and may reuse just that candidate.
for case in original:
 if case['id']=='other-update-different-target':
  case['turns'][1]['expect']={'reply_kind':'confirm','writes':0,'api_methods':['GET']}
oldrub['other-update-different-target'].pop('2')
bycat=defaultdict(list)
for c in original:
 prefix=c['id'].split('-')[0];cat={'in':'INBOUND','inv':'INVENTORY','out':'OUTBOUND','ret':'RETURN','other':'OTHER'}[prefix]
 bycat[cat].append(c)
 x=copy.deepcopy(c);register(x,'train',cat,'legacy:'+c['conversation_group_id'],'original_30',c['id'])
 if c['id'] in oldrub:RUBRIC[c['id']]=copy.deepcopy(oldrub[c['id']])
# 230 additional train dialogues. Preserve causal path, vary task vocabulary,
# reference calendar, fixture identifiers, and follow-up phrasing. IDs retain lineage.
for ci,cat in enumerate(CATS):
 for v in range(46):
  base=bycat[cat][v%len(bycat[cat])];x=copy.deepcopy(base)
  new_id=f'train-{cat.lower()}-{v+1:03d}';ref=datetime.fromisoformat(DATES[(v+ci)%len(DATES)]+'T09:00:00+09:00')+timedelta(days=3*(1+v//8))
  delta=ref-datetime.fromisoformat(base['reference_datetime'])
  fixture_ids={item['id'] for item in base['initial_schedules']}|({base['created_id']} if 'created_id' in base else set())
  idmap={i:uid(new_id+':'+i) for i in fixture_ids}
  # Replace complete literal task titles, longest first. Distinct titles stay distinct.
  tasks=[]
  for item in base['initial_schedules']+base['expect_final']:
   pair=(item.get('title'),item.get('category'))
   if pair[0] and pair not in tasks:tasks.append(pair)
  replacements={};used=defaultdict(set)
  for title,categ in tasks:
   pool=TITLES[categ];idx=(v+ci+len(used[categ]))%len(pool)
   while pool[idx] in used[categ]:idx=(idx+1)%len(pool)
   replacements[title]=pool[idx];used[categ].add(pool[idx])
  def transform(obj):
   if isinstance(obj,dict):return {k:transform(val) for k,val in obj.items()}
   if isinstance(obj,list):return [transform(y) for y in obj]
   if not isinstance(obj,str):return obj
   if obj in idmap:return idmap[obj]
   if len(obj)>=25 and obj[:4].isdigit() and 'T' in obj:
    try:return (datetime.fromisoformat(obj)+delta).isoformat(timespec='seconds')
    except ValueError:pass
   # One-pass placeholders prevent cascading replacements. Re-select an attached
   # particle because the replacement title may end in a different consonant.
   titles=sorted(replacements,key=len,reverse=True)
   for i,t in enumerate(titles):obj=obj.replace(t,f'@@TASK{i}@@')
   def restore(match):
    title=replacements[titles[int(match.group(1))]];particle=match.group(2)
    return title+(particle_for(title,particle) if particle else '')
   obj=re.sub(r'@@TASK(\d+)@@(?:(으로|로|을|를|이야|야|이|가|은|는|과|와)(?=$|[\s.,!?"“”‘’]))?',restore,obj)
   return obj
  x=transform(x);x['id']=new_id;x['conversation_group_id']=base['conversation_group_id']
  # Follow-up yes/no turns are controller turns; no manufactured model answer.
  for t in x['turns']:
   if not t['model_outputs'] and t['expect']['reply_kind'] in ('updated','deleted'):t['user']=['네','응','진행해','확인'][v%4]
   elif not t['model_outputs'] and t['expect']['reply_kind']=='cancelled':t['user']=['아니요','취소','그만'][v%3]
  register(x,'train',cat,'legacy:'+base['conversation_group_id'],'authored_variation',base['id'])
  if base['id'] in oldrub:RUBRIC[new_id]=copy.deepcopy(oldrub[base['id']])

class Builder:
 def __init__(self,split,cat,family,v):
  self.cat=cat;self.family=family;self.v=v;self.split=split
  self.id=f'{split}-{cat.lower()}-{family.replace(":","-")}-{v+1:02d}'
  self.now=datetime.fromisoformat(DATES[(v+CATS.index(cat))%len(DATES)]+'T09:00:00+09:00')
  self.title=TITLES[cat][(v+CATS.index(cat))%8];self.day=1+v%2;self.hour=14+v%4;self.minute=[0,15,30,45][v%4]
  self.c={'schema_version':'pilot-v0.1','id':self.id,'conversation_group_id':'family:'+family,'reference_datetime':self.now.isoformat(),'initial_schedules':[],'turns':[],'label_status':'proposed_unreviewed','expect_final':[]}
  self.questions={}
 def dt(self,day,hour=0,minute=0):return (self.now+timedelta(days=day)).replace(hour=hour,minute=minute).isoformat(timespec='seconds')
 def clock(self,h=None):
  h=self.hour if h is None else h
  return f'오후 {h-12}시'+(f' {self.minute}분' if self.minute else '')
 def fixture(self,cat=None,day=None,hour=10,completed=False,alarm=None):
  cat=cat or self.cat;day=self.day if day is None else day;n=len(self.c['initial_schedules'])
  x={'id':uid(self.id+':fixture:'+str(n)),'title':TITLES[cat][(self.v+CATS.index(cat)+n)%8],'scheduledAt':self.dt(day,hour),'category':cat,'completed':completed,'reminderMinutesBefore':alarm,'createdAt':self.dt(-1,9),'updatedAt':self.dt(-1,9)}
  self.c['initial_schedules'].append(x);return x
 def emit(self,*args,**kw):self.c['turns'].append(turn(*args,**kw))
 def ask(self,user,kind):
  self.emit(user,[act('ASK',message=Q[kind])],'ask');self.questions[str(len(self.c['turns']))]=kind
 def query(self,user,day=None,cat='same',completed=None):
  day=self.day if day is None else day;cat=self.cat if cat=='same' else cat
  req={'from':self.dt(day),'to':self.dt(day+1)}
  if cat is not None:req['category']=cat
  if completed is not None:req['completed']=completed
  self.emit(user,[act('LIST',req),act('ANSWER')],'answer',['GET'])
 def listing(self,day=None):
  day=self.day if day is None else day
  return act('LIST',{'from':self.dt(day),'to':self.dt(day+1),'category':self.cat})
 def finish(self,final=None):
  self.c['expect_final']=clean(self.c['initial_schedules'] if final is None else final)
  if self.questions:RUBRIC[self.id]=self.questions
  register(self.c,self.split,self.cat,self.family,'new_composition')
 def confirm(self,user,target,changes=None):
  name='CONFIRM_DELETE' if changes is None else 'CONFIRM_UPDATE';req={'targetId':target['id']}
  if changes is not None:req['changes']=changes
  self.emit(user,[self.listing(),act(name,req)],'confirm',['GET','GET'])
 def decision(self,target,changes=None):
  approve=self.v%2==0
  if approve:self.emit('네',[],'deleted' if changes is None else 'updated',['GET','DELETE' if changes is None else 'PATCH'],1,target_id=target['id'])
  else:self.emit('취소',[],'cancelled')
  final=copy.deepcopy(self.c['initial_schedules'])
  if approve:
   if changes is None:final=[x for x in final if x['id']!=target['id']]
   else:
    next(x for x in final if x['id']==target['id']).update(changes)
  self.finish(final)

def create(b,mode):
 d=DAYS[b.day];tm=b.clock();alarm=10 if b.v%2 else None;tail=' 10분 전 알림도 부탁해.' if alarm==10 else ''
 if mode=='task_date':
  b.ask(f'{tm}에 일정 등록해줘.'+tail,'task');b.ask(b.title,'date');last=f'{d}로 해줘.'
 elif mode=='date_time':
  b.ask(f'{b.title} 등록해줘.'+tail,'date');b.ask(f'{d}.','time');last=tm+particle_for(tm,'야')+'.'
 elif mode=='task_date_time':
  b.ask('일정 등록해줘.'+tail,'task');b.ask(b.title,'date');b.ask(d+particle_for(d,'야')+'.','time');last=tm+particle_for(tm,'로')+' 해줘.'
 elif mode=='date_ampm':
  b.ask(f'{b.hour-12}시에 {b.title} 등록해줘.'+tail,'date');b.ask(d+particle_for(d,'야')+'.','ampm');last=tm+particle_for(tm,'야')+'.'
 elif mode=='past_then_ambiguous':
  b.ask(f'오늘 아침 8시에 {b.title} 등록해줘.'+tail,'past_time');b.ask(f'{d} {b.hour-12}시로 할게.','ampm');last=tm+particle_for(tm,'야')+'.'
 body={'title':b.title,'scheduledAt':b.dt(b.day,b.hour,b.minute),'category':b.cat,'reminderMinutesBefore':alarm}
 b.c['created_id']=uid(b.id+':created');b.emit(last,[act('CREATE',body)],'created',['POST'],1,post_body=body)
 b.finish([{'id':b.c['created_id'],**body,'completed':False}])

def query(b,mode):
 d=DAYS[b.day];k=KO[b.cat];other=CATS[(CATS.index(b.cat)+1)%5]
 # Exact midnight inclusions/exclusions, category and completion distractors.
 b.fixture(hour=0);b.fixture(hour=15,completed=True);b.fixture(cat=other,hour=11)
 b.fixture(day=b.day+1,hour=0);b.fixture(day=0,hour=23)
 if mode=='missing_then_incomplete':
  b.ask(f'끝내지 않은 {k} 일정 보여줘.','date');b.query(f'{d} 일정.',completed=False)
 elif mode=='missing_then_complete':
  b.ask(f'완료한 {k} 일정 조회해줘.','date');b.query(f'{d} 일정.',completed=True)
 elif mode=='unsupported_then_toggle':
  b.ask(f'다음 주 {k} 일정 보여줘.','date');b.query(f'그럼 {d} 끝내지 않은 {k} 일정 보여줘.',completed=False);b.query(f'{d} 완료한 {k} 일정만 보여줘.',completed=True)
 elif mode=='filter_then_day':
  b.query(f'{d} 끝내지 않은 {k} 일정 알려줘.',completed=False);b.query(f'{d} 완료한 {k} 일정만 보여줘.',completed=True);b.query(f'오늘 {k} 일정은 완료 여부 상관없이 보여줘.',day=0)
 elif mode=='scope_then_filter':
  b.query(f'{d} 모든 종류의 일정 보여줘.',cat=None);b.query(f'{d} {k} 일정만 보여줘.');b.query(f'{d} 끝내지 않은 {k} 일정만 알려줘.',completed=False)
 b.finish()

def update(b,mode):
 d=DAYS[b.day];target=b.fixture(alarm=None);b.fixture(cat=CATS[(CATS.index(b.cat)+1)%5]);title=target['title'];newtitle=title+' 재확인';quoted=f'"{newtitle}"{particle_for(newtitle,"로")}'
 if mode=='title_alarm':changes={'title':newtitle,'reminderMinutesBefore':10};text=f'{d} {title} 제목을 {quoted} 바꿔줘. 10분 전 알림도 켜줘.'
 elif mode=='completed_alarm':changes={'completed':True,'reminderMinutesBefore':10};text=f'{d} {title} 일정을 완료로 바꿔줘. 10분 전 알림도 켜줘.'
 elif mode=='time_alarm':changes={'scheduledAt':b.dt(b.day,b.hour,b.minute),'reminderMinutesBefore':10};text=f'{d} {title} 시간을 {b.clock()}{particle_for(b.clock(),"로")} 바꿔줘. 10분 전 알림도 켜줘.'
 elif mode=='title_time':changes={'title':newtitle,'scheduledAt':b.dt(b.day,b.hour,b.minute)};text=f'{d} {title} 제목을 {quoted} 바꿔줘. 시간은 {b.clock()}{particle_for(b.clock(),"로")} 변경해줘.'
 elif mode=='missing_date_title':
  changes={'title':newtitle};b.ask(f'{title} 일정 제목을 {quoted} 바꿔줘.','date');text=f'{d} 일정이야.'
 b.confirm(text,target,changes);b.decision(target,changes)

def delete(b,mode):
 d=DAYS[b.day];k=KO[b.cat]
 if mode in ('missing_date_single','empty_then_single'):
  target=b.fixture();b.fixture(cat=CATS[(CATS.index(b.cat)+1)%5])
  if mode=='missing_date_single':b.ask(f'{k} 일정 삭제해줘.','date')
  else:b.query(f'오늘 {k} 일정 삭제해줘.',day=0)
  b.confirm(f'{d} {k} 일정 삭제해줘.',target);b.decision(target);return
 a=b.fixture(hour=10);target=b.fixture(hour=14);b.fixture(cat=CATS[(CATS.index(b.cat)+1)%5])
 if mode=='missing_date_select':b.ask(f'{k} 일정 하나 삭제해줘.','date')
 elif mode=='unsupported_select':b.ask(f'다음 주 {k} 일정 하나 삭제해줘.','date')
 elif mode=='empty_then_select':b.query(f'오늘 {k} 일정 삭제해줘.',day=0)
 b.emit(f'{d} {k} 일정 하나 삭제해줘.',[b.listing(),act('SELECT_TARGET',{'operation':'delete'})],'ask',['GET']);b.questions[str(len(b.c['turns']))]='selection'
 b.emit('2번',[],'confirm',['GET']);b.decision(target)

# Held-out compositions, assigned BEFORE lexical/calendar variants are authored.
# Approval/rejection and all five business categories of a family remain together.
FAMILIES={
'validation': [('create:task_date',create,1),('create:date_time',create,1),('query:missing_then_incomplete',query,1),('query:missing_then_complete',query,1),('update:title_alarm',update,1),('update:completed_alarm',update,1),('delete:missing_date_single',delete,1),('delete:empty_then_single',delete,1)],
 'test': [('create:task_date_time',create,2),('create:date_ampm',create,2),('create:past_then_ambiguous',create,1),('query:unsupported_then_toggle',query,2),('query:filter_then_day',query,2),('query:scope_then_filter',query,1),('update:time_alarm',update,2),('update:title_time',update,2),('update:missing_date_title',update,1),('delete:unsupported_select',delete,5)]}
for split,families in FAMILIES.items():
 for ci,cat in enumerate(CATS):
  for family,build,n in families:
   for v in range(n):build(Builder(split,cat,family,v+ci),family.split(':')[1])

# Preserve draft scenarios even if a verification fails, with no release manifest.
app.save_jsonl(ROOT/'scenarios_all_400_UNREVIEWED.jsonl',ALL)
app.save_json(ROOT/'question_rubric.json',RUBRIC)
app.save_jsonl(ROOT/'case_manifest.jsonl',META)
assert len(ALL)==400 and len({c['id'] for c in ALL})==400
assert Counter(m['split'] for m in META)=={'train':260,'validation':40,'test':100}
meta={m['case_id']:m for m in META};groups=defaultdict(set)
for m in META:groups[m['conversation_group_id']].add(m['split'])
assert all(len(s)==1 for s in groups.values())
results=[];failures=[]
for c in ALL:
 client=ReferenceClient([a for t in c['turns'] for a in t['model_outputs']])
 try:
  r=app.run_case(c,client,RUBRIC)
  r['source_kind']='scripted_reference';r['run_id']='dataset400-v0.1-scripted-reference';r['dataset_split']=meta[c['id']]['split']
  if not r['provisional_match'] or client.outputs or any(t['filter_events'] or t['guard_events'] or t['crash'] for t in r['turns']):failures.append({'case_id':c['id'],'result':r})
  results.append(r)
 except Exception as e:failures.append({'case_id':c['id'],'error':str(e)})
app.save_json(ROOT/'verification_failures.json',failures)
if failures:
 print('FAILURES',len(failures))
 for f in failures[:12]:print(f['case_id'],f.get('error') or [(t['index'],t['reply'],[x for x in t['checks'] if not x['match']]) for t in f['result']['turns'] if any(not x['match'] for x in t['checks'])])
 sys.exit(1)

# Validate the subset of JSON Schema emitted by schema_for, without dependencies.
def schema_matches(value,schema):
 if 'anyOf' in schema:return any(schema_matches(value,b) for b in schema['anyOf'])
 types={'null':lambda v:v is None,'string':lambda v:isinstance(v,str),'object':lambda v:isinstance(v,dict),'integer':lambda v:type(v) is int,'boolean':lambda v:type(v) is bool}
 if 'type' in schema and not types[schema['type']](value):return False
 if 'enum' in schema and value not in schema['enum']:return False
 if isinstance(value,str) and not schema.get('minLength',0)<=len(value)<=schema.get('maxLength',10**9):return False
 if schema.get('type')=='object':
  props=schema.get('properties',{})
  if len(value)<schema.get('minProperties',0):return False
  if set(schema.get('required',[]))-value.keys():return False
  if schema.get('additionalProperties') is False and value.keys()-props.keys():return False
  if any(not schema_matches(v,props[k]) for k,v in value.items() if k in props):return False
 return True
schema_errors=[]
for r in results:
 for t in r['turns']:
  for m in t['model_calls']:
   if not schema_matches(json.loads(m['raw']),m['schema']):schema_errors.append((r['case_id'],t['index'],m['raw']))
assert not schema_errors,schema_errors[:10]
# Exact whole-conversation duplicates, ignoring generated UUIDs, are forbidden.
fingerprints=set()
for c in ALL:
 canonical=json.dumps({k:v for k,v in c.items() if k not in ('id','conversation_group_id','created_id')},sort_keys=True,ensure_ascii=False)
 canonical=re.sub(r'[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}','UUID',canonical)
 h=hashlib.sha256(canonical.encode()).hexdigest()
 assert h not in fingerprints,('duplicate dialogue',c['id'])
 fingerprints.add(h)

# Actual application inputs + proposed reference action. No raw Qwen run is read.
rowhashes={};stats={}
for split in ['train','validation','test']:
 dest=ROOT/split;dest.mkdir(exist_ok=True)
 cases=[c for c in ALL if meta[c['id']]['split']==split];rr=[r for r in results if meta[r['case_id']]['split']==split]
 app.save_jsonl(dest/'scenarios_UNREVIEWED.jsonl',cases);app.save_jsonl(dest/'review_cases.jsonl',rr)
 rows=[];maps=[];seen={}
 for r in rr:
  for t in r['turns']:
   for callindex,m in enumerate(t['model_calls'],1):
    assert m['raw'] and not m['error'] and not m['parse_error']
    action=json.loads(m['raw']);assert action in t['expected_model_outputs']
    sample={'messages':m['messages']+[{'role':'assistant','content':m['raw']}]}
    key=hashlib.sha256(json.dumps(sample,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    if key not in seen:rows.append(sample);seen[key]=len(rows)
    maps.append({'row':seen[key],'row_sha256':key,'case_id':r['case_id'],'conversation_group_id':r['conversation_group_id'],'turn':t['index'],'call':callindex,'label_status':'proposed_unreviewed','schema':m['schema']})
 name='training_UNREVIEWED.jsonl' if split=='train' else 'validation_UNREVIEWED.jsonl' if split=='validation' else 'reference_actions_DO_NOT_TRAIN.jsonl'
 app.save_jsonl(dest/name,rows);app.save_jsonl(dest/'row_map.jsonl',maps)
 app.save_jsonl(dest/'review_decisions_TEMPLATE.jsonl',[{'case_id':c['id'],'conversation_group_id':c['conversation_group_id'],'label_status':'proposed_unreviewed','reviewer':None,'reviewed_at':None,'note':None} for c in cases])
 lines=['# '+split+' 대화 정답 검수표','','모두 미검수 초안입니다. 자동 통과는 실제 Qwen 성능이나 사람의 정답 승인을 뜻하지 않습니다.','']
 for c in cases:
  lines += ['## '+c['id'],'','대화 그룹: '+c['conversation_group_id'],'기준 시각: '+c['reference_datetime'],'검수자 / 승인·수정·제외 / 이유: ____________________','','초기 일정:','```json',json.dumps(c['initial_schedules'],ensure_ascii=False,indent=2),'```']
  for n,t in enumerate(c['turns'],1):
   lines += ['',f"### {n}턴",'사용자: '+t['user'],'','질문 검사: '+RUBRIC.get(c['id'],{}).get(str(n),'해당 없음'),'예상 프로그램 응답 종류: '+t['expect']['reply_kind'],'```json',json.dumps(t['model_outputs'],ensure_ascii=False,indent=2),'```']
   if not t['model_outputs']:lines += ['이 턴은 프로그램이 처리하므로 학습 정답 행을 만들지 않습니다.']
  lines += ['','최종 일정:','```json',json.dumps(c['expect_final'],ensure_ascii=False,indent=2),'```','']
 (dest/'검수표.md').write_text('\n'.join(lines),encoding='utf-8')
 rowhashes[split]=set(seen)
 stats[split]={'dialogues':len(cases),'conversation_groups':len({c['conversation_group_id'] for c in cases}),'user_turns':sum(len(c['turns']) for c in cases),'model_calls':len(maps),'unique_supervised_rows':len(rows),'categories':dict(Counter(meta[c['id']]['category'] for c in cases))}
for a,b in [('train','validation'),('train','test'),('validation','test')]:assert not rowhashes[a]&rowhashes[b],(a,b,'identical row leakage')
csv_path=ROOT/'scenarios_all_400_UNREVIEWED_turns.csv'
with csv_path.open(encoding='utf-8-sig',newline='') as source:
 reader=csv.DictReader(source);fieldnames=reader.fieldnames;csv_rows=list(reader)
turn_by_id={(case['id'],str(index)):turn for case in ALL for index,turn in enumerate(case['turns'],1)}
assert len(csv_rows)==len(turn_by_id) and {(row['scenario_id'],row['turn_number']) for row in csv_rows}==set(turn_by_id),'review CSV is out of sync with scenario IDs/turns'
for row in csv_rows:
 turn=turn_by_id[(row['scenario_id'],row['turn_number'])]
 row['user']=turn['user']
 row['model_outputs_json']=json.dumps(turn['model_outputs'],ensure_ascii=False,separators=(',',':'))
with csv_path.open('w',encoding='utf-8-sig',newline='') as target:
 writer=csv.DictWriter(target,fieldnames=fieldnames,lineterminator='\r\n')
 writer.writeheader();writer.writerows(csv_rows)
app.save_json(ROOT/'verification_summary.json',{'status':'scripted_reference_passed_NOT_MODEL_ACCURACY','cases':len(results),'matches':len(results),'filter_corrections':0,'blocked_writes':0,'group_overlap':0,'exact_row_overlap':0,'human_reviewed':0,'dynamic_action_schema_errors':0,'exact_dialogue_duplicates_ignoring_uuid':0,'splits':stats})
files=[p for p in ROOT.rglob('*') if p.is_file() and p.name!='manifest.json' and '__pycache__' not in str(p)]
app.save_json(ROOT/'manifest.json',{'dataset_version':'qwen-dataset-400-v0.1.1-proposed','label_status':'proposed_unreviewed','source_run_for_context_only':'eval_20261011_230323_c4efb5','source_run_answers_used':False,'split_unit':'conversation_group_id','test_status':'reserved_draft_not_human_reviewed_not_final_locked_benchmark','supervision':'only final assistant action per row; prior assistant messages are context','splits':stats,'sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}})
print(json.dumps(stats,ensure_ascii=False,indent=2));print('400 scripted reference dialogues passed; human reviewed: 0')
