"""唯讀 H3 Take 匯出副本 → TRISYNORA v1 證據 ledger。

取代 TRISYNORA `tools/h3_take_source_gate.py` 與 `tools/h3_take_exchange_bridge.py` 中
「消費端」的通用必要功能：核對 Take 收據與真實 MP4 位元組、以真實 ffprobe/ffmpeg 解碼、
並以既有 `trisynora/v1` 契約寫入 ledger。

H3 專屬的「產生」邏輯（從 ProjectStore 匯出快照與影片）仍由 minimax-H3 的
`backend/native_take_export.py` 負責，這裡不重複實作、也不寫入 H3。
本模組不呼叫 H3／ComfyUI／PointFlux，不授權 receiver，不證明 H3 真實渲染或匯出者身分：
快照是操作員提供的未簽章本機資料。
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any

from .._files import read_local
from ..ledger import EvidenceLedger
from ..media_probe import MAX_VIDEO_BYTES, MediaProbeUnavailable, probe_mp4
from ..validator import ContractError, validate_bundle

_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_REC_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$")
_SHA256 = re.compile(r"^[a-fA-F0-9]{64}$")
_H3_PACKS = frozenset({"h3-short-drama", "h3-music-video", "h3-native-shot"})
MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024


def _strict_pairs(pairs: list[tuple[str, object]]) -> dict:
    out: dict = {}
    for key, value in pairs:
        if key in out:
            raise ContractError("DUPLICATE_JSON_KEY")
        out[key] = value
    return out


def _unique(matches: list, code: str) -> dict:
    if len(matches) != 1:
        raise ContractError(code)
    return matches[0]


def check_take_receipt(snapshot: dict, *, h3_project_id: str, take_id: str,
                       media_name: str, media_sha256: str) -> str:
    """核對 H3 快照內的 Take 收據與實際媒體位元組一致；回傳 H3 prompt id。"""
    if snapshot.get("id") != h3_project_id:
        raise ContractError("PROJECT_ID_MISMATCH")
    production = snapshot.get("production_v1")
    if not isinstance(production, dict):
        raise ContractError("PRODUCTION_RECORD_MISSING")
    takes = production.get("takes")
    if not isinstance(takes, list):
        raise ContractError("TAKES_MISSING")
    take = _unique([t for t in takes if isinstance(t, dict) and t.get("id") == take_id],
                   "TAKE_MISSING_OR_AMBIGUOUS")
    prompt = take.get("h3_prompt_id")
    if not isinstance(prompt, str) or not _ID.fullmatch(prompt):
        raise ContractError("TAKE_PROMPT_RECEIPT_MISSING")
    runtime = production.get("workflow_runtime_v1")
    runs = runtime.get("runs") if isinstance(runtime, dict) else None
    if not isinstance(runs, list):
        raise ContractError("H3_RUNTIME_RECEIPT_MISSING_OR_AMBIGUOUS")
    _unique([r for r in runs if isinstance(r, dict) and r.get("prompt_id") == prompt
             and r.get("pack_id") in _H3_PACKS], "H3_RUNTIME_RECEIPT_MISSING_OR_AMBIGUOUS")
    history = production.get("generation_history")
    if not isinstance(history, list):
        raise ContractError("H3_RENDER_HISTORY_NOT_COMPLETE_OR_AMBIGUOUS")
    _unique([h for h in history if isinstance(h, dict) and h.get("prompt_id") == prompt
             and h.get("status") == "complete"], "H3_RENDER_HISTORY_NOT_COMPLETE_OR_AMBIGUOUS")
    asset = take.get("asset")
    if (not isinstance(asset, dict) or asset.get("type") != "output"
            or asset.get("filename") != media_name):
        raise ContractError("TAKE_OUTPUT_FILENAME_NOT_BOUND")
    expected = take.get("source_media_sha256")
    if not isinstance(expected, str) or not _SHA256.fullmatch(expected):
        raise ContractError("TAKE_MEDIA_DIGEST_MISSING")
    if expected.lower() != media_sha256:
        raise ContractError("TAKE_MEDIA_DIGEST_MISMATCH")
    return prompt


def _recorded_at(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ContractError("produced_at must be an ISO 8601 datetime") from exc
    if parsed.utcoffset() is None:
        raise ContractError("produced_at requires UTC offset")
    return value


def make_bundle(*, evidence_root: str | Path, project_file: str, video: str,
                h3_project_id: str, take_id: str, tenant_id: str, project_id: str,
                produced_at: str) -> list[dict[str, Any]]:
    """把一份已匯出的 Take 副本轉成 ProjectSpec／ShotSpec／ArtifactRecord。

    不產生 EvaluationRecord、HumanReview 或任何核准；`rights.*` 一律為 False。
    """
    for label, value in (("h3 project id", h3_project_id), ("take id", take_id)):
        if not isinstance(value, str) or not _ID.fullmatch(value):
            raise ContractError(f"invalid {label}")
    for label, value in (("tenant_id", tenant_id), ("project_id", project_id)):
        if not isinstance(value, str) or not _REC_ID.fullmatch(value):
            raise ContractError(f"invalid {label}")
    produced_at = _recorded_at(produced_at)
    root_path = Path(evidence_root)
    if root_path.is_symlink() or not root_path.is_dir():
        raise ContractError("evidence root must be a real directory")
    root = root_path.resolve(strict=True)

    snap_path, snap_rel, snap_sha, _ = read_local(root, project_file, max_bytes=MAX_SNAPSHOT_BYTES)
    media_path, media_rel, media_sha, media_size = read_local(root, video)
    if snap_path == media_path or media_path.suffix.lower() != ".mp4":
        raise ContractError("EXPECTED_SEPARATE_MP4")
    if media_size > MAX_VIDEO_BYTES:
        raise ContractError("MEDIA_EXCEEDS_PILOT_128_MIB")
    snapshot_bytes = snap_path.read_bytes()
    if hashlib.sha256(snapshot_bytes).hexdigest() != snap_sha:
        raise ContractError("SNAPSHOT_CHANGED_DURING_CHECK")
    try:
        snapshot = json.loads(
            snapshot_bytes.decode("utf-8"), object_pairs_hook=_strict_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ContractError("NONFINITE_JSON")))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError("INVALID_SNAPSHOT_JSON") from exc
    if not isinstance(snapshot, dict):
        raise ContractError("INVALID_SNAPSHOT_JSON")

    prompt = check_take_receipt(snapshot, h3_project_id=h3_project_id, take_id=take_id,
                                media_name=media_path.name, media_sha256=media_sha)
    # 時基與編碼由真實媒體讀出；工具缺失會拋出 MediaProbeUnavailable（不寫入任何證據）。
    media = probe_mp4(media_path)
    after_media = hashlib.sha256()
    with media_path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            after_media.update(block)
    if (after_media.hexdigest() != media_sha
            or hashlib.sha256(snap_path.read_bytes()).hexdigest() != snap_sha):
        raise ContractError("EXPORTED_SOURCE_CHANGED_DURING_PREFLIGHT")

    key = hashlib.sha256("\0".join(
        (tenant_id, project_id, h3_project_id, take_id, snap_sha, media_sha)).encode()).hexdigest()[:32]

    def base(kind: str, idx: str, reference: str, sha: str, producer: str) -> dict[str, Any]:
        return {
            "schema_version": "trisynora/v1", "record_type": kind,
            "record_id": f"h3take.{key}.{idx}", "event_id": f"h3take.{key}.event_{idx}",
            "tenant_id": tenant_id, "project_id": project_id,
            "produced_at": produced_at, "producer": producer,
            "producer_version": "h3-take-import/v1",
            "source": {"system": "h3-export-copy", "reference": reference, "sha256": sha},
        }

    proj = base("ProjectSpec", "project", snap_rel, snap_sha, "operator-import") | {
        "name": f"H3 project {h3_project_id}",
        "delivery": {"width": media["width"], "height": media["height"],
                     "fps_num": media["fps_num"], "fps_den": media["fps_den"]},
        "rights": {"reuse_allowed": False, "cross_tenant_learning_allowed": False,
                   "external_training_allowed": False},
    }
    shot = base("ShotSpec", "shot", snap_rel, snap_sha, "operator-import") | {
        "project_spec_record_id": proj["record_id"], "shot_id": take_id,
        "duration_frames": media["frame_count"],
        "fps_num": media["fps_num"], "fps_den": media["fps_den"],
        "intent": f"H3 take {take_id} (prompt {prompt})",
        "acceptance_criteria": ["h3-take-receipt-matches-media-bytes", "real-mp4-decode"],
    }
    art = base("ArtifactRecord", "artifact", media_rel, media_sha, "h3-take-import") | {
        "shot_record_id": shot["record_id"],
        "artifact": {"relative_path": media_rel, "sha256": media_sha, "mime": "video/mp4",
                     "byte_size": media_size, "width": media["width"], "height": media["height"],
                     "fps_num": media["fps_num"], "fps_den": media["fps_den"],
                     "frame_count": media["frame_count"]},
        "input_digests": [snap_sha],
    }
    bundle = [proj, shot, art]
    validate_bundle(bundle, evidence_root=root)
    return bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import an exported H3 Take copy into the TRISYNORA v1 evidence ledger")
    for field in ("evidence-root", "project-file", "video", "h3-project-id", "take-id",
                  "tenant-id", "project-id", "produced-at", "ledger"):
        parser.add_argument("--" + field, required=True)
    args = parser.parse_args(argv)
    try:
        bundle = make_bundle(evidence_root=args.evidence_root, project_file=args.project_file,
                             video=args.video, h3_project_id=args.h3_project_id,
                             take_id=args.take_id, tenant_id=args.tenant_id,
                             project_id=args.project_id, produced_at=args.produced_at)
        with EvidenceLedger(args.ledger) as ledger:
            result = ledger.append(bundle, evidence_root=args.evidence_root)
        result["receiver_authorized"] = False
        result["pointflux_imported"] = False
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except MediaProbeUnavailable as exc:
        print(json.dumps({"ok": False, "status": "not_verified", "error": str(exc)},
                         ensure_ascii=False, sort_keys=True))
        return 4
    except (ContractError, OSError, ValueError, TypeError, sqlite3.Error) as exc:
        print(json.dumps({"ok": False, "status": "blocked", "error": str(exc)},
                         ensure_ascii=False, sort_keys=True))
        return 1


if __name__ == "__main__":
    sys.exit(main())
