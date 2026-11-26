"""Conservative evidence guards. No scenario expectations are used here.
Supported date/time grammar is deliberately bounded; unknown forms fail closed.
"""
import re
from datetime import datetime,timedelta

CATEGORY_WORDS = {'INBOUND': r'입고', 'INVENTORY': r'재고',
                  'OUTBOUND': r'출고|피킹|포장', 'RETURN': r'반품',
                  'OTHER': r'기타|회의|인수인계|안전\s*점검|장비\s*점검'}

def requests_reminder(text):
    return '알림' in text or bool(re.search(r'\d+\s*분\s*전에?\s*(?:알려|보여)|미리\s*알려', text))

def task_evidence(users):
    """Bounded warehouse vocabulary; unknown tasks remain unverified, not OTHER."""
    result = {'status': 'missing', 'category': None, 'source': None}
    for text in users:
        # A correction replaces the prior task; its rejected prefix is not evidence.
        source = re.split(r'말고|대신', text)[-1]
        categories = [k for k, pattern in CATEGORY_WORDS.items() if re.search(pattern, source)]
        has_task = bool(re.search(r'확인|검수|실사|조사|준비|피킹|포장|정리|회의|인수인계|점검|상차|촬영|분류', source))
        if has_task or categories:
            result = {'status': 'confirmed' if has_task and len(categories)==1 else 'ambiguous',
                      'category': categories[0] if len(categories)==1 else None, 'source': source}
    return result

def query_scope(users):
    """Current explicit category, with an explicit all-category reset."""
    category=None; known=False
    for text in users:
        categories=[k for k,p in CATEGORY_WORDS.items() if re.search(p,text)]
        all_categories=bool(re.search(r'다른\s*종류.*포함|모든\s*종류|카테고리.*(?:상관없이|무관)|전체|전부|모두',text))
        if all_categories and (not categories or re.search(r'다른\s*종류.*포함|모든\s*종류|카테고리.*(?:상관없이|무관)',text)):
            category=None;known=True
        elif len(categories)==1:
            category=categories[0];known=True
    return {'known':known,'category':category}

def required_question(users,now):
    task=task_evidence(users);facts=evidence(users,now)
    if task['status']=='missing': return 'task'
    if task['status']!='confirmed': return 'category'
    if facts['day'] is None: return 'date'
    if facts['ambiguous']: return 'ampm'
    if facts['clock'] is None: return 'time'
    if facts['datetime']<now: return 'past_time'
    return None

def query_date_range(users,now):
    """Only unambiguous relative calendar days. Never read scenario labels.

    Unsupported new date/range expressions invalidate a prior known day.
    Modification target dates and proposed replacement dates are not conflated.
    """
    result={'status':'missing','from':None,'to':None,'source':None}
    for index,text in enumerate(users):
        source=re.sub(r'["“‘\'][^"”’\']*["”’\']','',text)
        days=re.findall(r'(오늘|내일|모레)(?!\s*(?:배송분|입고분|할\s*일))',source)
        intent=intent_from_users(users[:index+1])
        unsupported=bool(re.search(r'다음\s*주|이번\s*주|주말|어제|그제|글피|\d{4}-\d{2}-\d{2}|\d+\s*(?:월|일)|월요일|화요일|수요일|목요일|금요일|토요일|일요일|부터|까지',source))
        # Whole-day bounds would discard an explicit time constraint in a query.
        if intent=='query' and re.search(r'오전|오후|아침|저녁|야간|\d+\s*시|이후|이전',source): unsupported=True
        if unsupported or len(set(days))>1:
            result={'status':'unsupported','from':None,'to':None,'source':text}
        elif days:
            start=(now+timedelta(days={'오늘':0,'내일':1,'모레':2}[days[0]])).replace(hour=0,minute=0,second=0,microsecond=0)
            result={'status':'known','from':start.isoformat(timespec='seconds'),'to':(start+timedelta(days=1)).isoformat(timespec='seconds'),'source':text}
    return result

def evidence(users,now):
    day=None;clock=None;ambiguous=False
    for text in users:
        # Quoted titles and task-target dates are not schedule date evidence.
        source=re.sub(r'["“‘\'][^"”’\']*["”’\']','',text)
        for m in re.finditer(r'(오늘|내일|모레)(?!\s*(?:배송분|입고분|할\s*일))',source):
            day=now.date()+timedelta(days={'오늘':0,'내일':1,'모레':2}[m.group(1)])
        for m in re.finditer(r'(?:(오전|오후|아침|저녁)\s*)?(\d{1,2})\s*시(?:\s*(\d{1,2})\s*분)?',source):
            period,hour,minute=m.groups();hour=int(hour);minute=int(minute or 0)
            ambiguous=period is None and 1<=hour<=12
            clock=None
            if not ambiguous and 0<=hour<=23 and 0<=minute<60:
                if period in {'오후','저녁'} and hour<12: hour+=12
                if period in {'오전','아침'} and hour==12: hour=0
                clock=(hour,minute)
    value=None
    if day is not None and clock is not None:
        value=datetime.combine(day,datetime.min.time(),tzinfo=now.tzinfo).replace(hour=clock[0],minute=clock[1])
    return {'day':day,'clock':clock,'ambiguous':ambiguous,'datetime':value}

