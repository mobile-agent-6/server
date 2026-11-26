"""Full-command source grammar for evidence roles, over frozen v10.

No dataset, case ID, title dictionary, model call or database access. Rebuild only
when every user turn is consumed by one supported, unambiguous command grammar.
Unrecognized grammar uses v10; ambiguous supported corrections request restatement.
"""
import copy
import json
import re
from dataclasses import dataclass
from evidence_postprocess_v10 import apply_rules as apply_v10, ClarificationRequired, history
from evidence_postprocess_v3 import DATE, TIME, dump
from category_postprocess import CUES, _validate, _reject_constant, MAX_PAYLOAD_BYTES

VERSION = 'evidence-rules-v11'
ROLES = ('targetEvidence', 'changeEvidence', 'filterEvidence')
STATE = r'(?:이미 완료된|아직 안 끝난|완료 안 된|완료가 아닌|미완료인|완료된|미완료|끝난)'
CATEGORY = rf'(?:분류가|카테고리가) (?:{"|".join(CUES)})인'
QUERY = r'(?:조회해|검색해|찾아|보여)\s*(?:줘|주세요)'
DELETE = r'(?:삭제해|지워)\s*(?:줘|주세요)'
CHANGE = r'(?:바꿔|변경해|옮겨|조정해|수정해)\s*(?:줘|주세요)'
COMPLETE = r'(?:(?:미완료로|완료로) (?:표시해|바꿔|변경해|되돌려)|완료 처리해|완료 표시를 해제해)\s*(?:줘|주세요)'
END = r'\s*[.!。]?\s*'
MARK = re.compile(r'말고|대신|(?<![가-힣])아니(?:다|라|고)?(?![가-힣])')
UNCERTAIN = re.compile(r'또는|혹은|아마|아무거나|둘 중|(?:이거|그거|저거|이걸|그걸|저걸)(?![가-힣])|(?:하지|지)\s*(?:마|말)|취소하지')
OUTSIDE = re.compile(r'[?？"\x27“”‘’\n\r]|그리고|또한|알림|매일|매주|부터|까지|반복')
TITLE_BAD = re.compile(r'말고|대신|아니|또는|혹은|그리고|(?:일정|제목|분류|카테고리|시간|시각|날짜)(?:은|는|을|를|이|가)?(?:\s|$)|등록해|삭제해|지워|조회해|찾아|보여|바꿔|변경해|수정해|옮겨|해 줘')


@dataclass(frozen=True)
class Result:
    raw: str
    changed: bool
    reasons: tuple[str, ...]
    version: str = VERSION


class NoPlan(ValueError):
    pass


class Pairs(dict):
    def __init__(self, pairs):
        self.duplicates = []
        for k, v in pairs:
            if k in self:
                self.duplicates.append(k)
            self[k] = v


def model_envelope(raw):
    """Do not silently accept duplicate operation kinds or other structural keys.

    Duplicate completed slots may be discarded ONLY with a full independent
    source plan. No last-value-wins payload is ever returned to the caller.
    """
    if not isinstance(raw, str) or len(raw.encode()) > MAX_PAYLOAD_BYTES:
        raise NoPlan()
    v = json.loads(raw, object_pairs_hook=Pairs, parse_constant=_reject_constant)
    if not isinstance(v, dict) or v.duplicates or set(v) != {'operations', 'unsupportedEvidence', 'missingInformation'}:
        raise NoPlan()
    if v['unsupportedEvidence'] or not isinstance(v['missingInformation'], list) or not isinstance(v['operations'], list) or len(v['operations']) != 1:
        raise NoPlan()
    op = v['operations'][0]
    if not isinstance(op, dict) or op.duplicates or set(op) != {'index', 'kind', *ROLES, 'unsupportedEvidence', 'missingInformation'}:
        raise NoPlan()
    if op['index'] != 1 or op['unsupportedEvidence'] or not isinstance(op['missingInformation'], list):
        raise NoPlan()
    slots = {'title','date','time','category','completed','completedState','futureScope'}
    for role in ROLES:
        mapping = op[role]
        if not isinstance(mapping, dict) or set(mapping) - slots or set(mapping.duplicates) - {'completed'}:
            raise NoPlan()
        if any(not isinstance(ss, list) or not 1 <= len(ss) <= 12 for ss in mapping.values()):
            raise NoPlan()
        # Broken span values can be replaced, but extra keys/duplicate structural
        # keys cannot be hidden by a source repair.
        for ss in mapping.values():
            for s in ss:
                if not isinstance(s, dict) or s.duplicates or set(s)-{'turn','text','status','occurrence'}:
                    raise NoPlan()
    return op['kind']


