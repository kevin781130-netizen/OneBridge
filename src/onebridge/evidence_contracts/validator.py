"""Versioned evidence contract validator.

Important: structure validation is NOT identity authentication or proof of rendered quality.
The CLI never accepts a user-provided flag to attest human authority. Trusted
service integrations must verify a human's identity *before* passing a mapping
of authorized review event IDs to reviewer IDs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Mapping, Any

from jsonschema import Draft202012Validator, FormatChecker

KINDS = frozenset({
    "ProjectSpec", "ShotSpec", "ArtifactRecord", "EvaluationRecord",
    "HumanReview", "DecisionRecord", "RevisionEdge",
})
SCHEMAS = Path(__file__).resolve().parent / "contracts"


class ContractError(ValueError):
    """The bundle cannot be trusted as a well-formed evidence chain."""


def _canonical(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _schema(kind: str) -> dict[str, Any]:
    return json.loads((SCHEMAS / f"{kind}.schema.json").read_text(encoding="utf-8"))


def _verify_file(root: Path, relative: str, expected_digest: str, expected_size: int | None = None) -> None:
    # Resolve symlinks and refuse escapes from the operator-approved evidence root.
    try:
        candidate = (root / relative).resolve(strict=True)
        candidate.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ContractError(f"evidence path is missing or outside root: {relative}") from exc
    if not candidate.is_file():
        raise ContractError(f"evidence path is not a file: {relative}")
    h = hashlib.sha256()
    size = 0
    with candidate.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            h.update(chunk)
            size += len(chunk)
    if h.hexdigest() != expected_digest:
        raise ContractError(f"sha256 mismatch: {relative}")
    if expected_size is not None and size != expected_size:
        raise ContractError(f"byte_size mismatch: {relative}")


def validate_bundle(
    records: list[dict[str, Any]],
    *,
    evidence_root: Path | None = None,
    trusted_human_reviewers: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Validate a *single submitted bundle*, not a persistent ledger.

    Duplicates are idempotent within this bundle; persistence and durable
    idempotence are intentionally deferred. A trusted service (NOT an Agent)
    may pass verified reviewer IDs after external authentication. The CLI does
    not expose that parameter.
    """
    if not isinstance(records, list) or not records:
        raise ContractError("bundle must be a nonempty list")
    if len(records) > 10000:
        raise ContractError("bundle exceeds 10000 records")
    root = Path(evidence_root).resolve(strict=True) if evidence_root is not None else None
    trusted = trusted_human_reviewers or {}
    validators: dict[str, Draft202012Validator] = {}
    unique: list[dict[str, Any]] = []
    by_event: dict[tuple[str, str], str] = {}
    by_id: dict[tuple[str, str], dict[str, Any]] = {}
    deduplicated = 0

    for idx, record in enumerate(records):
        if not isinstance(record, dict):
            raise ContractError(f"record {idx}: object required")
        kind = record.get("record_type")
        if kind not in KINDS:
            raise ContractError(f"record {idx}: unknown record_type")
        if kind not in validators:
            schema = _schema(kind)
            Draft202012Validator.check_schema(schema)
            validators[kind] = Draft202012Validator(schema, format_checker=FormatChecker())
        errors = sorted(validators[kind].iter_errors(record), key=lambda err: str(err.path))
        if errors:
            err = errors[0]
            raise ContractError(f"record {idx} ({kind}): {'.'.join(map(str, err.path))}: {err.message}")
        payload = _canonical(record)
        event_key = (record["tenant_id"], record["event_id"])
        record_key = (record["tenant_id"], record["record_id"])
        if event_key in by_event:
            if by_event[event_key] != payload:
                raise ContractError(f"event_id reused with different payload: {event_key[1]}")
            deduplicated += 1
            continue
        if record_key in by_id:
            raise ContractError(f"record_id reused with different event: {record_key[1]}")
        by_event[event_key] = payload
        by_id[record_key] = record
        unique.append(record)

    # This v1 API deliberately scopes each bundle to one project and tenant.
    tenant = unique[0]["tenant_id"]
    project = unique[0]["project_id"]
    if any(r["tenant_id"] != tenant or r["project_id"] != project for r in unique):
        raise ContractError("cross-tenant or cross-project bundle is forbidden")

    def link(r: dict[str, Any], field: str, expected_kind: str) -> dict[str, Any]:
        target_id = r[field]
        target = by_id.get((tenant, target_id))
        if target is None or target["record_type"] != expected_kind or target["project_id"] != project:
            raise ContractError(f"{r['record_id']}: invalid {field} -> {target_id} ({expected_kind})")
        return target

    # Two-pass validation permits records to arrive out of order.
    revision_graph: dict[str, list[str]] = {}
    artifacts_verified = 0
    reports_verified = 0
    for rec in unique:
        kind = rec["record_type"]
        if kind == "ShotSpec":
            link(rec, "project_spec_record_id", "ProjectSpec")
        elif kind == "ArtifactRecord":
            shot = link(rec, "shot_record_id", "ShotSpec")
            artifact = rec["artifact"]
            if root is not None:
                _verify_file(root, artifact["relative_path"], artifact["sha256"], artifact["byte_size"])
                artifacts_verified += 1
        elif kind == "EvaluationRecord":
            artifact = link(rec, "artifact_record_id", "ArtifactRecord")
            if rec["artifact_sha256"] != artifact["artifact"]["sha256"]:
                raise ContractError(f"{rec['record_id']}: artifact digest disagrees with referenced artifact")
            states = [c["status"] for c in rec["criteria"]]
            status = rec["status"]
            if status == "passed" and (not states or any(s != "passed" for s in states)):
                raise ContractError(f"{rec['record_id']}: passed requires nonempty all-passed criteria")
            if status == "failed" and "failed" not in states:
                raise ContractError(f"{rec['record_id']}: failed requires a failed criterion")
            if status in ("not-run", "blocked") and any(s in ("passed", "failed") for s in states):
                raise ContractError(f"{rec['record_id']}: unexecuted status cannot contain executed criteria")
            if root is not None:
                _verify_file(root, rec["report"]["relative_path"], rec["report"]["sha256"])
                reports_verified += 1
        elif kind == "HumanReview":
            artifact = link(rec, "artifact_record_id", "ArtifactRecord")
            if rec["artifact_sha256"] != artifact["artifact"]["sha256"]:
                raise ContractError(f"{rec['record_id']}: review artifact digest mismatch")
            if rec["producer"] != "human" or trusted.get(rec["event_id"]) != rec["reviewer_id"]:
                raise ContractError(f"{rec['record_id']}: untrusted human review; verified identity required")
        elif kind == "DecisionRecord":
            chosen = link(rec, "chosen_artifact_record_id", "ArtifactRecord")
            choices = rec["candidate_artifact_record_ids"]
            if chosen["record_id"] not in choices:
                raise ContractError(f"{rec['record_id']}: chosen artifact is not a candidate")
            for candidate in choices:
                fake = {**rec, "candidate": candidate}
                link(fake, "candidate", "ArtifactRecord")
            for eval_id in rec["evaluation_record_ids"]:
                evaluation = link({**rec, "evaluation": eval_id}, "evaluation", "EvaluationRecord")
                if evaluation["artifact_record_id"] not in choices:
                    raise ContractError(f"{rec['record_id']}: evaluation belongs to a non-candidate artifact")
        elif kind == "RevisionEdge":
            before = link(rec, "from_artifact_record_id", "ArtifactRecord")
            after = link(rec, "to_artifact_record_id", "ArtifactRecord")
            if before["record_id"] == after["record_id"] or before["shot_record_id"] != after["shot_record_id"]:
                raise ContractError(f"{rec['record_id']}: revision must connect distinct artifacts from the same shot")
            for field, artifact in (("before_evaluation_record_id", before), ("after_evaluation_record_id", after)):
                if field in rec:
                    evaluation = link(rec, field, "EvaluationRecord")
                    if evaluation["artifact_record_id"] != artifact["record_id"]:
                        raise ContractError(f"{rec['record_id']}: {field} references a different artifact")
            revision_graph.setdefault(before["record_id"], []).append(after["record_id"])

    # Iterative DFS avoids recursion-depth failure on long revision histories.
    marks: dict[str, int] = {}
    for start in revision_graph:
        if marks.get(start) == 2:
            continue
        marks[start] = 1
        stack = [(start, iter(revision_graph.get(start, [])))]
        while stack:
            node, next_edges = stack[-1]
            try:
                neighbor = next(next_edges)
            except StopIteration:
                marks[node] = 2
                stack.pop()
                continue
            if marks.get(neighbor) == 1:
                raise ContractError("revision graph cycle detected")
            if marks.get(neighbor) == 2:
                continue
            marks[neighbor] = 1
            stack.append((neighbor, iter(revision_graph.get(neighbor, []))))

    return {
        "ok": True,
        "schema_version": "trisynora/v1",
        "tenant_id": tenant,
        "project_id": project,
        "record_count": len(unique),
        "deduplicated_in_bundle": deduplicated,
        "schema_valid": True,
        "lineage_valid": True,
        "evidence_files_verified": root is not None,
        "artifacts_verified": artifacts_verified,
        "reports_verified": reports_verified,
        "human_review_count": sum(r["record_type"] == "HumanReview" for r in unique),
        "release_approved": False,  # This validator has no authority to issue release approvals.
        "warning": "Validation does not authenticate engine-produced records or grant publication rights.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TRISYNORA v1 evidence validator (read-only)")
    parser.add_argument("bundle", help="JSON array of evidence records")
    parser.add_argument("--evidence-root", type=Path, help="verify artifact/report bytes under this root")
    args = parser.parse_args(argv)
    try:
        payload = json.loads(Path(args.bundle).read_text(encoding="utf-8"))
        output = validate_bundle(payload, evidence_root=args.evidence_root)
    except (ContractError, OSError, ValueError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
