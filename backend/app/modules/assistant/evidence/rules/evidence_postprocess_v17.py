"""Recover tightly anchored source-copy errors; never invent replacement text.

The prior rules run first. Valid outputs are left alone. A corrupted Hangul
word may be replaced only inside a unique source span whose other words match
exactly, with one syllable edit and a trailing source anchor. Numeric identifiers,
cross-turn guesses, multiple matches, corrections and broad rewrites abstain.
"""
import copy,json,re
from dataclasses import dataclass
from .evidence_postprocess_v16 import (
    apply_rules as apply_v16,source_plan,model_envelope,NoPlan,
    ClarificationRequired,absorbed_condition,
)
from .evidence_postprocess_v15 import clean_title,UNCERTAIN,MARK,CHANGE,END
from .evidence_postprocess_v3 import TIME,dump
from .category_postprocess import _validate,_strict_pairs,_reject_constant,CUES

VERSION='evidence-rules-v17'
ROLES=('targetEvidence','changeEvidence','filterEvidence')
WORD=re.compile(r'\S+')
BOUNDARY=r'(?=(?:은|는|이|가|을|를|의|이라는|라는|으로|로)?(?:\s|[,.!?。]|$))'

@dataclass(frozen=True)
class Result:
    raw:str
    changed:bool
    reasons:tuple[str,...]
    version:str=VERSION

def ask(reason):
    exc=ClarificationRequired(reason)
    exc.question='일정 제목이나 시간을 원문과 정확히 연결하지 못했어요. 최종 일정 제목과 날짜·시간, 원하는 작업을 한 문장으로 다시 말씀해 주세요.'
    raise exc

def one_edit(a,b):
    if a==b or abs(len(a)-len(b))>1:return False
    if len(a)==len(b):return sum(x!=y for x,y in zip(a,b))==1
    short,long=(a,b) if len(a)<len(b) else (b,a)
    return any(long[:i]+long[i+1:]==short for i in range(len(long)))

def title_candidates(broken,source):
    words=list(WORD.finditer(broken));candidates=set()
    if len(words)<3 or len(broken)>100:return candidates
    # A following anchor is required; a corrupt final noun cannot safely be
    # separated from a Korean particle using edit distance alone.
    for bad in words[:-1]:
        if not re.fullmatch(r'[가-힣]{2,}',bad[0]):continue
        anchors=broken[:bad.start()]+broken[bad.end():]
        if len(re.findall(r'[가-힣]',anchors))<6:continue
        pattern=r'(?<![가-힣A-Za-z0-9_-])'+re.escape(broken[:bad.start()])+r'(?P<word>[가-힣]+)'+re.escape(broken[bad.end():])+BOUNDARY
        for m in re.finditer(pattern,source):
            if not one_edit(bad[0],m['word']):continue
            candidate=m[0]
            try:clean_title(candidate)
            except NoPlan:continue
            # Changing a category cue would need a separate semantic repair.
            if {c for c in CUES if c in broken}!={c for c in CUES if c in candidate}:continue
            candidates.add(candidate)
    return candidates

def invalid_copy_spans(value,turns):
    result=[]
    for op in value['operations']:
        for role in ROLES:
            for slot,spans in op[role].items():
                for s in spans:
                    if (type(s.get('turn')) is int and s['turn'] in turns and
                        isinstance(s.get('text'),str) and s['text'] and s['text'] not in turns[s['turn']]):
                        result.append((role,slot,s))
    return result

def repair_unique_time(op,span,source):
    if op['kind']!='update' or set(op['changeEvidence'])!={'time'}:return False
    if len(op['changeEvidence']['time'])!=1 or span['status']!='adopted':return False
    titles=op['targetEvidence'].get('title',[])
    if len(titles)!=1 or titles[0]['status']!='adopted' or titles[0]['turn']!=span['turn']:return False
    # Whole request, explicit source title and change label, one clock only.
    title=titles[0]['text']
    pattern=rf'\s*{re.escape(title)}(?:의)? (?:시작 )?(?:시간|시각)(?:만|을|를) (?P<time>{TIME})(?:으로|로) {CHANGE}{END}'
    m=re.fullmatch(pattern,source)
    if not m or len(list(re.finditer(TIME,source)))!=1:return False
    if source.count(m['time'])!=1 or span.get('occurrence',1)!=1:return False
    span['text']=m['time'];return True