def validate_write(body,users,now,operation='create',current=None):
    if users and requests_reminder(users[-1]):
        return '알림 요청은 지원하지 않습니다. 알림 없는 일정 작업에 대한 확인이 필요합니다.'
    facts=evidence(users,now)
    if operation=='create':
        task=task_evidence(users)
        if task['status']!='confirmed': return '업무와 카테고리의 발화 근거가 부족합니다.'
        if body.get('category')!=task['category']: return '카테고리가 확인된 업무와 다릅니다.'
        title=re.sub(r'\s+','',body.get('title',''))
        title_task=task_evidence([body.get('title','')])
        if not title or title not in re.sub(r'\s+','',task['source']) or title_task['status']!='confirmed' or title_task['category']!=task['category']:
            return '제목이 확인된 업무 발화에 없습니다. 별도 검수가 필요합니다.'
    if operation=='update':
        if 'title' in body and (not isinstance(body['title'],str) or not any(
                '제목' in u and re.sub(r'\s+','',body['title']) in re.sub(r'\s+','',u)
                for u in users)):
            return '변경할 제목의 발화 근거가 없습니다.'
        if 'completed' in body:
            requested=(r'완료(?:로|\s*처리|\s*표시)' if body['completed']
                       else r'미완료(?:로|\s*처리|\s*표시)|완료.*(?:취소|해제)')
            if type(body['completed']) is not bool or not any(re.search(requested,u) for u in users):
                return '완료 상태 변경의 발화 근거가 없습니다.'
        if 'category' in body:
            word=CATEGORY_WORDS.get(body['category'])
            if not word or not any(re.search(r'(?:분류|카테고리)(?:를|을)?[^.?!]*?(?:'+word+r')\s*(?:로|으로)',u)
                                   and re.search(r'바꿔|변경|수정',u) for u in users):
                return '카테고리 변경의 발화 근거가 없습니다.'
    if 'scheduledAt' in body:
        expected=facts['datetime']
        if operation=='update' and expected is None and facts['clock'] and current:
            base=datetime.fromisoformat(current['scheduledAt'].replace('Z','+00:00')).astimezone(now.tzinfo)
            expected=base.replace(hour=facts['clock'][0],minute=facts['clock'][1],second=0,microsecond=0)
        if expected is None: return '시각의 발화 근거가 없거나 날짜·오전/오후가 불명확합니다.'
        actual=datetime.fromisoformat(body['scheduledAt'].replace('Z','+00:00'))
        if actual!=expected: return '모델이 만든 시각이 사용자 발화의 시각과 다릅니다.'
        if actual<now: return '이미 지난 시각입니다.'
    if 'reminderMinutesBefore' in body or 'reminderAt' in body:
        return '지원하지 않는 알림 필드입니다.'
    return None

# A conservative lexical rubric, not a general Korean semantic judge.
# Unknown wording must be reviewed, never silently treated as a successful ASK.
def check_question(kind,text,selection_active=False):
    if not isinstance(text,str): return False
    # A keyword in a statement is not a question to the user.
    asks=bool(re.search(r'[?？]|(?:까요|나요|세요|주세요|줘요|줘)\s*[.!?]?(?:\s|$)',text))
    if kind=='unsupported_reminder': return asks and '알림' in text and '지원하지' in text
    if kind=='selection': return asks and selection_active and ('번호' in text or ('1번' in text and '2번' in text))
    if kind=='scope': return asks and bool(re.search(r'(?:범위|날짜|제목).*(?:좁혀|구체적으로|다시)',text))
    if kind=='change': return asks and bool(re.search(r'변경.*(?:내용|말씀|알려)',text))
    if kind=='ampm': return asks and '오전' in text and '오후' in text and bool(re.search(r'인가|어느|몇|오전.*오후.*[?？]',text))
    if kind=='date': return asks and bool(re.search(r'(?:어느|어떤|무슨)\s*(?:날짜|날)|날짜.*(?:알려|말씀|언제)|며칠|언제.*(?:일정|등록|진행|잡)',text))
    if kind=='task': return asks and bool(re.search(r'(어떤|무슨).*?(업무|일정|일)|업무.*(알려|말씀)|제목.*(알려|말씀|정해)',text))
    if kind=='category': return asks and bool(re.search(r'(어떤|무슨).*?(업무|종류)|입고.*반품|업무.*(알려|말씀)',text))
    if kind in {'time','past_time'}:
        positive=bool(re.search(r'몇\s*시|(?:어떤|어느|무슨)\s*(?:시간|시각)(?:에|으로|을|이)|시각.*(?:알려|말씀)|시간.*(?:알려|말씀)|언제로|언제.*(?:등록|변경|진행)',text))
        bad=bool(re.search(r'언제까지|얼마나|몇\s*시간|소요|걸리|동안|제목.*(?:정해|알려)|어떤.*업무',text))
        # A yes/no proposal or an invented concrete time is not a request for the missing time.
        bad=bad or bool(re.search(r'\d{1,2}\s*시|\d{1,2}:\d{2}',text))
        if kind=='past_time': positive=positive and bool(re.search(r'지났|지난|과거',text))
        return asks and positive and not bad
    raise ValueError('Unknown question rubric: '+kind)

def intent_from_users(users):
    """Bounded lexical routing from real utterances, never gold actions/case IDs."""
    intent=None
    for text in users:
        if re.search(r'삭제|지우|지워|지울',text): intent='delete'
        elif re.search(r'바꾸|바꿔|변경|수정|완료로|알림.*(?:켜|꺼|해제)',text): intent='update'
        elif re.search(r'등록|잡아|잡고|넣',text): intent='create'
        elif re.search(r'보여|알려|일정.*있어|조회',text): intent='query'
    return intent