def clean_title(text, *, explicit=False):
    if not re.fullmatch(r'[가-힣A-Za-z0-9][가-힣A-Za-z0-9 _-]{1,100}', text) or TITLE_BAD.search(text):
        raise NoPlan()
    if not explicit and (re.search(DATE, text) or re.search(TIME, text) or re.match(STATE, text)):
        raise NoPlan()
    # A residual grammatical clause is not a title. Conservative fallback is
    # preferable to swallowing a category condition, conjunction or request.
    if re.search(r'일정|분류|카테고리|(?:으로|이라는|라는|으니|니까|하고|이름)(?:\s|$)|\d+분|(?:의|는|을|를)$',text):
        raise NoPlan()
    if not explicit and text.endswith(' 기록'):
        raise NoPlan()  # Could be a title suffix or a request for an existing record.
    if text in ('그거','그것','그 일정','물류센터','하나','일정'):
        raise NoPlan()
    return text


def source_plan(turns, trusted):
    if list(turns) not in ([1], [1,2], [1,2,3]) or any(not isinstance(t, str) or len(t)>2000 or OUTSIDE.search(t) for t in turns.values()):
        raise NoPlan()
    if any(UNCERTAIN.search(t) for t in turns.values()):
        raise NoPlan()
    if MARK.search(turns[1]):
        raise NoPlan()  # Same-turn old vs proposed values need stronger context.
    op = {'index':1,'kind':None,'targetEvidence':{},'changeEvidence':{},'filterEvidence':{},'unsupportedEvidence':[],'missingInformation':[]}
    pending = None

    def sp(text, n, occurrence=None):
        offsets=[m.start() for m in re.finditer(re.escape(text),turns[n])]
        if not offsets or len(offsets)>1 and occurrence is None:
            raise NoPlan()
        s={'turn':n,'text':text,'status':'adopted'}
        if len(offsets)>1:s['occurrence']=occurrence
        return [s]

    def put(mapping, slot, text, n, occurrence=None):
        if slot in mapping:raise NoPlan()
        mapping[slot]=sp(text,n,occurrence)

    def head(text, n, role, title_occurrence=None):
        """Parse only explicit leading conditions; remaining text is one title."""
        d={};rest=text.strip()
        patterns=(('futureScope',r'(앞으로 남은|지금 이후)(?:에)?\s+'),
                  ('completed' if role=='filterEvidence' else 'completedState',rf'({STATE})\s+'),
                  ('category',rf'({CATEGORY})\s+'),('date',rf'({DATE})(?:의)?\s+'),
                  ('time',rf'({TIME})(?:에 있는|에는|에)?\s+'))
        while True:
            found=False
            for slot,pattern in patterns:
                m=re.match(pattern,rest)
                if m:
                    if slot=='futureScope' and role!='filterEvidence':raise NoPlan()
                    put(d,slot,m[1],n);rest=rest[m.end():];found=True;break
            if not found:break
        if rest not in ('', '물류센터', '일정', '물류센터 일정'):
            put(d,'title',clean_title(rest),n,title_occurrence)
        return d

    first=turns[1]
    # Complete/undo completion: target conditions remain distinct from new state.
    m=re.fullmatch(rf'\s*(?P<head>.+?)(?:의|을|를)?\s+(?P<cue>{COMPLETE}){END}',first)
    if m:
        op['kind']='update';op['targetEvidence']=head(m['head'],1,'targetEvidence')
        put(op['changeEvidence'],'completed',m['cue'],1)
    if op['kind'] is None:
        m=re.fullmatch(rf'\s*(?P<head>.+?)(?:(?:이라는|라는) 제목의 일정|\s*일정)?(?:을|를)?\s+{DELETE}{END}',first)
        if m:op['kind']='delete';op['targetEvidence']=head(m['head'],1,'targetEvidence')
    if op['kind'] is None:
        m=re.fullmatch(rf'\s*(?P<head>.+?)(?:\s*일정)?(?:만|을|를)?\s+{QUERY}{END}',first)
        if m:op['kind']='list';op['filterEvidence']=head(m['head'],1,'filterEvidence')
    if op['kind'] is None:
        m=re.fullmatch(rf'\s*(?P<head>.+?)\s+(?P<slot>날짜|시간|시각|분류|제목)(?:을|를) (?P<value>.+?)(?:으로|로) {CHANGE}{END}',first)
        if m:
            slot={'날짜':'date','시간':'time','시각':'time','분류':'category','제목':'title'}[m['slot']]
            val=m['value']
            if slot=='title':clean_title(val,explicit=True)
            elif not re.fullmatch({'date':DATE,'time':TIME,'category':'|'.join(CUES)}[slot],val):raise NoPlan()
            op['kind']='update';op['targetEvidence']=head(m['head'],1,'targetEvidence',1 if slot=='title' else None)
            put(op['changeEvidence'],slot,val,1)
    if op['kind'] is None:
        m=re.fullmatch(rf'\s*(?P<head>.+?)(?:을|를)? (?P<date>{DATE})(?: (?P<time>{TIME}))?(?:으로|로) {CHANGE}{END}',first)
        if m:
            op['kind']='update';op['targetEvidence']=head(m['head'],1,'targetEvidence')
            put(op['changeEvidence'],'date',m['date'],1)
            if m['time']:put(op['changeEvidence'],'time',m['time'],1)
    if op['kind'] is None:
        m=re.fullmatch(rf'\s*(?P<head>.+?)(?:\s+(?P<slot>날짜|시간|시각|분류|제목|상태)|\s+일정)?(?:을|를)? (?:수정할게|바꿀게|수정해\s*(?:줘|주세요)|바꿔\s*(?:줘|주세요)){END}',first)
        if m:
            op['kind']='update';op['targetEvidence']=head(m['head'],1,'targetEvidence')
            pending={'날짜':'date','시간':'time','시각':'time','분류':'category','제목':'title','상태':'completed',None:None}[m['slot']]
    if op['kind'] is None:
        m=re.fullmatch(rf'\s*(?P<head>.+?)(?:\s*일정)?(?:을|를)? (?:등록해|잡아|넣어|추가해)\s*(?:줘|주세요){END}',first)
        if m:
            op['kind']='create';op['changeEvidence']=head(m['head'],1,'changeEvidence')
            if set(op['changeEvidence'])-{'title','date','time','category'}:raise NoPlan()
    if op['kind'] is None:raise NoPlan()
    role='changeEvidence' if op['kind']=='create' else 'filterEvidence' if op['kind']=='list' else 'targetEvidence'
    for n in range(2,len(turns)+1):
        text=turns[n]
        # A single explicit later-turn correction has an existing value and an
        # independently typed replacement; never guess between two slots.
        if MARK.search(text):
            if sum(len(MARK.findall(t)) for t in turns.values())!=1:raise ClarificationRequired('multiple_corrections_need_restatement')
            m=re.fullmatch(rf'\s*(?P<mark>아니|아니다)[,，]?\s+(?P<val>.+?)(?:으로|로) 해\s*(?:줘|주세요){END}',text)
            slot=None;dest=role;val=None
            if m:
                val=m['val']
                if re.fullmatch(DATE,val):slot='date'
                elif re.fullmatch(TIME,val):slot='time'
                else:slot='title';clean_title(val,explicit=True)
                if op['kind']=='update':dest='changeEvidence'
            if not m:
                m=re.fullmatch(rf'\s*(?P<mark>아니|아니다)[,，]?\s+(?P<val>{DATE}) 일정(?:이야|으로){END}',text)
                if m:slot='date';val=m['val'];dest=role
            if not m and op['kind'] in ('list','delete','update'):
                ending=rf'(?:으로|로) {QUERY}' if op['kind']=='list' else r'(?:을|를) 말하는 거야'
                m=re.fullmatch(rf'\s*(?P<mark>아니|아니다)[,，]?\s+(?P<val>.+?){ending}{END}',text)
                if m:slot='title';val=clean_title(m['val'],explicit=True);dest=role
            if not m and op['kind']=='update':
                m=re.fullmatch(rf'\s*(?P<mark>아니|아니다)[,，]?\s+(?P<val>{COMPLETE}){END}',text)
                if m:slot='completed';val=m['val'];dest='changeEvidence'
            if not m or slot not in op[dest] or len(op[dest][slot])!=1:raise NoPlan()
            old=op[dest][slot][0]
            if old['text']==val:raise NoPlan()
            op[dest][slot]=history(old['text'],val,m['mark'],old['turn'],n,n)
            continue
        # Explicit title supplement or rename; role comes from the first request.
        m=re.fullmatch(rf'\s*(?:대상 제목|물류센터 일정 제목|제목|이름)(?:은|는) (?P<val>.+?)(?:이야|야|이고){END}',text)
        if m:put(op[role],'title',clean_title(m['val'],explicit=True),n);continue
        if op['kind']=='update' and pending=='title':
            m=re.fullmatch(rf'\s*이름은 (?P<val>.+?)(?:으로|로) 해\s*(?:줘|주세요){END}',text)
            if m:put(op['changeEvidence'],'title',clean_title(m['val'],explicit=True),n);continue
        m=re.fullmatch(rf'\s*(?:날짜|대상)(?:은|는) (?P<val>{DATE})(?:이야| 일정이야){END}',text)
        if m:put(op[role],'date',m['val'],n);continue
        m=re.fullmatch(rf'\s*(?:시간|시각)(?:은|는) (?P<val>{TIME})이야{END}',text)
        if m:put(op[role],'time',m['val'],n);continue
        m=re.fullmatch(rf'\s*(?P<date>{DATE})(?: (?P<time>{TIME}))?(?: 일정으로| 일정을 말하는 거야| 건이야|에 있는 거야|에 있는 거야|에 있는 건이야){END}',text)
        if m:
            put(op[role],'date',m['date'],n)
            if m['time']:put(op[role],'time',m['time'],n)
            continue
        m=re.fullmatch(rf'\s*(?P<val>{TIME})(?: 일정으로|에 있는 거야){END}',text)
        if m:put(op[role],'time',m['val'],n);continue
        m=re.fullmatch(rf'\s*(?P<val>{STATE})(?: 것만 찾아\s*(?:줘|주세요)| 일정이 필요해| 일정이 대상이야| 것만 대상으로 해\s*(?:줘|주세요)){END}',text)
        if m and role!='changeEvidence':put(op[role],'completed' if role=='filterEvidence' else 'completedState',m['val'],n);continue
        m=re.fullmatch(rf'\s*(?P<val>{CATEGORY})(?: 것으로| 걸 말하는 거야| 것을 말하는 거야){END}',text)
        if m and role!='changeEvidence':put(op[role],'category',m['val'],n);continue
        m=re.fullmatch(rf'\s*(?P<val>지금 이후|앞으로 남은)에 있는 것만 {QUERY}{END}',text)
        if m and role=='filterEvidence':put(op[role],'futureScope',m['val'],n);continue
        if op['kind']=='update':
            m=re.fullmatch(rf'\s*(?:(?P<label>날짜|시간|시각|분류)(?:는|은|를|을) )?(?P<val>.+?)(?:으로|로) (?:{CHANGE}|바꾸고|해\s*(?:줘|주세요)){END}',text)
            if m:
                slot={'날짜':'date','시간':'time','시각':'time','분류':'category'}.get(m['label'],pending)
                if slot in ('date','time','category') and re.fullmatch({'date':DATE,'time':TIME,'category':'|'.join(CUES)}[slot],m['val']):
                    put(op['changeEvidence'],slot,m['val'],n);continue
            m=re.fullmatch(rf'\s*(?P<val>{COMPLETE}){END}',text)
            if m:put(op['changeEvidence'],'completed',m['val'],n);continue
        raise NoPlan()
    # Do not emit bare unidentified update/delete operations.
    if op['kind'] in ('update','delete') and 'title' not in op['targetEvidence']:raise NoPlan()
    if op['kind']=='create':
        c=op['changeEvidence']
        if 'title' not in c:raise NoPlan()
        if len(c['title'])>1:raise NoPlan() # v10 owns category history for title corrections.
        if 'category' not in c:
            title=c['title'][0];cues=[cue for cue in CUES if cue in title['text']]
            if len(cues)>1:raise NoPlan()
            if cues:c['category']=sp(cues[0],title['turn'])
        for slot in ('date','time'):
            if slot not in c:op['missingInformation'].append(slot)
        if 'time' in c:
            time=c['time'][0]['text']
            hour=re.search(r'\d+',time)
            if hour and 1<=int(hour[0])<=12 and not re.search('오전|오후',time):op['missingInformation'].append('ampm')
        if not trusted and 'date' in c and not re.match(r'\d{4}년',c['date'][0]['text']):
            op['missingInformation'].append('absolute_datetime_due_to_untrusted_time')
    if op['kind']=='update' and not op['changeEvidence']:op['missingInformation']=['change_value']
    return {'operations':[op],'unsupportedEvidence':[],'missingInformation':list(op['missingInformation'])}


