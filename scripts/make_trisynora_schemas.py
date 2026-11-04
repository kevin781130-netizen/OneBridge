"""Build standalone JSON Schema 2020-12 files for TRISYNORA v1."""
from copy import deepcopy
from pathlib import Path
import json

OUT = Path(__file__).resolve().parent.parent / "src" / "onebridge" / "evidence_contracts" / "contracts"
OUT.mkdir(parents=True, exist_ok=True)

ID = {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$"}
HASH = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
TEXT = {"type": "string", "minLength": 1, "maxLength": 4096}
FILENAME = {"type": "string", "minLength": 1, "maxLength": 1024,
            "allOf": [{"not": {"pattern": r"^(?:/|\\|[A-Za-z]:|[a-z]+://)"}},
                       {"not": {"pattern": r"(?:^|[\\/])\.\.(?:[\\/]|$)"}}]}
TIMESTAMP = {"type": "string", "format": "date-time"}


def obj(props, required=None):
    return {"type": "object", "properties": props, "required": required or list(props),
            "additionalProperties": False}


def schema(kind, fields, required):
    common = {
        "schema_version": {"const": "trisynora/v1"},
        "record_type": {"const": kind},
        "record_id": ID,
        "event_id": ID,
        "tenant_id": ID,
        "project_id": ID,
        "produced_at": TIMESTAMP,
        "producer": ID,
        "producer_version": TEXT,
        "source": obj({"system": ID, "reference": TEXT, "sha256": HASH}),
    }
    res = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://schemas.trisynora.dev/v1/{kind}.schema.json",
        "title": f"TRISYNORA {kind} v1",
        "description": "Structure only. Human authority and file-integrity require trusted runtime validation.",
        "type": "object", "properties": {**deepcopy(common), **fields},
        "required": list(common) + required,
        "additionalProperties": False,
    }
    (OUT / f"{kind}.schema.json").write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


DELIVERY = obj({"width": {"type": "integer", "minimum": 16, "maximum": 16384},
                "height": {"type": "integer", "minimum": 16, "maximum": 16384},
                "fps_num": {"type": "integer", "minimum": 1, "maximum": 1000000},
                "fps_den": {"type": "integer", "minimum": 1, "maximum": 1000000}})
RIGHTS = obj({"reuse_allowed": {"type": "boolean"},
              "cross_tenant_learning_allowed": {"type": "boolean"},
              "external_training_allowed": {"type": "boolean"}})
EVIDENCE = obj({"relative_path": FILENAME, "sha256": HASH})

schema("ProjectSpec", {
    "name": TEXT, "delivery": DELIVERY, "rights": RIGHTS,
}, ["name", "delivery", "rights"])

schema("ShotSpec", {
    "project_spec_record_id": ID,
    "shot_id": ID,
    "duration_frames": {"type": "integer", "minimum": 1, "maximum": 1000000},
    "fps_num": {"type": "integer", "minimum": 1, "maximum": 1000000},
    "fps_den": {"type": "integer", "minimum": 1, "maximum": 1000000},
    "intent": TEXT,
    "acceptance_criteria": {"type": "array", "items": TEXT, "minItems": 1, "maxItems": 64, "uniqueItems": True},
}, ["project_spec_record_id", "shot_id", "duration_frames", "fps_num", "fps_den", "intent", "acceptance_criteria"])

schema("ArtifactRecord", {
    "shot_record_id": ID,
    "artifact": obj({"relative_path": FILENAME,
                     "sha256": HASH,
                     "mime": {"type": "string", "pattern": r"^[a-z0-9.+-]+/[a-z0-9.+-]+$"},
                     "byte_size": {"type": "integer", "minimum": 0},
                     "width": {"type": "integer", "minimum": 1},
                     "height": {"type": "integer", "minimum": 1},
                     "fps_num": {"type": "integer", "minimum": 1},
                     "fps_den": {"type": "integer", "minimum": 1},
                     "frame_count": {"type": "integer", "minimum": 1},
                    }, ["relative_path", "sha256", "mime", "byte_size"]),
    "input_digests": {"type": "array", "items": HASH, "maxItems": 128, "uniqueItems": True},
}, ["shot_record_id", "artifact"])

schema("EvaluationRecord", {
    "artifact_record_id": ID,
    "artifact_sha256": HASH,
    "evaluator": ID,
    "evaluator_version": TEXT,
    "status": {"enum": ["passed", "failed", "not-run", "blocked"]},
    "criteria": {"type": "array", "minItems": 0, "maxItems": 128,
                 "items": obj({"name": ID,
                               "status": {"enum": ["passed", "failed", "not-run", "blocked"]},
                               "actual": {"type": ["number", "null"]},
                               "threshold": {"type": ["number", "null"]},
                              }, ["name", "status"])},
    "report": EVIDENCE,
}, ["artifact_record_id", "artifact_sha256", "evaluator", "evaluator_version", "status", "criteria", "report"])

schema("HumanReview", {
    "artifact_record_id": ID,
    "artifact_sha256": HASH,
    "reviewer_id": ID,
    "decision": {"enum": ["approved", "rejected"]},
    "notes": {"type": "string", "maxLength": 4096},
}, ["artifact_record_id", "artifact_sha256", "reviewer_id", "decision"])

schema("DecisionRecord", {
    "chosen_artifact_record_id": ID,
    "candidate_artifact_record_ids": {"type": "array", "items": ID, "minItems": 1,
                                      "maxItems": 128, "uniqueItems": True},
    "evaluation_record_ids": {"type": "array", "items": ID, "maxItems": 128, "uniqueItems": True},
    "rationale": TEXT,
}, ["chosen_artifact_record_id", "candidate_artifact_record_ids", "evaluation_record_ids", "rationale"])

schema("RevisionEdge", {
    "from_artifact_record_id": ID,
    "to_artifact_record_id": ID,
    "before_evaluation_record_id": ID,
    "after_evaluation_record_id": ID,
    "change_summary": TEXT,
    "change_digest": HASH,
}, ["from_artifact_record_id", "to_artifact_record_id", "change_summary", "change_digest"])

print("wrote", len(list(OUT.glob("*.schema.json"))), "schemas")
