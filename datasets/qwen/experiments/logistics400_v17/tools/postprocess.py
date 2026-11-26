"""Apply v17 to a saved input/raw-output pair. No model or API calls."""
import sys,json,argparse
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from common import ROOT,datetime,parse_evidence_output,check_roles,check_role_span_overlaps
sys.path.insert(0,str(ROOT/'runtime/rules'))
from evidence_postprocess_v17 import apply_rules,ClarificationRequired

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--input',required=True);ap.add_argument('--raw',required=True);args=ap.parse_args()
    u=json.loads(Path(args.input).read_text());raw=Path(args.raw).read_text()
    turns={t['turn']:t['text'] for t in u['userTurns']};ref=datetime.fromisoformat(u['taskReferenceTime'])
    try:
        out=apply_rules(raw,user_turns=turns,reference=ref,server_time_trusted=u['serverTimeTrusted'])
    except ClarificationRequired as exc:
        print(json.dumps(dict(status='clarification_required',reason=exc.reason,question=getattr(exc,'question',None)),ensure_ascii=False,indent=2));return
    try:
        parsed=parse_evidence_output(out.raw,user_turns=turns)
        check_roles(parsed,reference=ref);check_role_span_overlaps(parsed,user_turns=turns)
    except (ValueError,KeyError,TypeError) as exc:
        print(json.dumps(dict(status='invalid_output',error=str(exc)),ensure_ascii=False,indent=2));sys.exit(2)
    print(json.dumps(dict(status='contract_valid_evidence',evidence=json.loads(out.raw),reasons=out.reasons,executionPerformed=False),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
