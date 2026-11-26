"""Candidate: recover invalid evidence only from fully consumed narrow grammar.

No corpus, case identifiers, specific schedule titles, model inference or DB calls.
All outputs remain evidence; this module never grants approval or executes tasks.
Valid previous outputs are preserved byte-for-byte. Unknown grammar falls back.
"""
import re
from dataclasses import dataclass
from .evidence_postprocess_v23_candidate import apply_rules as previous
from .evidence_postprocess_v15 import NoPlan,ClarificationRequired,clean_title,OUTSIDE,UNCERTAIN,MARK,STATE,QUERY,DELETE,COMPLETE,END
from .evidence_postprocess_v10 import history
from .evidence_postprocess_v3 import DATE,dump
from .category_postprocess import CUES,_validate,MAX_PAYLOAD_BYTES
VERSION='evidence-rules-v24-candidate'
# Limit possessive-boundary recovery to full task nouns. This excludes ambiguous
# final 의 in nouns such as 협의/회의 instead of blindly removing a syllable.
TASK_ENDINGS={'점검','확인','검수','청소','교체','세척','대조','정리','소독'}
RECOVERABLE_QUESTIONS={'invalid_or_unresolved_correction','non_unique_source_title'}

@dataclass(frozen=True)
class Result:
    raw:str
    changed:bool
    reasons:tuple[str,...]
    version:str=VERSION

def narrow_source_plan(turns,trusted):
    if not trusted or list(turns) not in ([1],[1,2]):raise NoPlan()
    if any(not isinstance(t,str) or len(t)>2000 or OUTSIDE.search(t) or UNCERTAIN.search(t) for t in turns.values()):raise NoPlan()
    if MARK.search(turns[1]) or sum(len(MARK.findall(t)) for t in turns.values())>1:raise NoPlan()
    op={'index':1,'kind':None,'targetEvidence':{},'changeEvidence':{},'filterEvidence':{},'unsupportedEvidence':[],'missingInformation':[]}
    def span(text,n,status='adopted'):
        if not text or len(list(re.finditer(re.escape(text),turns[n])))!=1:raise NoPlan()
        return {'turn':n,'text':text,'status':status}
    def put(role,slot,text,n,title=False):
        if title:clean_title(text,explicit=False)
        op[role][slot]=[span(text,n)]
    first=turns[1]
    if len(turns)==1:
        m=re.fullmatch(rf'\s*(?P<state>{STATE}) (?P<title>.+?) 일정만 {QUERY}{END}',first)
        if m:
            op['kind']='list';put('filterEvidence','title',m['title'],1,True);put('filterEvidence','completed',m['state'],1)
        else:
            m=re.fullmatch(rf'\s*삭제할 일정은 (?P<title>.+?)(?:이야|야)[.] 목록에서 {DELETE}{END}',first)
            if m:
                op['kind']='delete';put('targetEvidence','title',m['title'],1,True)
            else:
                m=re.fullmatch(rf'\s*(?P<date>{DATE}) (?P<title>.+?) 일정은 (?P<cue>{COMPLETE}){END}',first)
                if not m:raise NoPlan()
                op['kind']='update';put('targetEvidence','title',m['title'],1,True);put('targetEvidence','date',m['date'],1);put('changeEvidence','completed',m['cue'],1)
    else:
        second=turns[2]
        m=re.fullmatch(rf'\s*(?P<title>.+?) 일정을 수정해\s*(?:줘|주세요){END}',first)
        n=re.fullmatch(rf'\s*바꿀 것은 분류야[.] (?P<category>{"|".join(map(re.escape,CUES))})(?:으로|로) 설정해\s*(?:줘|주세요){END}',second)
        if m and n:
            op['kind']='update';put('targetEvidence','title',m['title'],1,True);put('changeEvidence','category',n['category'],2)
        else:
            m=re.fullmatch(rf'\s*(?P<title>.+?)의 제목을 바꾸고 싶어{END}',first)
            n=re.fullmatch(rf'\s*새 제목으로 (?P<title>.+?)(?:을|를) 써\s*(?:줘|주세요){END}',second)
            if m and n:
                if m['title'].split()[-1] not in TASK_ENDINGS:raise NoPlan()
                if m['title']==n['title']:raise NoPlan()
                op['kind']='update';put('targetEvidence','title',m['title'],1,True);put('changeEvidence','title',n['title'],2,True)
            else:
                m=re.fullmatch(rf'\s*(?P<date>{DATE}) (?P<title>.+?) 일정을 {QUERY}{END}',first)
                n=re.fullmatch(rf'\s*찾을 날짜를 정정할게[.] (?P<old>{DATE}) (?P<mark>말고|대신) (?P<new>{DATE}) 걸 {QUERY}{END}',second)
                if not m or not n or m['date']!=n['old'] or n['old']==n['new']:raise NoPlan()
                op['kind']='list';put('filterEvidence','title',m['title'],1,True)
                for key in ('old','mark','new'):span(n[key],2)
                # Existing v10/v15 history serialization, with repeated old text
                # sourced from the correction turn; no gold-dependent reordering.
                op['filterEvidence']['date']=history(n['old'],n['new'],n['mark'],2,2,2)
    if op['kind'] is None:raise NoPlan()
    return {'operations':[op],'unsupportedEvidence':[],'missingInformation':[]}

def apply_rules(raw,*,user_turns,reference,server_time_trusted):
    kw=dict(user_turns=user_turns,reference=reference,server_time_trusted=server_time_trusted)
    prior=None;question=None
    try:
        prior=previous(raw,**kw)
    except ClarificationRequired as exc:
        if exc.reason not in RECOVERABLE_QUESTIONS:raise
        question=exc
    if prior is not None:
        try:
            _validate(prior.raw,user_turns,reference)
        except (ValueError,KeyError,TypeError,IndexError,AttributeError,RecursionError):pass
        else:return Result(prior.raw,prior.raw!=raw,tuple(prior.reasons))
    try:
        if not isinstance(raw,str) or len(raw.encode())>MAX_PAYLOAD_BYTES:raise NoPlan()
        plan=narrow_source_plan(user_turns,server_time_trusted)
        proposed=dump(plan);_validate(proposed,user_turns,reference)
        return Result(proposed,proposed!=raw,('narrow_full_source_invalid_output_recovery',))
    except (ValueError,KeyError,TypeError,IndexError,AttributeError,RecursionError):
        if question is not None:raise question
        return Result(prior.raw,prior.raw!=raw,tuple(prior.reasons))
