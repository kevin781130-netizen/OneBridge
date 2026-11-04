"""PointFlux reference-only shot exchange -> TRISYNORA v1 *metadata* records.

自 TRISYNORA `feature/pointflux-reference-evidence-adapter-20261018` 遷入。
Read-only with respect to PointFlux/H3. This does NOT create video, evaluations,
human approval, identity authentication, or a native H3 binding. Input native
Scene IR files must be independently named by the operator and byte-verified.
輸出僅為 ProjectSpec／ShotSpec 中繼資料檔（不寫入 ledger、不產生 ArtifactRecord／EvaluationRecord）。
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
from typing import Any

from .._files import read_local, strict_json
from ..validator import ContractError, validate_bundle

ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
KIND = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")
PATH = re.compile(r"^[A-Za-z0-9_./-]{1,500}$")
MAX_BYTES = 1_048_576
ASSET_ROLES = frozenset({"source", "target", "previs", "video", "audio", "model", "other"})


class ReferenceError(ContractError):
    """An untrusted or incompatible source reference was refused."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ReferenceError(reason)


def _keys(data: Any, required: set[str], optional: set[str], name: str) -> dict:
    _require(isinstance(data, dict), f"{name}: object required")
    _require(required <= data.keys() and data.keys() <= required | optional, f"{name}: invalid keys")
    return data


def _id(value: Any, name: str) -> str:
    _require(isinstance(value, str) and ID.fullmatch(value) is not None, f"{name}: invalid ID")
    return value


def _sha(value: Any, name: str) -> str:
    _require(isinstance(value, str) and DIGEST.fullmatch(value) is not None, f"{name}: invalid SHA-256")
    return value


def _integer(value: Any, low: int, high: int, name: str) -> int:
    _require(type(value) is int and low <= value <= high, f"{name}: out of bounds")
    return value


def _number(value: Any, low: float, high: float, name: str) -> None:
    if type(value) not in (int, float):
        raise ReferenceError(f"{name}: invalid numeric value")
    try:
        _require(math.isfinite(value) and low <= value <= high,
                 f"{name}: invalid numeric value")
    except OverflowError as exc:
        raise ReferenceError(f"{name}: invalid numeric value") from exc


def _relpath(value: Any) -> PurePosixPath:
    _require(isinstance(value, str) and PATH.fullmatch(value) is not None, "unsafe relative asset/scene path")
    parts = value.split("/")
    _require(".." not in value and not any(part in ("", ".", "..") for part in parts) and not value.startswith("/"),
             "unsafe relative asset/scene path")
    return PurePosixPath(value)


def _read_json(raw: bytes) -> Any:
    try:
        return strict_json(raw, invalid="invalid UTF-8 JSON")
    except ContractError as exc:
        raise ReferenceError(str(exc)) from exc


def _source_bytes(root: Path, relative: str) -> bytes:
    _relpath(relative)
    try:
        source, _, _, _ = read_local(root, relative, max_bytes=MAX_BYTES)
        raw = source.read_bytes()
    except ContractError as exc:
        raise ReferenceError("scene is unavailable or outside the approved source root") from exc
    except OSError as exc:
        raise ReferenceError("scene is unavailable or outside the approved source root") from exc
    _require(len(raw) <= MAX_BYTES, "scene is too large")
    return raw


def _shot(shot: Any) -> dict:
    s = _keys(shot, {"shotId", "name", "origin", "durationFrames", "fps", "camera", "output",
                     "timeline", "assets", "handoff"}, set(), "shot")
    _id(s["shotId"], "shotId")
    _require(isinstance(s["name"], str) and 1 <= len(s["name"]) <= 120, "shot: invalid name")
    origin = _keys(s["origin"], {"engine", "sourceVersion", "sourceSha256"}, set(), "origin")
    _require(origin["engine"] == "pointflux" and origin["sourceVersion"] == "1.4", "unsupported source")
    _sha(origin["sourceSha256"], "origin.sourceSha256")
    fps = _integer(s["fps"], 1, 120, "fps")
    frames = _integer(s["durationFrames"], 1, min(7200, 60 * fps), "durationFrames")
    cam = _keys(s["camera"], {"position", "target", "fovDegrees"}, set(), "camera")
    for field in ("position", "target"):
        v = cam[field]
        _require(isinstance(v, list) and len(v) == 3, f"camera.{field}: vec3 required")
        for x in v:
            _number(x, -10000, 10000, f"camera.{field}")
    _number(cam["fovDegrees"], 1, 179, "fovDegrees")
    _require(1 < cam["fovDegrees"] < 179, "fovDegrees must be strictly between 1 and 179")
    out = _keys(s["output"], {"width", "height"}, set(), "output")
    _integer(out["width"], 64, 8192, "width")
    _integer(out["height"], 64, 8192, "height")
    _require(isinstance(s["timeline"], list) and len(s["timeline"]) <= 500, "timeline invalid")
    events = set()
    for entry in s["timeline"]:
        e = _keys(entry, {"eventId", "kind", "atFrame", "durationFrames"}, set(), "event")
        eid = _id(e["eventId"], "eventId")
        _require(eid not in events, "duplicate eventId")
        events.add(eid)
        _require(isinstance(e["kind"], str) and KIND.fullmatch(e["kind"]) is not None,
                 "invalid event kind")
        start = _integer(e["atFrame"], 0, frames, "atFrame")
        duration = _integer(e["durationFrames"], 0, frames, "event duration")
        _require(start + duration <= frames, "event exceeds shot duration")
    _require(isinstance(s["assets"], list) and len(s["assets"]) <= 100, "assets invalid")
    for asset in s["assets"]:
        a = _keys(asset, {"role", "uri"}, {"sha256"}, "asset")
        _require(a["role"] in ASSET_ROLES, "invalid asset role")
        _relpath(a["uri"])
        if "sha256" in a:
            _sha(a["sha256"], "asset.sha256")
    handoff = _keys(s["handoff"], {"mode", "requiresExplicitBind"}, set(), "handoff")
    _require(handoff["mode"] == "reference-only" and handoff["requiresExplicitBind"] is True,
             "exchange must remain reference-only with explicit binding")
    return s



