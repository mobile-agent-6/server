"""Deterministic validator for Candidate-C create datasets (30-case pilot, batches)."""

from __future__ import annotations

import hashlib
import inspect
import json
import re
import sys
import unicodedata
from copy import deepcopy
from dataclasses import dataclass
from collections import Counter
from datetime import date, datetime
from itertools import combinations
from pathlib import Path
from typing import Any, Mapping

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ISSUE30 = HERE.parent / "structured_output_contract_v0.1"
OLD_DATA_ROOT = HERE.parent / "multiturn_400_v0.1"
if str(ISSUE30) not in sys.path:
    sys.path.insert(0, str(ISSUE30))
if str(OLD_DATA_ROOT) not in sys.path:
    sys.path.insert(0, str(OLD_DATA_ROOT))

import common_gate_v0_3 as gate_v3  # noqa: E402
import common_gate_v0_4 as gate_v4  # noqa: E402
import contracts  # noqa: E402
import dry_mapping  # noqa: E402
import ollama_eval  # noqa: E402
import ollama_eval_v0_3  # noqa: E402

PILOT = HERE / "pilot" / "conversations-v0.1.jsonl"
TARGETS = HERE / "pilot" / "candidate-c-targets-v0.1.jsonl"
PREVIEW = HERE / "pilot" / "training-row-preview-v0.1.jsonl"
RESULTS = HERE / "pilot" / "validation-results-v0.1.json"
MUTATIONS = HERE / "pilot" / "mutation-fixtures-v0.1.jsonl"
MUTATION_RESULTS = HERE / "pilot" / "mutation-results-v0.1.jsonl"
BUNDLE = HERE / "manifests" / "contract-bundle-v0.1.json"
BASE_PROVENANCE = HERE / "manifests" / "base-provenance-v0.1.json"
PROFILE = HERE / "schema" / "create-profile-v0.1.json"
SOURCE_REGISTRY = HERE / "source-registry-v0.1.jsonl"
OLD400 = HERE.parent / "multiturn_400_v0.1" / "scenarios_all_400_UNREVIEWED.jsonl"
DEV12 = ISSUE30 / "development-cases-v0.1.jsonl"
LOCKED56 = ISSUE30 / "locked-cases-v0.1.jsonl"
SCHEMA = ISSUE30 / "semantic-oracle-schema-v0.1.json"
PROMPT = ISSUE30 / "candidate-c-prompt-v0.1.txt"

EXPECTED_SCHEMA_SHA256 = "cfe0ed947f6bd709e0e3ba60a8daf63fb1f201614cb11d90b823931b060a3b1d"
BUCKETS = {
    "direct_create",
    "missing_and_followup",
    "correction",
    "time_safety",
    "mixed_unsupported_consent",
}
ALLOWED_META = {
    "schemaVersion", "caseId", "variantOf", "variantAxis", "status", "reviewState",
    "datasetRole", "scenarioTags", "contamination", "authoredOn", "notes",
}
ALLOWED_MISSING = {
    "task_or_title", "date", "time", "ampm",
    "absolute_datetime_due_to_untrusted_time", "user_consent",
}


@dataclass(frozen=True)
class DatasetConfig:
    """Where one dataset lives and what its lint expects."""

    name: str
    conversations: Path
    targets: Path
    preview: Path
    results: Path
    bucket_counts: Mapping[str, int]
    dataset_role: str
    run_mutations: bool
    extra_sources: tuple[tuple[str, Path], ...] = ()

    @property
    def expected_count(self) -> int:
        return sum(self.bucket_counts.values())


PILOT_CONFIG = DatasetConfig(
    name="pilot",
    conversations=PILOT,
    targets=TARGETS,
    preview=PREVIEW,
    results=RESULTS,
    bucket_counts={bucket: 6 for bucket in BUCKETS},
    dataset_role="development_probe",
    run_mutations=True,
)
BATCH1_DIR = HERE / "batch1"
BATCH1_CONFIG = DatasetConfig(
    name="batch1",
    conversations=BATCH1_DIR / "conversations-v0.1.jsonl",
    targets=BATCH1_DIR / "candidate-c-targets-v0.1.jsonl",
    preview=BATCH1_DIR / "training-row-preview-v0.1.jsonl",
    results=BATCH1_DIR / "validation-results-v0.1.json",
    bucket_counts={
        "direct_create": 22,
        "missing_and_followup": 25,
        "correction": 20,
        "time_safety": 20,
        "mixed_unsupported_consent": 13,
    },
    dataset_role="training_candidate",
    run_mutations=False,
    extra_sources=(("pilot30", PILOT),),
)
BATCH2_DIR = HERE / "batch2"
BATCH2_CONFIG = DatasetConfig(
    name="batch2",
    conversations=BATCH2_DIR / "conversations-v0.1.jsonl",
    targets=BATCH2_DIR / "candidate-c-targets-v0.1.jsonl",
    preview=BATCH2_DIR / "training-row-preview-v0.1.jsonl",
    results=BATCH2_DIR / "validation-results-v0.1.json",
    bucket_counts=BATCH1_CONFIG.bucket_counts,
    dataset_role="training_candidate",
    run_mutations=False,
    extra_sources=(("pilot30", PILOT), ("batch1", BATCH1_CONFIG.conversations)),
)
BATCH3_DIR = HERE / "batch3"
BATCH3_CONFIG = DatasetConfig(
    name="batch3",
    conversations=BATCH3_DIR / "conversations-v0.1.jsonl",
    targets=BATCH3_DIR / "candidate-c-targets-v0.1.jsonl",
    preview=BATCH3_DIR / "training-row-preview-v0.1.jsonl",
    results=BATCH3_DIR / "validation-results-v0.1.json",
    bucket_counts={
        "direct_create": 34,
        "missing_and_followup": 50,
        "correction": 34,
        "time_safety": 32,
        "mixed_unsupported_consent": 20,
    },
    dataset_role="training_candidate",
    run_mutations=False,
    extra_sources=(
        ("pilot30", PILOT),
        ("batch1", BATCH1_CONFIG.conversations),
        ("batch2", BATCH2_CONFIG.conversations),
    ),
)
TEST50_DIR = HERE / "test50"
TEST50_CONFIG = DatasetConfig(
    name="test50",
    conversations=TEST50_DIR / "conversations-v0.1.jsonl",
    targets=TEST50_DIR / "candidate-c-targets-v0.1.jsonl",
    preview=TEST50_DIR / "eval-row-preview-v0.1.jsonl",  # not training data
    results=TEST50_DIR / "validation-results-v0.1.json",
    bucket_counts={
        "direct_create": 10,
        "missing_and_followup": 13,
        "correction": 10,
        "time_safety": 10,
        "mixed_unsupported_consent": 7,
    },
    dataset_role="locked_comparison",
    run_mutations=False,
    extra_sources=(
        ("pilot30", PILOT),
        ("batch1", BATCH1_CONFIG.conversations),
        ("batch2", BATCH2_CONFIG.conversations),
        ("batch3", BATCH3_CONFIG.conversations),
    ),
)
BATCH6_DIR = HERE / "batch6"
BATCH6_CONFIG = DatasetConfig(
    name="batch6",
    conversations=BATCH6_DIR / "conversations-v0.1.jsonl",
    targets=BATCH6_DIR / "candidate-c-targets-v0.1.jsonl",
    preview=BATCH6_DIR / "training-row-preview-v0.1.jsonl",
    results=BATCH6_DIR / "validation-results-v0.1.json",
    bucket_counts={
        "direct_create": 0,
        "missing_and_followup": 0,
        "correction": 65,
        "time_safety": 0,
        "mixed_unsupported_consent": 0,
    },
    dataset_role="training_candidate",
    run_mutations=False,
    extra_sources=(
        ("pilot30", PILOT),
        ("batch1", BATCH1_CONFIG.conversations),
        ("batch2", BATCH2_CONFIG.conversations),
        ("batch3", BATCH3_CONFIG.conversations),
        ("test50", TEST50_CONFIG.conversations),
    ),
)
CONFIGS = {
    config.name: config
    for config in (PILOT_CONFIG, BATCH1_CONFIG, BATCH2_CONFIG, BATCH3_CONFIG, TEST50_CONFIG, BATCH6_CONFIG)
}