def apply_rules(raw, *, user_turns, reference, server_time_trusted):
    markers=[m for t in user_turns.values() for m in MARK.finditer(t)]
    if markers and any(UNCERTAIN.search(t) for t in user_turns.values()):
        raise ClarificationRequired('ambiguous_correction_source')
    try:
        kind=model_envelope(raw)
        value=source_plan(user_turns,server_time_trusted)
        if kind!=value['operations'][0]['kind']:
            raise NoPlan() # Never switch operation kind from a guessed model output.
        proposed=dump(value);_validate(proposed,user_turns,reference)
        return Result(proposed,proposed!=raw,('full_source_role_reconstruction',) if proposed!=raw else ())
    except ClarificationRequired:
        raise
    except (ValueError,TypeError,KeyError,IndexError,AttributeError,UnicodeError,RecursionError):
        pass
    previous=apply_v10(raw,user_turns=user_turns,reference=reference,server_time_trusted=server_time_trusted)
    # v10's explicit correction grammar can leave a repeated old title without
    # an occurrence. Add it only if exactly one occurrence precedes its marker;
    # do not choose among genuinely ambiguous repeats.
    current=previous.raw;reasons=list(previous.reasons)
    try:
        _validate(current,user_turns,reference)
        value=json.loads(current);changed=False
        for op in value['operations']:
            for role in ROLES:
                for spans in op[role].values():
                    markers=[s for s in spans if s['status']=='correction_marker']
                    for s in spans:
                        if s['status']!='superseded' or 'occurrence' in s:continue
                        text=user_turns[s['turn']]
                        positions=[m.start() for m in re.finditer(re.escape(s['text']),text)]
                        if len(positions)<2:continue
                        ms=[m.start() for cue in markers if cue['turn']==s['turn'] for m in re.finditer(re.escape(cue['text']),text)]
                        if len(ms)!=1:continue
                        eligible=[i+1 for i,p in enumerate(positions) if p+len(s['text'])<=ms[0]]
                        if len(eligible)==1:s['occurrence']=eligible[0];changed=True
        if changed:
            proposed=dump(value);_validate(proposed,user_turns,reference)
            current=proposed;reasons.append('unique_superseded_occurrence_before_marker')
    except (ValueError,TypeError,KeyError,IndexError,AttributeError):pass
    return Result(current,current!=raw,tuple(reasons))