def _check_native_projection(shot: dict, scene: dict) -> None:
    """Match every advertised PF -> H3 field to independently hashed Scene IR.

    PointFlux's v1.4 semantic validator preserves the original Scene object;
    its exporter copies camera/output, maps events using JS Math.round, and
    normalizes asset URIs by stripping one initial slash. This *does not*
    independently reproduce the full PointFlux Scene IR semantic validator.
    """
    cam = scene.get("camera")
    output = scene.get("output")
    events = scene.get("timeline")
    targets = scene.get("targets")
    origin = scene.get("source")
    _require(isinstance(cam, dict) and isinstance(output, dict) and
             isinstance(events, list) and isinstance(targets, dict) and
             isinstance(origin, dict), "native Scene IR lacks exported fields")
    _require(shot["camera"] == {"position": cam.get("position"),
                                  "target": cam.get("target"), "fovDegrees": cam.get("fov")},
             "camera does not match source Scene IR")
    _require(shot["fps"] == output.get("fps") and
             shot["output"] == {"width": output.get("width"), "height": output.get("height")},
             "output/FPS do not match source Scene IR")
    expected = []
    for i, event in enumerate(events):
        _require(isinstance(event, dict) and isinstance(event.get("at"), (float, int)) and
                 not isinstance(event.get("at"), bool), "invalid native event")
        at = event["at"]
        duration = event.get("duration", 0)
        _require(type(duration) in (int, float) and math.isfinite(duration) and duration >= 0
                 and math.isfinite(at) and at >= 0, "invalid native event timing")
        expected.append({"eventId": event.get("id") or f"event-{i+1}",
                         "kind": event.get("effect"),
                         "atFrame": math.floor(at * shot["fps"] + 0.5),
                         "durationFrames": math.floor(duration * shot["fps"] + 0.5)})
    _require(shot["timeline"] == expected, "timeline does not match source Scene IR")
    _require(shot["durationFrames"] >= max([shot["fps"]] +
             [e["atFrame"] + e["durationFrames"] for e in expected]),
             "source timeline exceeds shot duration")

    def as_asset(asset, role):
        _require(isinstance(asset, dict) and isinstance(asset.get("uri"), str),
                 "invalid native asset")
        uri = asset["uri"]
        uri = uri[1:] if uri.startswith("/") else uri
        _relpath(uri)
        return {"role": role, "uri": uri,
                **({"sha256": asset["sha256"]} if asset.get("sha256") else {})}

    expected_assets = [as_asset(origin, "source")]
    expected_assets.extend(as_asset(asset, "target") for asset in targets.values())
    _require(shot["assets"] == expected_assets, "assets do not match source Scene IR")

