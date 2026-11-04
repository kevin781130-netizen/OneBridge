"""Read-only PointFlux *temporal* Judge importer for TRISYNORA v1.

It never invokes PointFlux, assumes an engine identity, or grants human/release
approval. The existing local report and video bytes are mandatory inputs.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from typing import Any

from ..ledger import EvidenceLedger
from ..validator import ContractError, validate_bundle

_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$")
_MAX_REPORT = 32 * 1024 * 1024


def _read_local(root: Path, path: str, *, max_bytes: int | None = None) -> tuple[Path, str, str, int]:
    """Resolve only evidence-root relative paths and reject symlink escapes."""
    raw = Path(path)
    if raw.is_absolute() or not path or "\\" in path or any(p in ("..", ".") for p in raw.parts):
        raise ContractError("absolute/traversal evidence paths are forbidden")
    try:
        resolved = (root / raw).resolve(strict=True)
        relative = resolved.relative_to(root).as_posix()
    except (ValueError, OSError) as exc:
        raise ContractError("evidence file missing or outside approved root") from exc
    if not resolved.is_file() or raw.as_posix() != relative:
        raise ContractError("evidence must be a regular file with canonical relative path")
    if max_bytes is not None and resolved.stat().st_size > max_bytes:
        raise ContractError("evidence JSON exceeds size limit")
    sha = hashlib.sha256()
    n = 0
    with resolved.open("rb") as inp:
        for block in iter(lambda: inp.read(1024 * 1024), b""):
            sha.update(block)
            n += len(block)
            if max_bytes is not None and n > max_bytes:
                raise ContractError("evidence JSON exceeds size limit")
    return resolved, relative, sha.hexdigest(), n


def _read_json(path: Path) -> dict[str, Any]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite JSON")))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ContractError("invalid evidence JSON") from exc
    if not isinstance(doc, dict):
        raise ContractError("evidence JSON must be an object")
    return doc


def _positive(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
        raise ContractError(f"invalid positive {name}")
    return float(value)


def _int(value: Any, name: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ContractError(f"invalid {name}")
    return value


def _id(value: str, field: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ContractError(f"invalid {field}")
    return value


def _recorded_at(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ContractError("produced_at must be an ISO 8601 datetime") from exc
    if parsed.utcoffset() is None:
        raise ContractError("produced_at requires UTC offset")
    return value


def _required_criteria(acceptance: dict[str, Any]) -> tuple[bool, list[dict[str, Any]]]:
    if acceptance.get("version") != "1" or type(acceptance.get("accepted")) is not bool:
        raise ContractError("PointFlux acceptance v1 is required")
    all_criteria = acceptance.get("criteria")
    if not isinstance(all_criteria, list) or not (1 <= len(all_criteria) <= 128):
        raise ContractError("PointFlux acceptance criteria must be nonempty and bounded")
    required: list[dict[str, Any]] = []
    names: set[str] = set()
    for c in all_criteria:
        if not isinstance(c, dict) or type(c.get("required")) is not bool or type(c.get("pass")) is not bool:
            raise ContractError("malformed PointFlux criterion")
        name = _id(c.get("id"), "criterion id")
        if name in names:
            raise ContractError("duplicate PointFlux criterion")
        names.add(name)
        if c["required"]:
            row = {"name": name, "status": "passed" if c["pass"] else "failed"}
            for source, dest in (("observed", "actual"), ("threshold", "threshold")):
                n = c.get(source)
                if n is None:
                    row[dest] = None
                elif isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n):
                    raise ContractError(f"non-numeric {source}")
                else:
                    row[dest] = n
            required.append(row)
    summary = acceptance.get("summary")
    if not isinstance(summary, dict):
        raise ContractError("PointFlux acceptance summary is missing")
    checks = {"required": len(required),
              "requiredPassed": sum(c["status"] == "passed" for c in required),
              "total": len(all_criteria),
              "totalPassed": sum(c["pass"] for c in all_criteria)}
    if any(type(summary.get(k)) is not int or summary[k] != v for k, v in checks.items()):
        raise ContractError("PointFlux acceptance summary contradicts criteria")
    for c in all_criteria:
        reason = c.get("reason")
        if c["pass"] and reason != "criterion-satisfied":
            raise ContractError("PointFlux criterion pass/reason inconsistent")
        if not c["pass"] and reason not in ("criterion-not-satisfied", "missing-frame",
                                             "missing-reference", "missing-evidence", "non-numeric-evidence"):
            raise ContractError("PointFlux failed criterion reason inconsistent")
    accepted = acceptance["accepted"]
    if not required or accepted != all(x["status"] == "passed" for x in required):
        raise ContractError("PointFlux acceptance contradicts required criterion results")
    return accepted, required


def make_bundle(
    *, evidence_root: str | Path, render_manifest: str, judgement: str, artifact: str,
    tenant_id: str, project_id: str, shot_id: str, project_name: str,
    intent: str, produced_at: str,
) -> list[dict[str, Any]]:
    """Translate existing *real files*; do not claim they were authenticated."""
    root = Path(evidence_root).resolve(strict=True)
    tenant_id = _id(tenant_id, "tenant_id")
    project_id = _id(project_id, "project_id")
    shot_id = _id(shot_id, "shot_id")
    produced_at = _recorded_at(produced_at)
    if not isinstance(project_name, str) or not 1 <= len(project_name) <= 4096:
        raise ContractError("project_name required")
    if not isinstance(intent, str) or not 1 <= len(intent) <= 4096:
        raise ContractError("shot intent required")

    mp, mr, mh, _ = _read_local(root, render_manifest, max_bytes=_MAX_REPORT)
    jp, jr, jh, _ = _read_local(root, judgement, max_bytes=_MAX_REPORT)
    ap, ar, ah, ab = _read_local(root, artifact)
    if ap.parent != mp.parent or ap.suffix.lower() != ".mp4":
        raise ContractError("artifact must be an MP4 in the render-manifest directory")
    with ap.open("rb") as f:
        if len(f.read(12)) < 12:
            raise ContractError("MP4 file too short")
        f.seek(4)
        if f.read(4) != b"ftyp":
            raise ContractError("MP4 container header is missing")

    manifest = _read_json(mp)
    report = _read_json(jp)
    if manifest.get("manifestVersion") != "1" or report.get("version") != "1":
        raise ContractError("unsupported PointFlux manifest or temporal judgement version")
    scene_hash = manifest.get("sceneSha256")
    if not isinstance(scene_hash, str) or not _DIGEST.fullmatch(scene_hash) or report.get("sceneSha256") != scene_hash:
        raise ContractError("PointFlux render/judgement sceneSha256 mismatch")
    fps = _positive(manifest.get("fps"), "manifest fps")
    capture = report.get("capture")
    if not isinstance(capture, dict):
        raise ContractError("missing temporal capture")
    if _positive(capture.get("fps"), "judge fps") != fps:
        raise ContractError("render/judge FPS mismatch")
    w = _int(manifest.get("width"), "width", 16)
    h = _int(manifest.get("height"), "height", 16)
    count = _int(manifest.get("frameCount"), "frameCount", 1)
    start = _int(manifest.get("startFrame"), "startFrame")
    if _int(manifest.get("lastFrame"), "lastFrame") != start + count - 1:
        raise ContractError("render frame interval is inconsistent")
    cap = report.get("capture")
    if not isinstance(cap, dict):
        raise ContractError("missing temporal capture")
    sampled = cap.get("frames")
    if not isinstance(sampled, list) or not sampled or any(type(x) is not int or x < start or x >= start + count for x in sampled) or len(set(sampled)) != len(sampled):
        raise ContractError("judge frames are outside the rendered frame interval")
    pf_frames = report.get("frames")
    if not isinstance(pf_frames, list) or [x.get("frame") if isinstance(x, dict) else None for x in pf_frames] != sampled:
        raise ContractError("temporal report frame list mismatch")
    accepted, criteria = _required_criteria(report.get("acceptance") if isinstance(report.get("acceptance"), dict) else {})
    rational = Fraction(str(fps)).limit_denominator(1000000)
    if rational.numerator > 1000000 or rational.denominator > 1000000 or abs(float(rational) - fps) > 1e-8:
        raise ContractError("fps cannot be represented in v1 contract")
    # Identifiers derive from immutable observed bytes and scope, not wall-clock.
    key = hashlib.sha256("\0".join((tenant_id, project_id, shot_id, mh, jh, ah)).encode()).hexdigest()[:32]
    def base(kind: str, idx: str, source_ref: str, source_sha: str, producer: str) -> dict[str, Any]:
        return {
            "schema_version": "trisynora/v1", "record_type": kind,
            "record_id": f"pf.{key}.{idx}", "event_id": f"pf.{key}.event_{idx}",
            "tenant_id": tenant_id, "project_id": project_id,
            "produced_at": produced_at, "producer": producer,
            "producer_version": "pointflux-temporal-import/v1",
            "source": {"system": "pointflux-local-artifacts", "reference": source_ref, "sha256": source_sha},
        }
    proj = base("ProjectSpec", "project", mr, mh, "operator-import") | {
        "name": project_name,
        "delivery": {"width": w, "height": h, "fps_num": rational.numerator, "fps_den": rational.denominator},
        "rights": {"reuse_allowed": False, "cross_tenant_learning_allowed": False, "external_training_allowed": False},
    }
    shot = base("ShotSpec", "shot", mr, mh, "operator-import") | {
        "project_spec_record_id": proj["record_id"], "shot_id": shot_id, "duration_frames": count,
        "fps_num": rational.numerator, "fps_den": rational.denominator,
        "intent": intent, "acceptance_criteria": [c["name"] for c in criteria],
    }
    art = base("ArtifactRecord", "artifact", ar, ah, "pointflux-import") | {
        "shot_record_id": shot["record_id"],
        "artifact": {"relative_path": ar, "sha256": ah, "mime": "video/mp4", "byte_size": ab,
                     "width": w, "height": h, "fps_num": rational.numerator,
                     "fps_den": rational.denominator, "frame_count": count},
        "input_digests": [mh, scene_hash] if mh != scene_hash else [mh],
    }
    evaluation = base("EvaluationRecord", "evaluation", jr, jh, "pointflux-import") | {
        "artifact_record_id": art["record_id"], "artifact_sha256": ah,
        "evaluator": "pointflux-temporal", "evaluator_version": "1",
        "status": "passed" if accepted else "failed", "criteria": criteria,
        "report": {"relative_path": jr, "sha256": jh},
    }
    bundle = [proj, shot, art, evaluation]
    validate_bundle(bundle, evidence_root=root)
    return bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import existing PointFlux temporal judge + video into TRISYNORA")
    for field in ("evidence-root", "render-manifest", "judgement", "artifact", "tenant-id", "project-id", "shot-id", "project-name", "intent", "produced-at", "ledger"):
        parser.add_argument("--" + field, required=True)
    args = parser.parse_args(argv)
    try:
        bundle = make_bundle(evidence_root=args.evidence_root, render_manifest=args.render_manifest,
                             judgement=args.judgement, artifact=args.artifact, tenant_id=args.tenant_id,
                             project_id=args.project_id, shot_id=args.shot_id,
                             project_name=args.project_name, intent=args.intent, produced_at=args.produced_at)
        # Every artifact & report is verified again inside the ledger transaction.
        with EvidenceLedger(args.ledger) as ledger:
            result = ledger.append(bundle, evidence_root=args.evidence_root)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (ContractError, OSError, ValueError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 1


if __name__ == "__main__":
    sys.exit(main())