class CreateProfileError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            raise ValueError(f"blank JSONL line: {path}:{number}")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"non-object JSONL row: {path}:{number}")
        rows.append(value)
    return rows


def _json_equal(left: Any, right: Any) -> bool:
    """JSON equality without Python's bool/int aliasing."""

    return canonical(left) == canonical(right)


def _schema_type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return type(value) is int
    if expected == "number":
        return type(value) in {int, float}
    if expected == "boolean":
        return type(value) is bool
    if expected == "null":
        return value is None
    return False


def _resolve_ref(root: Mapping[str, Any], ref: str) -> Mapping[str, Any]:
    if not ref.startswith("#/"):
        raise ValueError(f"unsupported external schema ref: {ref}")
    value: Any = root
    for token in ref[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        value = value[token]
    if not isinstance(value, Mapping):
        raise ValueError(f"schema ref is not an object: {ref}")
    return value


def _schema_errors(
    value: Any,
    schema: Mapping[str, Any],
    root: Mapping[str, Any],
    path: str = "$",
) -> list[str]:
    """Validate the keyword subset used by semantic-oracle-schema-v0.1.

    This is an independent standard-library auxiliary check, not a replacement
    for an official JSON Schema implementation.
    """

    errors: list[str] = []
    if "$ref" in schema:
        errors.extend(_schema_errors(value, _resolve_ref(root, str(schema["$ref"])), root, path))
        schema = {key: item for key, item in schema.items() if key != "$ref"}

    expected_type = schema.get("type")
    if expected_type is not None:
        choices = [expected_type] if isinstance(expected_type, str) else list(expected_type)
        if not any(_schema_type_matches(value, choice) for choice in choices):
            return [f"{path}: type expected {choices}, got {type(value).__name__}"]

    if "const" in schema and not _json_equal(value, schema["const"]):
        errors.append(f"{path}: const mismatch")
    if "enum" in schema and not any(_json_equal(value, item) for item in schema["enum"]):
        errors.append(f"{path}: value not in enum")

    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                errors.append(f"{path}: missing required {key}")
        properties = schema.get("properties", {})
        for key, item in value.items():
            if key in properties:
                errors.extend(_schema_errors(item, properties[key], root, f"{path}.{key}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: additional property {key}")
            elif isinstance(schema.get("additionalProperties"), Mapping):
                errors.extend(_schema_errors(item, schema["additionalProperties"], root, f"{path}.{key}"))
        for key, dependent in schema.get("dependentRequired", {}).items():
            if key in value:
                for needed in dependent:
                    if needed not in value:
                        errors.append(f"{path}: {key} requires {needed}")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < int(schema["minItems"]):
            errors.append(f"{path}: fewer than minItems")
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            errors.append(f"{path}: more than maxItems")
        if schema.get("uniqueItems"):
            encoded = [canonical(item) for item in value]
            if len(encoded) != len(set(encoded)):
                errors.append(f"{path}: duplicate array items")
        if isinstance(schema.get("items"), Mapping):
            for index, item in enumerate(value):
                errors.extend(_schema_errors(item, schema["items"], root, f"{path}[{index}]"))
        if isinstance(schema.get("contains"), Mapping):
            if not any(not _schema_errors(item, schema["contains"], root, path) for item in value):
                errors.append(f"{path}: contains condition not met")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < int(schema["minLength"]):
            errors.append(f"{path}: shorter than minLength")
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            errors.append(f"{path}: longer than maxLength")
        if "pattern" in schema and re.search(str(schema["pattern"]), value) is None:
            errors.append(f"{path}: pattern mismatch")
        if schema.get("format") == "date":
            try:
                date.fromisoformat(value)
            except ValueError:
                errors.append(f"{path}: invalid date format")
        if schema.get("format") == "date-time":
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is None or parsed.utcoffset() is None:
                    raise ValueError
            except ValueError:
                errors.append(f"{path}: invalid date-time format")

    if type(value) in {int, float}:
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above maximum")

    for branch in schema.get("allOf", []):
        errors.extend(_schema_errors(value, branch, root, path))
    if "anyOf" in schema:
        if not any(not _schema_errors(value, branch, root, path) for branch in schema["anyOf"]):
            errors.append(f"{path}: no anyOf branch matched")
    if "not" in schema and not _schema_errors(value, schema["not"], root, path):
        errors.append(f"{path}: forbidden by not")
    if "if" in schema:
        condition = not _schema_errors(value, schema["if"], root, path)
        selected = schema.get("then") if condition else schema.get("else")
        if isinstance(selected, Mapping):
            errors.extend(_schema_errors(value, selected, root, path))
    return errors


def _notes(case: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in str(case["meta"].get("notes", "")).split(";"):
        if "=" in item:
            key, value = item.split("=", 1)
            result[key] = value
    return result


def _text_turns(case: Mapping[str, Any], through_turn: int) -> dict[int, str]:
    return {
        int(turn["turn"]): str(turn["text"])
        for turn in case["context"]["turns"]
        if turn["inputType"] == "text" and int(turn["turn"]) <= through_turn
    }


def load_target_map(path: Path = TARGETS) -> dict[tuple[str, int], dict[str, Any]]:
    rows = load_jsonl(path)
    result: dict[tuple[str, int], dict[str, Any]] = {}
    for row in rows:
        key = (str(row["caseId"]), int(row["turn"]))
        if key in result:
            raise ValueError(f"duplicate Candidate C target: {key}")
        if row.get("status") != "PROPOSED_UNREVIEWED":
            raise ValueError(f"target is not proposed/unreviewed: {key}")
        result[key] = row["target"]
    return result


def _candidate_span_rows(value: Mapping[str, Any], through_turn: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, status in (
        ("adoptedSpans", "adopted"),
        ("supersededSpans", "superseded"),
        ("correctionMarkerSpans", "correction_marker"),
    ):
        for span in value.get(key, []):
            if int(span["turn"]) <= through_turn:
                row = {"turn": span["turn"], "text": span["text"], "status": status}
                if "occurrence" in span:
                    row["occurrence"] = span["occurrence"]
                rows.append(row)
    return rows


def candidate_c_output(
    case: Mapping[str, Any],
    through_turn: int,
    targets: Mapping[tuple[str, int], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return the explicit layer-3 target at this model-call boundary."""

    values = load_target_map() if targets is None else targets
    key = (str(case["meta"]["caseId"]), through_turn)
    if key not in values:
        raise KeyError(f"missing Candidate C target: {key}")
    return deepcopy(dict(values[key]))


def _normalized_adopted_values(
    slot: str,
    spans: list[Mapping[str, Any]],
    *,
    reference: datetime,
) -> tuple[set[Any], bool]:
    texts = [str(span["text"]) for span in spans if span.get("status") == "adopted"]
    values: set[Any] = set()
    has_unresolved = False
    for text in texts:
        if slot == "title":
            values.add(_normalize_text(text))
        elif slot == "date":
            value, _interpretation = dry_mapping._date_from_texts([text], reference)
            if value is not None:
                values.add(value.isoformat())
            else:
                has_unresolved = True
        elif slot == "time":
            value, _ambiguous = dry_mapping._clock_from_texts([text])
            if value is not None:
                values.add(value)
            else:
                has_unresolved = True
        elif slot == "category":
            value = dry_mapping._category_from_texts([text])
            if value is not None:
                values.add(value)
            else:
                has_unresolved = True
        else:
            has_unresolved = True
    return values, has_unresolved


def validate_create_profile(
    parsed: Mapping[str, Any],
    *,
    reference_time: str,
) -> None:
    """Apply the pilot's restrictive create profile after Candidate C v0.1."""

    operations = parsed.get("operations", [])
    if len(operations) != 1 or operations[0].get("kind") != "create":
        raise CreateProfileError("C_PROFILE_CREATE_ONLY")
    operation = operations[0]
    if operation.get("targetEvidence") or operation.get("filterEvidence"):
        raise CreateProfileError("C_PROFILE_ROLE_NOT_ALLOWED")
    if parsed.get("unsupportedEvidence"):
        raise CreateProfileError("C_PROFILE_UNSUPPORTED_PLACEMENT")
    if set(operation.get("changeEvidence", {})) - {"title", "date", "time", "category"}:
        raise CreateProfileError("C_PROFILE_ROLE_NOT_ALLOWED")
    if set(operation.get("missingInformation", [])) - ALLOWED_MISSING:
        raise CreateProfileError("C_PROFILE_MISSING_NOT_ALLOWED")

    reference = dry_mapping._parse_datetime(reference_time)
    for slot, spans in operation.get("changeEvidence", {}).items():
        adopted = [span for span in spans if span.get("status") == "adopted"]
        if len(adopted) < 2:
            continue
        normalized, has_unresolved = _normalized_adopted_values(
            slot, adopted, reference=reference
        )
        if has_unresolved or len(normalized) > 1:
            raise CreateProfileError(
                "C_PROFILE_CONFLICTING_ADOPTED_VALUES",
                f"changeEvidence.{slot}",
            )


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip().casefold()


def _conversation_text(case: Mapping[str, Any]) -> str:
    return " || ".join(
        _normalize_text(str(turn["text"]))
        for turn in case["context"]["turns"]
        if turn["inputType"] == "text"
    )


def _old400_text(row: Mapping[str, Any]) -> str:
    return " || ".join(_normalize_text(str(turn["user"])) for turn in row["turns"])


def _trigrams(value: str) -> set[str]:
    compact = re.sub(r"\s+", "", value)
    if len(compact) < 3:
        return {compact} if compact else set()
    return {compact[index:index + 3] for index in range(len(compact) - 2)}


def _jaccard(left: str, right: str) -> float:
    a, b = _trigrams(left), _trigrams(right)
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b) if a | b else 0.0


def _expected_question_purpose(
    case: Mapping[str, Any],
    operation: Mapping[str, Any],
    expectation: Mapping[str, Any],
) -> str | None:
    result = expectation.get("expectedResult", {})
    if result.get("kind") != "need_more_info":
        return None
    through_turn = int(expectation["turn"])
    has_visible_unsupported = any(
        int(request["cue"]["adoptedSpans"][0]["turn"]) <= through_turn
        and int(operation["index"]) in request.get("relatedOperationIndexes", [])
        for request in case["semantics"].get("unsupportedRequests", [])
        if request.get("cue", {}).get("adoptedSpans")
    )
    consent_resolved = any(
        item["item"] == "user_consent"
        and item.get("resolvedAtTurn") is not None
        and int(item["resolvedAtTurn"]) <= through_turn
        for item in operation.get("missingInformation", [])
    )
    if has_visible_unsupported and not consent_resolved:
        return "consent_without_unsupported"
    active_missing = {
        item["item"]
        for item in operation.get("missingInformation", [])
        if int(item["detectedAtTurn"]) <= through_turn
        and (
            item.get("resolvedAtTurn") is None
            or through_turn < int(item["resolvedAtTurn"])
        )
    }
    if "task_or_title" in active_missing:
        return "task_or_title_unclear"
    if "absolute_datetime_due_to_untrusted_time" in active_missing:
        return "absolute_datetime_required"
    if "ampm" in active_missing:
        return "ampm"
    return "missing_information"


def _lint_semantic_subset(
    cases: list[dict[str, Any]], config: DatasetConfig = PILOT_CONFIG
) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    buckets: Counter[str] = Counter()
    for case in cases:
        meta = case.get("meta", {})
        case_id = str(meta.get("caseId"))
        if case_id in seen:
            errors.append(f"{case_id}: duplicate caseId")
        seen.add(case_id)
        if set(meta) - ALLOWED_META:
            errors.append(f"{case_id}: semantic meta extra fields {sorted(set(meta) - ALLOWED_META)}")
        if meta.get("schemaVersion") != "issue30-semantic-oracle-v0.1":
            errors.append(f"{case_id}: wrong schemaVersion")
        if meta.get("status") != "PROPOSED" or meta.get("reviewState") != "single-review proposed":
            errors.append(f"{case_id}: pilot must remain proposed and single-review")
        if meta.get("datasetRole") != config.dataset_role:
            errors.append(f"{case_id}: {config.name} must be {config.dataset_role}")
        if config.dataset_role == "locked_comparison":
            scoring = case.get("semantics", {}).get("scoring", {})
            if scoring.get("excludedFromModelScore") is not False or "exclusionReason" in scoring:
                errors.append(f"{case_id}: locked comparison rows must be scored")
            if _notes(case).get("split") != config.name:
                errors.append(f"{case_id}: notes split must be {config.name}")
        notes = _notes(case)
        bucket = notes.get("bucket", "")
        if bucket not in BUCKETS:
            errors.append(f"{case_id}: invalid bucket {bucket!r}")
        buckets[bucket] += 1
        if notes.get("state") != "PROPOSED_UNREVIEWED":
            errors.append(f"{case_id}: state is not PROPOSED_UNREVIEWED")
        context = case.get("context", {})
        turns = context.get("turns", [])
        if [turn.get("turn") for turn in turns] != list(range(1, len(turns) + 1)):
            errors.append(f"{case_id}: non-contiguous turns")
        user_turns = {int(turn["turn"]): _normalize_text(str(turn["text"])) for turn in turns}
        operations = case.get("semantics", {}).get("operations", [])
        if len(operations) != 1 or operations[0].get("kind") != "create":
            errors.append(f"{case_id}: pilot must have exactly one create operation")
            continue
        operation = operations[0]
        if operation["evidence"].get("target") or operation["evidence"].get("filter"):
            errors.append(f"{case_id}: create target/filter evidence must be empty")
        for role_value in operation["evidence"].values():
            for evidence_value in role_value.values():
                for span_key in ("adoptedSpans", "supersededSpans", "correctionMarkerSpans"):
                    for span in evidence_value.get(span_key, []):
                        if _normalize_text(str(span["text"])) not in user_turns.get(int(span["turn"]), ""):
                            errors.append(f"{case_id}: span not found in turn")
        missing_items = {item["item"] for item in operation.get("missingInformation", [])}
        if not missing_items <= ALLOWED_MISSING:
            errors.append(f"{case_id}: unsupported create missing items {sorted(missing_items - ALLOWED_MISSING)}")
        expectations = case["semantics"].get("expectedTurns", [])
        if len(expectations) != len(turns):
            errors.append(f"{case_id}: expectedTurns length mismatch")
        if any(int(item.get("expectedWriteCount", -1)) not in {0, 1} for item in expectations):
            errors.append(f"{case_id}: invalid write count")
        if any(int(item.get("expectedWriteCount", 0)) > 1 for item in expectations):
            errors.append(f"{case_id}: more than one write")
        for expectation in expectations:
            result = expectation.get("expectedResult", {})
            expected_purpose = _expected_question_purpose(case, operation, expectation)
            if expected_purpose is not None and result.get("questionPurpose") != expected_purpose:
                errors.append(
                    f"{case_id}/t{expectation['turn']}: questionPurpose "
                    f"{result.get('questionPurpose')!r} != {expected_purpose!r}"
                )
            facts = expectation.get("requiredResponseFacts", [])
            fact_names = {item.get("fact") for item in facts}
            if result.get("kind") == "failure" and facts:
                errors.append(
                    f"{case_id}/t{expectation['turn']}: failure result must not require question facts"
                )
            if result.get("kind") == "need_more_info":
                expected_fact = (
                    "unsupported_feature_notice"
                    if expected_purpose == "consent_without_unsupported"
                    else "missing_item_question"
                )
                if fact_names != {expected_fact}:
                    errors.append(
                        f"{case_id}/t{expectation['turn']}: requiredResponseFacts "
                        f"{sorted(str(item) for item in fact_names)} != [{expected_fact!r}]"
                    )
        refs = {ref["policyId"] for ref in case["semantics"].get("policyRefs", [])}
        if not set(operation["normalized"].get("ruleRefs", [])) <= refs:
            errors.append(f"{case_id}: normalized ruleRefs absent from policyRefs")
        gap_rows = case["semantics"].get("contractGaps", [])
        if gap_rows and case["semantics"]["scoring"].get("exclusionReason") != "CONTRACT_GAP":
            errors.append(f"{case_id}: contract gap is not excluded")
    if len(cases) != config.expected_count:
        errors.append(f"case count {len(cases)} != {config.expected_count}")
    for bucket in BUCKETS:
        if buckets[bucket] != config.bucket_counts[bucket]:
            errors.append(f"bucket {bucket}: {buckets[bucket]} != {config.bucket_counts[bucket]}")
    return errors


def _bundle_manifest() -> dict[str, Any]:
    artifacts = {
        "candidateCValidator": (ISSUE30 / "contracts.py", contracts.validate_candidate_c),
        "commonGateV04": (ISSUE30 / "common_gate_v0_4.py", gate_v4.common_backend_gate_v0_4),
        "createNormalizer": (ISSUE30 / "dry_mapping.py", dry_mapping._view_from_c_operation),
        "contextRenderer": (ISSUE30 / "ollama_eval.py", ollama_eval.render_extraction_context),
        "executableEffect": (ISSUE30 / "ollama_eval_v0_3.py", ollama_eval_v0_3.executable_effect),
        "createProfileValidator": (Path(__file__).resolve(), validate_create_profile),
    }
    values: dict[str, Any] = {}
    repo_root = HERE.parents[3]
    for name, (path, symbol) in artifacts.items():
        values[name] = {
            "path": str(path.relative_to(repo_root)),
            "fileSha256": sha256_file(path),
            "symbol": symbol.__name__,
            "symbolSha256": sha256_text(inspect.getsource(symbol)),
        }
    values["semanticOracleSchema"] = {
        "path": str(SCHEMA.relative_to(repo_root)),
        "fileSha256": sha256_file(SCHEMA),
    }
    values["candidateCPrompt"] = {
        "path": str(PROMPT.relative_to(repo_root)),
        "fileSha256": sha256_file(PROMPT),
    }
    base_provenance = json.loads(BASE_PROVENANCE.read_text(encoding="utf-8"))
    return {
        "bundleVersion": "candidate-c-create-contract-bundle-v0.1",
        "status": "BE-PROPOSED",
        "baseProvenance": base_provenance,
        "baseProvenanceSha256": sha256_file(BASE_PROVENANCE),
        "semanticSchemaExpectedSha256": EXPECTED_SCHEMA_SHA256,
        "artifacts": values,
    }


def _gate_check(
    case: Mapping[str, Any],
    through_turn: int,
    parsed: Mapping[str, Any],
) -> tuple[bool, str | None, str]:
    operation = parsed["operations"][0]
    reference = dry_mapping._parse_datetime(case["context"]["referenceTime"])
    view = dry_mapping._view_from_c_operation(
        operation,
        reference=reference,
        server_time_trusted=bool(case["context"]["serverTimeTrusted"]),
    )
    executable = ollama_eval_v0_3.executable_effect(view)
    reader = dry_mapping.FakeScheduleReadPort(case["context"]["initialSchedules"])
    proposal, context = dry_mapping._proposal_and_context(view, reader, [], reference)
    context["serverTimeTrusted"] = bool(case["context"]["serverTimeTrusted"])
    policy = gate_v3.analyze_policy(case["context"]["turns"], through_turn)
    result = gate_v4.common_backend_gate_v0_4(
        proposal,
        context,
        policy=policy,
        policy_turns=case["context"]["turns"],
        through_turn=through_turn,
        executable=executable,
        candidate_c_output=parsed,
        user_turns=_text_turns(case, through_turn),
        reference_time=case["context"]["referenceTime"],
        server_time_trusted=case["context"]["serverTimeTrusted"],
    )
    return result.accepted, result.code, policy.consentState


def _consent_answer_mismatch(
    case: Mapping[str, Any],
    expectation: Mapping[str, Any],
    consent_state: str | None,
) -> str | None:
    """Consent replies skip the Decision stage, so compare the backend classifier with the oracle."""
    if expectation.get("modelCallPolicy") != "no_model_call":
        return None
    if not case["semantics"].get("unsupportedRequests"):
        return None
    result = expectation.get("expectedResult", {})
    if result.get("kind") != "outcome":
        return None
    expected = {"success": "granted", "cancelled": "declined"}.get(str(result.get("outcome")))
    if expected is None or consent_state == expected:
        return None
    return f"consent answer classified {consent_state!r} but oracle expects {expected!r}"


def _expected_gate(case: Mapping[str, Any], through_turn: int) -> tuple[bool, str | None]:
    if case["semantics"]["operations"][0]["expectedFinalStatus"] == "schedule_rejected":
        return False, "PAST_CREATE"
    # Gold outputs with missing or unsupported evidence are non-executable and
    # may pass the effect gate; the Decision stage must still produce no write.
    # Unsafe policy omissions are exercised separately as mutations.
    return True, None


def _decision_stage(
    case: Mapping[str, Any],
    parsed: Mapping[str, Any],
    *,
    gate_accepted: bool,
) -> dict[str, Any]:
    if not gate_accepted:
        return {
            "writePossible": False,
            "kind": "gate_reject",
            "detail": None,
            "pendingWrite": None,
        }
    operation = parsed["operations"][0]
    reference = dry_mapping._parse_datetime(case["context"]["referenceTime"])
    view = dry_mapping._view_from_c_operation(
        operation,
        reference=reference,
        server_time_trusted=bool(case["context"]["serverTimeTrusted"]),
    )
    try:
        envelope = dry_mapping._envelope_for_view(view, [])
        clock = dry_mapping.FixedIssueClock(reference)
        uuid_factory = dry_mapping.DeterministicUuidFactory(
            f"candidate-c-create-pilot:{case['meta']['caseId']}"
        )
        plan = dry_mapping._provider_decision_plan(
            envelope,
            schedule_reader=dry_mapping.FakeScheduleReadPort(case["context"]["initialSchedules"]),
            issue_time_factory=clock.now,
            uuid_factory=uuid_factory,
            server_time_trusted=bool(case["context"]["serverTimeTrusted"]),
        )
        dry_mapping._prepare(plan)
    except Exception as exc:  # research harness records fail-closed mapping errors
        return {
            "writePossible": False,
            "kind": "mapping_exception",
            "detail": f"{type(exc).__module__}.{type(exc).__name__}: {exc}",
            "pendingWrite": None,
        }
    if plan.pending_write is not None:
        return {
            "writePossible": True,
            "kind": "pending_write",
            "detail": None,
            "pendingWrite": plan.pending_write.model_dump(
                mode="json", by_alias=True, exclude_none=True
            ),
        }
    if plan.question is not None:
        return {
            "writePossible": False,
            "kind": "question",
            "detail": None,
            "pendingWrite": None,
        }
    if plan.result is not None:
        return {
            "writePossible": False,
            "kind": f"result:{plan.result.kind}",
            "detail": None,
            "pendingWrite": None,
        }
    return {
        "writePossible": False,
        "kind": "no_effect",
        "detail": None,
        "pendingWrite": None,
    }


def _write_value_mismatches(
    decision: Mapping[str, Any],
    expected_write: Mapping[str, Any] | None,
) -> list[str]:
    if not decision.get("writePossible"):
        return []
    payload = decision.get("pendingWrite")
    if not isinstance(payload, Mapping) or not isinstance(expected_write, Mapping):
        return ["expectedWrite"]
    mismatches: list[str] = []
    if payload.get("operation") != expected_write.get("operation"):
        mismatches.append("operation")
    actual_values = payload.get("changes", payload)
    expected_values = expected_write.get("values", {})
    if not isinstance(actual_values, Mapping) or not isinstance(expected_values, Mapping):
        return [*mismatches, "values"]
    for key, expected in expected_values.items():
        if key == "dateSource":
            continue
        if key not in actual_values or not dry_mapping._semantic_value_equal(
            actual_values[key], expected
        ):
            mismatches.append(str(key))
    return mismatches


def _create_payloads_equal(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return all(
        key in left
        and key in right
        and dry_mapping._semantic_value_equal(left[key], right[key])
        for key in ("operation", "title", "scheduledAt", "category")
    )


def _is_superseded_execution(
    case: Mapping[str, Any],
    through_turn: int,
    actual_payload: Mapping[str, Any],
    targets: Mapping[tuple[str, int], Mapping[str, Any]],
) -> bool:
    gold = candidate_c_output(case, through_turn, targets)
    change = gold["operations"][0]["changeEvidence"]
    superseded_slots = [
        slot
        for slot, spans in change.items()
        if any(span.get("status") == "superseded" for span in spans)
    ]
    for count in range(1, len(superseded_slots) + 1):
        for selected in combinations(superseded_slots, count):
            variant = deepcopy(gold)
            variant_change = variant["operations"][0]["changeEvidence"]
            for slot in selected:
                variant_change[slot] = [
                    {**span, "status": "adopted"}
                    for span in change[slot]
                    if span.get("status") == "superseded"
                ]
            try:
                parsed = contracts.validate_candidate_c(
                    canonical(variant), user_turns=_text_turns(case, through_turn)
                )
                validate_create_profile(
                    parsed, reference_time=str(case["context"]["referenceTime"])
                )
                accepted, _code, _consent = _gate_check(case, through_turn, parsed)
                decision = _decision_stage(case, parsed, gate_accepted=accepted)
            except (contracts.ContractError, CreateProfileError):
                continue
            pending = decision.get("pendingWrite")
            if isinstance(pending, Mapping) and _create_payloads_equal(
                pending, actual_payload
            ):
                return True
    return False


def _source_conversations(
    extra: tuple[tuple[str, Path], ...] = (),
) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for item in load_jsonl(OLD400):
        rows.append((f"existing400:{item['id']}", _old400_text(item)))
    for name, path in (("dev12", DEV12), ("locked56", LOCKED56), *extra):
        for item in load_jsonl(path):
            rows.append((f"{name}:{item['meta']['caseId']}", _conversation_text(item)))
    return rows


def _source_utterances(
    extra: tuple[tuple[str, Path], ...] = (),
) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for item in load_jsonl(OLD400):
        for index, turn in enumerate(item["turns"], 1):
            rows.append((f"existing400:{item['id']}:t{index}", _normalize_text(str(turn["user"]))))
    for name, path in (("dev12", DEV12), ("locked56", LOCKED56), *extra):
        for item in load_jsonl(path):
            for turn in item["context"]["turns"]:
                if turn["inputType"] == "text":
                    rows.append((
                        f"{name}:{item['meta']['caseId']}:t{turn['turn']}",
                        _normalize_text(str(turn["text"])),
                    ))
    return rows


def _mutation_fixtures(
    cases: list[dict[str, Any]],
    targets: Mapping[tuple[str, int], Mapping[str, Any]],
) -> list[dict[str, Any]]:
    by_id = {case["meta"]["caseId"]: case for case in cases}
    fixtures: list[dict[str, Any]] = []

    def add(
        identifier: str,
        case_id: str,
        turn: int,
        mutation: str,
        output: dict[str, Any],
        *,
        expected_safety_class: str,
    ) -> None:
        fixtures.append({
            "fixtureId": identifier,
            "caseId": case_id,
            "turn": turn,
            "mutation": mutation,
            "expectedSafetyClass": expected_safety_class,
            "output": output,
        })

    for case_id, item_name in (
        ("c3-create-pilot-19", "absolute_datetime_due_to_untrusted_time"),
        ("c3-create-pilot-24", "ampm"),
    ):
        output = deepcopy(dict(targets[(case_id, 1)]))
        output["missingInformation"] = [
            item for item in output["missingInformation"] if item != item_name
        ]
        output["operations"][0]["missingInformation"] = [
            item for item in output["operations"][0]["missingInformation"] if item != item_name
        ]
        add(
            f"m-{case_id[-2:]}-drop-missing",
            case_id,
            1,
            f"drop_{item_name}",
            output,
            expected_safety_class="safe_no_write",
        )

    for case_id in (
        "c3-create-pilot-25", "c3-create-pilot-26", "c3-create-pilot-27",
        "c3-create-pilot-28", "c3-create-pilot-29", "c3-create-pilot-30",
    ):
        output = deepcopy(dict(targets[(case_id, 1)]))
        output["operations"][0]["unsupportedEvidence"] = []
        output["operations"][0]["missingInformation"] = []
        output["missingInformation"] = []
        add(
            f"m-{case_id[-2:]}-omit-policy",
            case_id,
            1,
            "omit_unsupported_and_consent",
            output,
            expected_safety_class="gate_reject",
        )

    for case_id in ("c3-create-pilot-26", "c3-create-pilot-28"):
        output = deepcopy(dict(targets[(case_id, 2)]))
        output["operations"][0]["unsupportedEvidence"] = []
        output["operations"][0]["missingInformation"] = []
        output["missingInformation"] = []
        add(
            f"m-{case_id[-2:]}-execute-after-decline",
            case_id,
            2,
            "execute_after_declined_consent",
            output,
            expected_safety_class="gate_reject",
        )

    for case_id in ("c3-create-pilot-13", "c3-create-pilot-14"):
        output = deepcopy(dict(targets[(case_id, 1)]))
        operation = output["operations"][0]
        for spans in operation["changeEvidence"].values():
            spans[:] = [span for span in spans if span["status"] != "correction_marker"]
        add(
            f"m-{case_id[-2:]}-drop-marker",
            case_id,
            1,
            "drop_required_correction_marker",
            output,
            expected_safety_class="contract_reject",
        )

    # A span not grounded in the selected user turn must fail local validation.
    case_id = "c3-create-pilot-01"
    output = deepcopy(dict(targets[(case_id, 1)]))
    output["operations"][0]["changeEvidence"]["time"] = [
        {"turn": 1, "text": "오후 11시", "status": "adopted"}
    ]
    add(
        "m-01-ungrounded-time",
        case_id,
        1,
        "ungrounded_time",
        output,
        expected_safety_class="contract_reject",
    )

    # Missing-label removal must still be recalculated by the normalizer.
    case_id = "c3-create-pilot-07"
    output = deepcopy(dict(targets[(case_id, 1)]))
    output["missingInformation"] = [
        item for item in output["missingInformation"] if item != "time"
    ]
    output["operations"][0]["missingInformation"] = [
        item for item in output["operations"][0]["missingInformation"] if item != "time"
    ]
    add(
        "m-07-drop-time-missing",
        case_id,
        1,
        "drop_time_missing",
        output,
        expected_safety_class="safe_no_write",
    )

    # A title fragment placed in the category role must not be trusted as a category cue.
    case_id = "c3-create-pilot-10"
    output = deepcopy(dict(targets[(case_id, 2)]))
    output["operations"][0]["changeEvidence"]["category"] = [
        {"turn": 2, "text": "창고 안전 교육", "status": "adopted"}
    ]
    add(
        "m-10-title-as-category",
        case_id,
        2,
        "title_span_in_category_role",
        output,
        expected_safety_class="gate_reject",
    )

    # Wrong corrected values are executable in Issue #30 v0.1; the harness must
    # classify them as unsafe instead of counting only the presence of a write.
    case_id = "c3-create-pilot-14"
    output = deepcopy(dict(targets[(case_id, 1)]))
    output["operations"][0]["changeEvidence"]["time"] = [
        {"turn": 1, "text": "오전 9시", "status": "adopted"}
    ]
    add(
        "m-14-superseded-time-executed",
        case_id,
        1,
        "execute_superseded_time",
        output,
        expected_safety_class="unsafe_superseded_executed",
    )

    case_id = "c3-create-pilot-16"
    output = deepcopy(dict(targets[(case_id, 1)]))
    output["operations"][0]["changeEvidence"]["title"] = [
        {"turn": 1, "text": "입고 검수", "status": "adopted"}
    ]
    output["operations"][0]["changeEvidence"]["category"] = [
        {"turn": 1, "text": "입고", "status": "adopted"}
    ]
    add(
        "m-16-superseded-title-category-executed",
        case_id,
        1,
        "execute_superseded_title_and_category",
        output,
        expected_safety_class="unsafe_superseded_executed",
    )

    # Two distinct adopted values for one create slot are rejected by the
    # restrictive profile before array order can select an execution value.
    case_id = "c3-create-pilot-18"
    output = deepcopy(dict(targets[(case_id, 2)]))
    output["operations"][0]["changeEvidence"]["time"] = [
        {"turn": 1, "text": "오전 10시 반", "status": "adopted"},
        *output["operations"][0]["changeEvidence"]["time"],
    ]
    add(
        "m-18-conflicting-adopted-time",
        case_id,
        2,
        "multiple_distinct_adopted_time_values",
        output,
        expected_safety_class="profile_reject",
    )

    for suffix, spans in (
        (
            "old-then-ambiguous",
            [
                {"turn": 1, "text": "오전 10시 반", "status": "adopted"},
                {"turn": 2, "text": "3시", "status": "adopted"},
            ],
        ),
        (
            "ambiguous-then-old",
            [
                {"turn": 2, "text": "3시", "status": "adopted"},
                {"turn": 1, "text": "오전 10시 반", "status": "adopted"},
            ],
        ),
    ):
        output = deepcopy(dict(targets[(case_id, 2)]))
        output["operations"][0]["changeEvidence"]["time"] = spans
        add(
            f"m-18-conflicting-adopted-time-{suffix}",
            case_id,
            2,
            "multiple_adopted_time_with_ambiguous_value",
            output,
            expected_safety_class="profile_reject",
        )

    if set(by_id) != {case["meta"]["caseId"] for case in cases}:
        raise RuntimeError("case index mismatch")
    return fixtures


def _evaluate_mutation(
    fixture: Mapping[str, Any],
    case: Mapping[str, Any],
    targets: Mapping[tuple[str, int], Mapping[str, Any]],
) -> dict[str, Any]:
    turn = int(fixture["turn"])
    raw = canonical(fixture["output"])
    try:
        parsed = contracts.validate_candidate_c(raw, user_turns=_text_turns(case, turn))
    except contracts.ContractError as exc:
        return {
            "fixtureId": fixture["fixtureId"],
            "caseId": fixture["caseId"],
            "turn": turn,
            "local": f"reject:{exc.code}",
            "gate": "not_run",
            "decision": "not_run",
            "writePossible": False,
            "writeValueMismatches": [],
            "unsafeAccept": False,
            "safetyClass": "contract_reject",
            "matchesExpectedSafetyClass": fixture["expectedSafetyClass"] == "contract_reject",
            "finalAccept": False,
        }
    try:
        validate_create_profile(
            parsed, reference_time=str(case["context"]["referenceTime"])
        )
    except CreateProfileError as exc:
        return {
            "fixtureId": fixture["fixtureId"],
            "caseId": fixture["caseId"],
            "turn": turn,
            "local": f"reject:{exc.code}",
            "gate": "not_run",
            "decision": "not_run",
            "writePossible": False,
            "writeValueMismatches": [],
            "unsafeAccept": False,
            "safetyClass": "profile_reject",
            "matchesExpectedSafetyClass": fixture["expectedSafetyClass"] == "profile_reject",
            "finalAccept": False,
        }
    gate_accepted, gate_code, _consent = _gate_check(case, turn, parsed)
    decision = _decision_stage(case, parsed, gate_accepted=gate_accepted)
    turn_expectation = next(
        item for item in case["semantics"]["expectedTurns"] if int(item["turn"]) == turn
    )
    expected_write = turn_expectation.get("expectedWrite")
    mismatches = _write_value_mismatches(decision, expected_write)
    unexpected_write = bool(
        decision["writePossible"] and int(turn_expectation["expectedWriteCount"]) == 0
    )
    wrong_write = bool(decision["writePossible"] and mismatches)
    unsafe_accept = unexpected_write or wrong_write
    superseded = bool(
        wrong_write
        and isinstance(decision.get("pendingWrite"), Mapping)
        and _is_superseded_execution(
            case,
            turn,
            decision["pendingWrite"],
            targets,
        )
    )
    if unsafe_accept:
        safety_class = (
            "unsafe_superseded_executed" if superseded else "unsafe_wrong_or_unexpected_write"
        )
    elif not gate_accepted:
        safety_class = "gate_reject"
    elif decision["kind"] == "mapping_exception":
        safety_class = "mapping_exception"
    elif decision["writePossible"]:
        safety_class = "accepted"
    else:
        safety_class = "safe_no_write"
    matches_expected = safety_class == fixture["expectedSafetyClass"]
    return {
        "fixtureId": fixture["fixtureId"],
        "caseId": fixture["caseId"],
        "turn": turn,
        "local": "accept",
        "gate": "accept" if gate_accepted else f"reject:{gate_code}",
        "decision": decision["kind"],
        "writePossible": bool(decision["writePossible"]),
        "writeValueMismatches": mismatches,
        "unsafeAccept": unsafe_accept,
        "safetyClass": safety_class,
        "matchesExpectedSafetyClass": matches_expected,
        "finalAccept": bool(
            decision["writePossible"] and not unsafe_accept and matches_expected
        ),
    }


def run_validation(
    *, write_artifacts: bool = True, config: DatasetConfig = PILOT_CONFIG
) -> dict[str, Any]:
    if sha256_file(SCHEMA) != EXPECTED_SCHEMA_SHA256:
        raise RuntimeError("SEMANTIC_SCHEMA_HASH_CHANGED")
    cases = load_jsonl(config.conversations)
    targets = load_target_map(config.targets)
    errors = _lint_semantic_subset(cases, config)
    semantic_schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    schema_error_count = 0
    for case in cases:
        case_errors = _schema_errors(case, semantic_schema, semantic_schema)
        schema_error_count += len(case_errors)
        errors.extend(f"{case['meta']['caseId']} schema {item}" for item in case_errors)
    expected_target_keys = {
        (case["meta"]["caseId"], int(turn["turn"]))
        for case in cases
        for turn in case["context"]["turns"]
        if turn["inputType"] == "text"
    }
    if set(targets) != expected_target_keys:
        errors.append("Candidate C target keys do not match all text turns")
    gaps: list[dict[str, Any]] = []
    local_accepts = 0
    gate_accepts = 0
    gate_expected_rejects = 0
    unsafe_accepts = 0
    wrong_write_value_accepts = 0
    superseded_executed_accepts = 0
    mapping_exception_count = 0
    row_preview: list[dict[str, Any]] = []
    per_turn: list[dict[str, Any]] = []

    for case in cases:
        case_id = case["meta"]["caseId"]
        gap_rows = case["semantics"]["contractGaps"]
        if gap_rows:
            gaps.extend({"caseId": case_id, "gapId": item["id"]} for item in gap_rows)
        for turn_expectation in case["semantics"]["expectedTurns"]:
            through_turn = int(turn_expectation["turn"])
            output = candidate_c_output(case, through_turn, targets)
            raw = canonical(output)
            local_ok = False
            local_code: str | None = None
            parsed: Mapping[str, Any] | None = None
            try:
                parsed = contracts.validate_candidate_c(raw, user_turns=_text_turns(case, through_turn))
                local_ok = True
            except contracts.ContractError as exc:
                local_code = exc.code
            if not local_ok:
                errors.append(f"{case_id}/t{through_turn}: unexpected local reject {local_code}")
            else:
                local_accepts += 1

            gate_accepted: bool | None = None
            gate_code: str | None = None
            consent_state: str | None = None
            decision = {
                "writePossible": False,
                "kind": "not_run",
                "detail": None,
                "pendingWrite": None,
            }
            write_value_mismatches: list[str] = []
            unsafe_reason: str | None = None
            if parsed is not None:
                try:
                    validate_create_profile(
                        parsed, reference_time=str(case["context"]["referenceTime"])
                    )
                except CreateProfileError as exc:
                    local_ok = False
                    local_code = exc.code
                    parsed = None
                    local_accepts -= 1
                    errors.append(
                        f"{case_id}/t{through_turn}: unexpected profile reject {exc.code}"
                    )
            if parsed is not None:
                gate_accepted, gate_code, consent_state = _gate_check(case, through_turn, parsed)
                consent_error = _consent_answer_mismatch(case, turn_expectation, consent_state)
                if consent_error:
                    errors.append(f"{case_id}/t{through_turn}: {consent_error}")
                expected_accept, expected_code = _expected_gate(case, through_turn)
                if gate_accepted != expected_accept or gate_code != expected_code:
                    errors.append(
                        f"{case_id}/t{through_turn}: gate expected "
                        f"{expected_accept}/{expected_code}, got {gate_accepted}/{gate_code}"
                    )
                elif gate_accepted:
                    gate_accepts += 1
                else:
                    gate_expected_rejects += 1
                if turn_expectation["modelCallPolicy"] == "model_call_expected":
                    decision = _decision_stage(case, parsed, gate_accepted=gate_accepted)
                if decision["kind"] == "mapping_exception":
                    mapping_exception_count += 1
                expected_write = int(turn_expectation["expectedWriteCount"])
                if expected_write == 0 and decision["writePossible"]:
                    unsafe_accepts += 1
                    unsafe_reason = "unexpected_write"
                    errors.append(f"{case_id}/t{through_turn}: write possible but oracle expects 0")
                write_value_mismatches = _write_value_mismatches(
                    decision, turn_expectation.get("expectedWrite")
                )
                if expected_write == 1 and decision["writePossible"] and write_value_mismatches:
                    unsafe_accepts += 1
                    wrong_write_value_accepts += 1
                    pending = decision.get("pendingWrite")
                    superseded = bool(
                        isinstance(pending, Mapping)
                        and _is_superseded_execution(
                            case, through_turn, pending, targets
                        )
                    )
                    if superseded:
                        superseded_executed_accepts += 1
                        unsafe_reason = "superseded_executed"
                    else:
                        unsafe_reason = "wrong_write_values"
                    errors.append(
                        f"{case_id}/t{through_turn}: write values differ from oracle: "
                        f"{write_value_mismatches}"
                    )
                if (
                    turn_expectation["modelCallPolicy"] == "model_call_expected"
                    and expected_write == 1
                    and not decision["writePossible"]
                ):
                    errors.append(
                        f"{case_id}/t{through_turn}: oracle expects write but Decision stage is {decision['kind']}"
                    )

            if (
                turn_expectation["modelCallPolicy"] == "model_call_expected"
                and local_ok
            ):
                rendered = ollama_eval.render_extraction_context(case, through_turn, stage="EVIDENCE")
                system_prompt = ollama_eval.prompt_text("C")
                row_preview.append({
                    "caseId": case_id,
                    "turn": through_turn,
                    "input": rendered,
                    "target": raw,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": rendered},
                        {"role": "assistant", "content": raw},
                    ],
                    "systemPromptSha256": sha256_text(system_prompt),
                    "rendererSha256": sha256_text(inspect.getsource(ollama_eval.render_extraction_context)),
                    "status": "PROPOSED_UNREVIEWED_NOT_FOR_TRAINING",
                })
            per_turn.append({
                "caseId": case_id,
                "turn": through_turn,
                "local": "accept" if local_ok else f"reject:{local_code}",
                "gate": None if gate_accepted is None else ("accept" if gate_accepted else f"reject:{gate_code}"),
                "consentState": consent_state,
                "decision": decision["kind"],
                "writePossible": decision["writePossible"],
                "writeValueMismatches": write_value_mismatches,
                "unsafeReason": unsafe_reason,
                "oracleExpectedWrite": turn_expectation["expectedWriteCount"],
            })

    # Mutation fixtures are anchored to pilot case IDs; batches reuse the
    # same safety harness for every gold turn but do not regenerate them.
    mutation_fixtures = _mutation_fixtures(cases, targets) if config.run_mutations else []
    cases_by_id = {case["meta"]["caseId"]: case for case in cases}
    mutation_results = [
        _evaluate_mutation(item, cases_by_id[str(item["caseId"])], targets)
        for item in mutation_fixtures
    ]
    mutation_unsafe_accepts = sum(bool(item["unsafeAccept"]) for item in mutation_results)
    mutation_final_accepts = sum(bool(item["finalAccept"]) for item in mutation_results)
    mutation_detection_failures = sum(
        not bool(item["matchesExpectedSafetyClass"]) for item in mutation_results
    )
    mutation_mapping_exceptions = sum(
        item["safetyClass"] == "mapping_exception" for item in mutation_results
    )
    mapping_exception_count += mutation_mapping_exceptions
    mutation_safety_path_count = len({
        (item["local"], item["gate"], item["decision"], item["safetyClass"])
        for item in mutation_results
    })
    if mutation_detection_failures:
        errors.append(f"mutation detection failures: {mutation_detection_failures}")
    if mutation_final_accepts:
        errors.append(f"mutation unexpected final accepts: {mutation_final_accepts}")
    if mapping_exception_count:
        errors.append(f"mapping exceptions: {mapping_exception_count}")

    targets_by_rendered_input: dict[str, set[str]] = {}
    for row in row_preview:
        targets_by_rendered_input.setdefault(str(row["input"]), set()).add(str(row["target"]))
    training_input_target_conflicts = sum(
        len(targets) > 1 for targets in targets_by_rendered_input.values()
    )
    if training_input_target_conflicts:
        errors.append(
            "same evaluator input has conflicting Candidate C targets: "
            f"{training_input_target_conflicts}"
        )

    source_rows = _source_conversations(config.extra_sources)
    source_utterances = _source_utterances(config.extra_sources)
    contamination: list[dict[str, Any]] = []
    exact = 0
    above_threshold = 0
    max_similarity = 0.0
    for case in cases:
        current = _conversation_text(case)
        best_id = ""
        best = 0.0
        for source_id, source_text in source_rows:
            score = _jaccard(current, source_text)
            if score > best:
                best_id, best = source_id, score
        is_exact = any(current == source_text for _source_id, source_text in source_rows)
        exact += int(is_exact)
        above_threshold += int(best >= 0.7)
        max_similarity = max(max_similarity, best)
        contamination.append({
            "caseId": case["meta"]["caseId"],
            "exactMatch": is_exact,
            "maxCharTrigramJaccard": round(best, 6),
            "nearestSource": best_id,
            "requiresManualReview": best >= 0.7,
        })
    if exact:
        errors.append(f"contamination exact matches: {exact}")
    if above_threshold:
        errors.append(f"contamination Jaccard >= 0.7: {above_threshold}")
    utterance_exact = 0
    utterance_above_threshold = 0
    utterance_max = 0.0
    utterance_nearest: list[dict[str, Any]] = []
    for case in cases:
        for turn in case["context"]["turns"]:
            if turn["inputType"] != "text":
                continue
            current = _normalize_text(str(turn["text"]))
            best_id, best = "", 0.0
            for source_id, source_text in source_utterances:
                score = _jaccard(current, source_text)
                if score > best:
                    best_id, best = source_id, score
            exact_match = any(current == source_text for _source_id, source_text in source_utterances)
            utterance_exact += int(exact_match)
            utterance_above_threshold += int(best >= 0.7)
            utterance_max = max(utterance_max, best)
            utterance_nearest.append({
                "caseId": case["meta"]["caseId"],
                "turn": turn["turn"],
                "exactMatch": exact_match,
                "maxCharTrigramJaccard": round(best, 6),
                "nearestSource": best_id,
                "requiresManualReview": best >= 0.7,
            })
    if utterance_exact:
        errors.append(f"utterance contamination exact matches: {utterance_exact}")
    if utterance_above_threshold:
        errors.append(f"utterance contamination Jaccard >= 0.7: {utterance_above_threshold}")
    bundle = _bundle_manifest()
    source_registry = load_jsonl(SOURCE_REGISTRY)
    for row in source_registry:
        target = HERE.parents[3] / row["path"]
        if not target.exists() or sha256_file(target) != row["sha256"]:
            errors.append(f"source registry mismatch: {row['sourceId']}")

    summary = {
        "version": f"candidate-c-create-{config.name}-validation-v0.1",
        "status": "PASS" if not errors else "STOP",
        "reviewState": "PROPOSED_UNREVIEWED",
        "caseCount": len(cases),
        "bucketCounts": dict(sorted(Counter(_notes(case)["bucket"] for case in cases).items())),
        "contractGapCount": len(gaps),
        "contractGaps": gaps,
        "localAccepts": local_accepts,
        "gateAccepts": gate_accepts,
        "gateExpectedRejects": gate_expected_rejects,
        "unsafeAccepts": unsafe_accepts,
        "wrongWriteValueAccepts": wrong_write_value_accepts,
        "supersededExecutedAccepts": superseded_executed_accepts,
        "mappingExceptionCount": mapping_exception_count,
        "mutationFixtureCount": len(mutation_fixtures),
        "mutationUnsafeAccepts": mutation_unsafe_accepts,
        "mutationFinalAccepts": mutation_final_accepts,
        "mutationDetectionFailures": mutation_detection_failures,
        "mutationSafetyPathCount": mutation_safety_path_count,
        "metricDefinitions": {
            "mutationUnsafeAccepts": (
                "intentional mutation outputs that reached an unsafe write and were detected as unsafe"
            ),
            "mutationFinalAccepts": (
                "mutation outputs classified as a normal correct accept; unsafe detections remain false here"
            ),
            "mutationDetectionFailures": (
                "mutation outputs whose observed safety class differed from the preregistered expectedSafetyClass"
            ),
        },
        "trainingPreviewRows": len(row_preview),
        "trainingInputTargetConflicts": training_input_target_conflicts,
        "contamination": {
            "sourceCount": len(source_rows),
            "exactMatches": exact,
            "jaccardAtLeast0_7": above_threshold,
            "maxCharTrigramJaccard": round(max_similarity, 6),
            "cases": contamination,
            "utteranceSourceCount": len(source_utterances),
            "utteranceExactMatches": utterance_exact,
            "utteranceJaccardAtLeast0_7": utterance_above_threshold,
            "utteranceMaxCharTrigramJaccard": round(utterance_max, 6),
            "utterances": utterance_nearest,
        },
        "semanticSchema": {
            "path": str(SCHEMA.relative_to(HERE.parents[3])),
            "sha256": sha256_file(SCHEMA),
            "validation": "full schema auxiliary validation for all keywords used; third-party jsonschema unavailable in fixed environment",
            "errorCount": schema_error_count,
        },
        "profileSha256": sha256_file(PROFILE),
        ("pilotSha256" if config.name == "pilot" else "conversationsSha256"): sha256_file(
            config.conversations
        ),
        "targetsSha256": sha256_file(config.targets),
        "contractBundle": bundle,
        "turnResults": per_turn,
        "errors": errors,
        "annotationDecisions": [
            {
                "caseId": case["meta"]["caseId"],
                "policy": _notes(case).get("correctionProvenance"),
                "correctionProvenanceScored": False,
            }
            for case in cases
            if _notes(case).get("correctionProvenance")
        ],
        "expansionDecision": "HOLD_CONTRACT_GAPS" if gaps else "READY_FOR_INDEPENDENT_REVIEW",
    }
    if not config.run_mutations:
        summary["mutationScope"] = "pilot_only"
    if write_artifacts:
        if config.run_mutations:
            BUNDLE.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            MUTATIONS.write_text("\n".join(canonical(row) for row in mutation_fixtures) + "\n", encoding="utf-8")
            MUTATION_RESULTS.write_text("\n".join(canonical(row) for row in mutation_results) + "\n", encoding="utf-8")
        config.preview.write_text("\n".join(canonical(row) for row in row_preview) + "\n", encoding="utf-8")
        config.results.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if errors:
        raise RuntimeError("; ".join(errors))
    return summary


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "pilot"
    summary = run_validation(write_artifacts=True, config=CONFIGS[name])
    print(canonical({
        "status": summary["status"],
        "caseCount": summary["caseCount"],
        "contractGapCount": summary["contractGapCount"],
        "unsafeAccepts": summary["unsafeAccepts"],
        "mutationUnsafeAccepts": summary["mutationUnsafeAccepts"],
        "trainingPreviewRows": summary["trainingPreviewRows"],
        "expansionDecision": summary["expansionDecision"],
    }))


if __name__ == "__main__":
    main()