def build_records(exchange_raw: bytes, *, tenant_id: str, project_name: str,
                  produced_at: str, scene_root: Path, scene_paths: dict[str, str]) -> list[dict[str, Any]]:
    """Create only ProjectSpec/ShotSpec, not ArtifactRecord or EvaluationRecord."""
    _require(len(exchange_raw) <= MAX_BYTES, "exchange exceeds 1 MiB")
    _require(isinstance(tenant_id, str) and 1 <= len(tenant_id) <= 128 and
             re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{1,127}", tenant_id) is not None,
             "invalid tenant ID")
    _require(isinstance(project_name, str) and 1 <= len(project_name) <= 4096, "project name required")
    try:
        when = datetime.fromisoformat(produced_at.replace("Z", "+00:00"))
        _require(when.utcoffset() is not None, "timestamp requires timezone")
    except (ValueError, AttributeError) as exc:
        raise ReferenceError("invalid produced_at timestamp") from exc
    exchange = _keys(_read_json(exchange_raw), {"format", "projectId", "shots"}, set(), "exchange")
    _require(exchange["format"] == "pointflux-h3-shot-exchange/v1", "unsupported exchange format")
    project_id = _id(exchange["projectId"], "projectId")
    _require(len(project_id) >= 2, "projectId is too short for TRISYNORA v1")
    shots = exchange["shots"]
    _require(isinstance(shots, list) and 1 <= len(shots) <= 30, "shots must be 1..30")
    checked = [_shot(shot) for shot in shots]
    ids = [shot["shotId"] for shot in checked]
    _require(all(len(shot_id) >= 2 for shot_id in ids), "shotId is too short for TRISYNORA v1")
    _require(len(ids) == len(set(ids)), "duplicate shot ID")
    _require(type(scene_paths) is dict and set(scene_paths) == set(ids), "scene map must name every shot exactly")
    delivery = (checked[0]["output"]["width"], checked[0]["output"]["height"], checked[0]["fps"])
    _require(all((s["output"]["width"], s["output"]["height"], s["fps"]) == delivery for s in checked),
             "mixed delivery profiles not supported by the v1 evidence adapter")
    _require(not Path(scene_root).is_symlink(), "scene root symlinks forbidden")
    try:
        root = Path(scene_root).resolve(strict=True)
    except OSError as exc:
        raise ReferenceError("invalid scene root") from exc
    _require(root.is_dir(), "invalid scene root")
    exchange_sha = hashlib.sha256(exchange_raw).hexdigest()
    namespace = hashlib.sha256((tenant_id + "\0" + project_id + "\0" + exchange_sha).encode()).hexdigest()[:24]
    prefix = "pfref." + namespace

    def base(kind: str, key: str, system: str, reference: str, digest: str) -> dict[str, Any]:
        return {"schema_version": "trisynora/v1", "record_type": kind,
                "record_id": prefix + "." + key, "event_id": prefix + ".event_" + key,
                "tenant_id": tenant_id, "project_id": project_id,
                "produced_at": produced_at, "producer": "operator-import",
                "producer_version": "pointflux-reference-adapter/v1",
                "source": {"system": system, "reference": reference, "sha256": digest}}

    project = base("ProjectSpec", "project", "pointflux-h3-shot-exchange", "exchange/manifest", exchange_sha)
    project.update(name=project_name,
                   delivery={"width": delivery[0], "height": delivery[1],
                             "fps_num": delivery[2], "fps_den": 1},
                   rights={"reuse_allowed": False, "cross_tenant_learning_allowed": False,
                           "external_training_allowed": False})
    records = [project]
    for i, shot in enumerate(checked):
        raw = _source_bytes(root, scene_paths[shot["shotId"]])
        _require(hashlib.sha256(raw).hexdigest() == shot["origin"]["sourceSha256"],
                 "source Scene IR digest mismatch: " + shot["shotId"])
        scene = _read_json(raw)
        _require(isinstance(scene, dict) and scene.get("version") == "1.4", "unsupported native Scene IR")
        _check_native_projection(shot, scene)
        rec = base("ShotSpec", f"shot_{i+1}", "pointflux", "shot/" + shot["shotId"],
                   shot["origin"]["sourceSha256"])
        rec.update(project_spec_record_id=project["record_id"], shot_id=shot["shotId"],
                   duration_frames=shot["durationFrames"], fps_num=shot["fps"], fps_den=1,
                   intent="Reference-only: " + shot["name"],
                   acceptance_criteria=["source_scene_digest_matches_local_bytes"])
        records.append(rec)
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only PointFlux reference -> TRISYNORA metadata")
    for key in ("exchange", "scene-root", "scene-map", "tenant-id", "project-name", "produced-at", "out"):
        parser.add_argument("--" + key, required=True)
    args = parser.parse_args(argv)
    try:
        raw = Path(args.exchange).read_bytes()
        _require(len(raw) <= MAX_BYTES, "exchange exceeds 1 MiB")
        mapping_raw = Path(args.scene_map).read_bytes()
        _require(len(mapping_raw) <= 65536, "scene map exceeds 64 KiB")
        mapping = _read_json(mapping_raw)
        records = build_records(raw, tenant_id=args.tenant_id, project_name=args.project_name,
                                produced_at=args.produced_at, scene_root=Path(args.scene_root),
                                scene_paths=mapping)
        validate_bundle(records)  # 既有已發佈 v1 schema 與 lineage；不修改 schema
        output = Path(args.out)
        _require(not output.is_symlink(), "output symlinks forbidden")
        _require(not output.resolve().is_relative_to(Path(args.scene_root).resolve()),
                 "output cannot be inside source project")
        with output.open("x", encoding="utf-8") as stream:
            json.dump(records, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
        print(json.dumps({"ok": True, "status": "reference-metadata-only", "records": len(records),
                          "nativeImported": False, "renderVerified": False,
                          "humanApproved": False, "releaseApproved": False}))
        return 0
    except (ContractError, OSError, ValueError, TypeError) as exc:
        print(json.dumps({"ok": False, "status": "rejected", "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