def repair_unknown_time(op,value,turns):
    if op['kind']!='create':return False
    c=op['changeEvidence'];ss=c.get('time',[])
    if len(ss)!=1 or ss[0].get('status')!='adopted':return False
    span=ss[0]
    if span.get('text') not in ('아직 몰라','아직 못 정했어'):return False
    text=turns.get(span.get('turn'),'')
    if not re.fullmatch(r'.*?(?:몇 시에 시작할지는|몇 시에 시작할지|시각은|시간은) 아직 (?:몰라|못 정했어)[.!。]?\s*',text):return False
    if any(re.search(TIME,t) for t in turns.values()):return False
    if 'title' not in c or 'date' not in c:return False
    del c['time']
    for owner in (op,value):
        missing=[s for s in owner['missingInformation'] if s!='ampm']
        if 'time' not in missing:missing.append('time')
        owner['missingInformation']=missing
    return True

def apply_rules(raw, *, user_turns, reference, server_time_trusted):
    kw=dict(user_turns=user_turns,reference=reference,server_time_trusted=server_time_trusted)
    previous=apply_v16(raw,**kw)
    try:
        _validate(previous.raw,user_turns,reference)
        return Result(previous.raw,previous.raw!=raw,tuple(previous.reasons))
    except (ValueError,TypeError,KeyError,IndexError,AttributeError):pass
    try:
        model_envelope(previous.raw)
        value=json.loads(previous.raw,object_pairs_hook=_strict_pairs,parse_constant=_reject_constant)
        op=value['operations'][0]
        if any(MARK.search(t) or UNCERTAIN.search(t) for t in user_turns.values()):
            if invalid_copy_spans(value,user_turns):ask('ambiguous_source_copy')
            return Result(previous.raw,previous.raw!=raw,tuple(previous.reasons))
        proposed=copy.deepcopy(value);op=proposed['operations'][0];reasons=[]
        for role,slot,span in invalid_copy_spans(proposed,user_turns):
            if span.get('status')!='adopted':ask('unresolved_source_copy')
            source=user_turns[span['turn']]
            if slot=='title':
                if len(op[role][slot])!=1:ask('multiple_title_evidence')
                candidates=title_candidates(span['text'],source)
                if len(candidates)!=1:ask('non_unique_source_title')
                candidate=next(iter(candidates))
                if source.count(candidate)!=1 or span.get('occurrence',1)!=1:ask('repeated_source_title')
                span['text']=candidate
                reasons.append('unique_anchored_source_title_copy')
            elif slot=='time' and role=='changeEvidence' and repair_unique_time(op,span,source):
                reasons.append('explicit_source_change_time_copy')
            else:ask('unresolved_source_copy')
        if repair_unknown_time(op,proposed,user_turns):reasons.append('unknown_start_time_is_missing')
        if not reasons:return Result(previous.raw,previous.raw!=raw,tuple(previous.reasons))
        fixed=dump(proposed);_validate(fixed,user_turns,reference)
        # Stabilize with the existing rules, and validate again before returning.
        final=apply_v16(fixed,**kw);_validate(final.raw,user_turns,reference)
        return Result(final.raw,final.raw!=raw,tuple(previous.reasons)+tuple(reasons)+tuple(final.reasons))
    except ClarificationRequired:raise
    except (ValueError,TypeError,KeyError,IndexError,AttributeError,UnicodeError,RecursionError):
        return Result(previous.raw,previous.raw!=raw,tuple(previous.reasons))
