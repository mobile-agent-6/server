"""Optional backend integration for v37. No backend files or public schemas changed.

Call make_provider(inner, reader) to wrap both extraction and decision handling.
An unresolved correction asks for a complete restatement and clears stale task
evidence; a bare 'yes' cannot authorize the discarded operation.
"""
from .evidence_postprocess_v37 import apply_rules, ClarificationRequired


class PostprocessedEvidenceModel:
    def __init__(self, inner):
        self.inner = inner

    def is_ready(self):
        return self.inner.is_ready()

    @property
    def name(self):
        return getattr(self.inner, 'name', 'qwen440') + '+v37'

    @property
    def prompt_sha256(self):
        return self.inner.prompt_sha256

    def extract(self, *, turns, reference_time, server_time_trusted, timeout_seconds=None):
        raw = self.inner.extract(turns=turns, reference_time=reference_time,
                                 server_time_trusted=server_time_trusted, timeout_seconds=timeout_seconds)
        return apply_rules(raw, user_turns=dict(enumerate(turns, 1)),
                           reference=reference_time, server_time_trusted=server_time_trusted).raw


def make_provider(model, schedule_reader):
    # Import lazily so CPU postprocessing tools need only the contract snapshot.
    from app.modules.assistant.evidence.provider import EvidenceDecisionProvider, _State, _envelope

    class CorrectionDecisionProvider(EvidenceDecisionProvider):
        def decide(self, context):
            try:
                return super().decide(context)
            except ClarificationRequired as exc:
                # No planner/reader call has occurred: extraction precedes planning.
                # Reset pending confirmations and obsolete evidence. A new full
                # command is required, using the existing public ask response.
                return _envelope({'kind': 'ask', 'text': exc.question}, _State())

    return CorrectionDecisionProvider(PostprocessedEvidenceModel(model), schedule_reader)
