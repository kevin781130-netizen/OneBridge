"""H3 Take 匯出副本匯入：Take 收據綁定 + 真實 ffprobe/ffmpeg 解碼 + ledger 寫入。

需要 ffmpeg/ffprobe 的案例以真實編碼的 H.264 MP4 測試；環境缺少工具時該類案例會 skip
（回報為 NOT_RUN，不是 PASS）。快照內容為依 H3 ProjectStore 欄位建構的測試資料，
不代表真實 H3／ComfyUI 渲染。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from onebridge.cli import main
from onebridge.evidence_contracts.adapters.h3_take import check_take_receipt, make_bundle
from onebridge.evidence_contracts.ledger import EvidenceLedger
from onebridge.evidence_contracts.media_probe import (
    MediaProbeRejected, MediaProbeUnavailable, parse_probe, probe_mp4)
from onebridge.evidence_contracts.validator import ContractError

HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe 不可用：NOT_RUN")
SHA = lambda b: hashlib.sha256(b).hexdigest()


def _encode(path: Path, *extra: str, codec: str = "libx264", pix: str = "yuv420p",
            frames: int = 30, size: str = "320x180", rate: int = 30, audio: bool = False) -> None:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
           "-i", f"testsrc=size={size}:rate={rate}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:duration=1"]
    cmd += ["-frames:v", str(frames), "-c:v", codec, "-pix_fmt", pix]
    cmd += ["-c:a", "aac", "-shortest"] if audio else ["-an"]
    subprocess.run([*cmd, *extra, str(path)], check=True, timeout=60)


def _snapshot(media_sha: str, filename: str = "take001.mp4") -> dict:
    return {"id": "proj1", "production_v1": {
        "takes": [{"id": "take1", "h3_prompt_id": "job1", "source_media_sha256": media_sha,
                   "asset": {"type": "output", "filename": filename}}],
        "workflow_runtime_v1": {"runs": [{"pack_id": "h3-native-shot", "prompt_id": "job1"}]},
        "generation_history": [{"prompt_id": "job1", "status": "complete"}]}}


class Export:
    """模擬 H3 exporter 的輸出配置：project/snapshot.json 與 render/<mp4>。"""

    def __init__(self, root: Path, media: bytes):
        self.root = root
        (root / "project").mkdir(parents=True)
        (root / "render").mkdir()
        self.video = root / "render" / "take001.mp4"
        self.video.write_bytes(media)
        self.snapshot = _snapshot(SHA(media))
        self.save()

    def save(self) -> None:
        (self.root / "project" / "snapshot.json").write_text(json.dumps(self.snapshot), encoding="utf-8")

    def opts(self, **over) -> dict:
        base = dict(evidence_root=self.root, project_file="project/snapshot.json",
                    video="render/take001.mp4", h3_project_id="proj1", take_id="take1",
                    tenant_id="org1", project_id="campaign1", produced_at="2026-11-04T00:00:00Z")
        return base | over

    def argv(self, ledger: Path) -> list[str]:
        o = self.opts()
        out = ["evidence-import-h3-take", "--ledger", str(ledger)]
        for key in ("evidence_root", "project_file", "video", "h3_project_id", "take_id",
                    "tenant_id", "project_id", "produced_at"):
            out += ["--" + key.replace("_", "-"), str(o[key])]
        return out


@pytest.fixture()
def real(tmp_path: Path) -> Export:
    src = tmp_path / "src.mp4"
    _encode(src)
    return Export(tmp_path / "export", src.read_bytes())


@pytest.fixture()
def fake(tmp_path: Path) -> Export:
    """非 MP4 位元組：收據檢查在探測之前，因此可在無 ffmpeg 時驗證收據類負向案例。"""
    return Export(tmp_path / "export", b"synthetic-not-valid-mp4")


def _cli(capsys, argv: list[str]) -> tuple[int, dict]:
    code = main(argv)
    return code, json.loads(capsys.readouterr().out)


# ───────── 正向 ─────────

@needs_ffmpeg
def test_real_mp4_positive_import_records_probed_facts(real: Export, tmp_path, capsys):
    ledger = tmp_path / "l.sqlite"
    code, out = _cli(capsys, real.argv(ledger))
    assert code == 0 and out["ok"] is True and out["inserted"] == 3
    assert out["receiver_authorized"] is False and out["pointflux_imported"] is False
    assert out["release_approved"] is False
    with EvidenceLedger(ledger) as db:
        recs = {r["record_type"]: r for r in db.read("org1", "campaign1")}
    assert set(recs) == {"ProjectSpec", "ShotSpec", "ArtifactRecord"}  # 不含 Evaluation／HumanReview
    art = recs["ArtifactRecord"]["artifact"]
    assert (art["width"], art["height"], art["fps_num"], art["fps_den"], art["frame_count"]) == (320, 180, 30, 1, 30)
    assert art["sha256"] == SHA(real.video.read_bytes()) and art["mime"] == "video/mp4"
    assert recs["ProjectSpec"]["rights"] == {"reuse_allowed": False, "cross_tenant_learning_allowed": False,
                                             "external_training_allowed": False}


@needs_ffmpeg
def test_replay_is_idempotent(real: Export, tmp_path, capsys):
    ledger = tmp_path / "l.sqlite"
    assert _cli(capsys, real.argv(ledger))[0] == 0
    code, out = _cli(capsys, real.argv(ledger))
    assert code == 0 and out["inserted"] == 0 and out["replayed"] == 3


@needs_ffmpeg
def test_real_media_probe_reports_timebase_from_media(real: Export):
    meta = probe_mp4(real.video)
    assert meta == {"codec": "h264", "pixel_format": "yuv420p", "width": 320, "height": 180,
                    "fps_num": 30, "fps_den": 1, "frame_count": 30}


@pytest.mark.skipif(not os.environ.get("H3_NATIVE_TAKE_EXPORT") or not HAVE_FFMPEG,
                    reason="設定 H3_NATIVE_TAKE_EXPORT=<minimax-H3 backend/native_take_export.py> 才執行：NOT_RUN")
def test_real_h3_exporter_output_is_accepted(tmp_path, capsys):
    """以 minimax-H3 真實 exporter 產生匯出副本，再由 OneBridge 匯入（跨專案相容性）。"""
    media_root, projects = tmp_path / "comfy", tmp_path / "projects"
    media_root.mkdir(), projects.mkdir()
    _encode(media_root / "take001.mp4")
    (projects / "proj1.json").write_text(
        json.dumps(_snapshot(SHA((media_root / "take001.mp4").read_bytes()))), encoding="utf-8")
    out_dir = tmp_path / "out"
    done = subprocess.run([sys.executable, os.environ["H3_NATIVE_TAKE_EXPORT"],
                           "--project-root", str(projects), "--project-id", "proj1", "--take-id", "take1",
                           "--media-root", str(media_root), "--video", "take001.mp4", "--out", str(out_dir)],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
    ex = Export.__new__(Export)  # 不重建檔案，直接沿用 exporter 的輸出目錄
    ex.root = out_dir
    code, out = _cli(capsys, Export.argv(ex, tmp_path / "l.sqlite"))
    assert code == 0 and out["inserted"] == 3


# ───────── 負向：Take 收據與路徑（不需 ffmpeg） ─────────

def _blocked(fake: Export, match: str, **over):
    with pytest.raises(ContractError, match=match):
        make_bundle(**fake.opts(**over))


def test_media_tamper_after_export_is_blocked(fake: Export):
    fake.video.write_bytes(b"changed-after-export")
    _blocked(fake, "TAKE_MEDIA_DIGEST_MISMATCH")


def test_missing_output_binding_blocked(fake: Export):
    del fake.snapshot["production_v1"]["takes"][0]["asset"]
    fake.save()
    _blocked(fake, "TAKE_OUTPUT_FILENAME_NOT_BOUND")


def test_wrong_output_filename_blocked(fake: Export):
    fake.snapshot["production_v1"]["takes"][0]["asset"]["filename"] = "other.mp4"
    fake.save()
    _blocked(fake, "TAKE_OUTPUT_FILENAME_NOT_BOUND")


def test_missing_digest_blocked(fake: Export):
    del fake.snapshot["production_v1"]["takes"][0]["source_media_sha256"]
    fake.save()
    _blocked(fake, "TAKE_MEDIA_DIGEST_MISSING")


def test_duplicate_runtime_receipt_blocked(fake: Export):
    runs = fake.snapshot["production_v1"]["workflow_runtime_v1"]["runs"]
    runs.append(dict(runs[0]))
    fake.save()
    _blocked(fake, "AMBIGUOUS")


def test_unknown_pack_blocked(fake: Export):
    fake.snapshot["production_v1"]["workflow_runtime_v1"]["runs"][0]["pack_id"] = "other-pack"
    fake.save()
    _blocked(fake, "H3_RUNTIME_RECEIPT")


def test_incomplete_render_history_blocked(fake: Export):
    fake.snapshot["production_v1"]["generation_history"][0]["status"] = "running"
    fake.save()
    _blocked(fake, "NOT_COMPLETE")


def test_duplicate_take_blocked(fake: Export):
    takes = fake.snapshot["production_v1"]["takes"]
    takes.append(dict(takes[0]))
    fake.save()
    _blocked(fake, "TAKE_MISSING_OR_AMBIGUOUS")


def test_project_id_mismatch_blocked(fake: Export):
    _blocked(fake, "PROJECT_ID_MISMATCH", h3_project_id="proj2")


def test_duplicate_json_key_blocked(fake: Export):
    (fake.root / "project" / "snapshot.json").write_text('{"id":"proj1","id":"proj1"}')
    _blocked(fake, "DUPLICATE_JSON_KEY")


def test_nonfinite_json_blocked(fake: Export):
    (fake.root / "project" / "snapshot.json").write_text('{"id":"proj1","x":NaN}')
    _blocked(fake, "NONFINITE_JSON")


@pytest.mark.parametrize("bad", ["../outside.json", "/etc/passwd", "project\\snapshot.json"])
def test_unsafe_snapshot_paths_blocked(fake: Export, bad):
    _blocked(fake, "forbidden", project_file=bad)


def test_snapshot_and_media_must_be_distinct_mp4(fake: Export):
    _blocked(fake, "EXPECTED_SEPARATE_MP4", video="project/snapshot.json")


@pytest.mark.skipif(os.name == "nt", reason="Windows 建立 symlink 需特殊權限：NOT_RUN")
def test_symlinked_video_blocked(fake: Export, tmp_path):
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(fake.video.read_bytes())  # 位元組完全正確，但是 symlink
    fake.video.unlink()
    fake.video.symlink_to(outside)
    _blocked(fake, "outside approved root|canonical relative path")


def test_invalid_identifiers_blocked(fake: Export):
    _blocked(fake, "invalid take id", take_id="bad id!")
    _blocked(fake, "invalid tenant_id", tenant_id="x")
    _blocked(fake, "produced_at", produced_at="not-a-date")
    _blocked(fake, "produced_at requires UTC offset", produced_at="2026-11-04T00:00:00")


def test_receipt_failure_writes_nothing_to_ledger(fake: Export, tmp_path, capsys):
    fake.video.write_bytes(b"changed")
    ledger = tmp_path / "l.sqlite"
    code, out = _cli(capsys, fake.argv(ledger))
    assert code == 1 and out["status"] == "blocked"
    with EvidenceLedger(ledger) as db:
        assert db.read("org1", "campaign1") == []


def test_check_take_receipt_requires_matching_digest_case_insensitively():
    snap = _snapshot("A" * 64)
    assert check_take_receipt(snap, h3_project_id="proj1", take_id="take1",
                              media_name="take001.mp4", media_sha256="a" * 64) == "job1"


# ───────── 負向：真實媒體（需 ffmpeg） ─────────

@needs_ffmpeg
def test_non_mp4_bytes_with_matching_digest_rejected_and_not_recorded(fake: Export, tmp_path, capsys):
    ledger = tmp_path / "l.sqlite"  # 收據完全吻合，但位元組不是可解碼影片
    code, out = _cli(capsys, fake.argv(ledger))
    assert code == 1 and out["status"] == "blocked"
    with EvidenceLedger(ledger) as db:
        assert db.read("org1", "campaign1") == []


@needs_ffmpeg
@pytest.mark.parametrize("kwargs,match", [
    ({"audio": True}, "exactly one video stream"),
    ({"codec": "mpeg4"}, "H.264"),
    ({"pix": "yuv444p", "codec": "libx264"}, "yuv420p"),
    ({"size": "32x32"}, "dimensions"),
])
def test_policy_violating_real_media_rejected(tmp_path, kwargs, match):
    src = tmp_path / "bad.mp4"
    _encode(src, **kwargs)
    with pytest.raises(MediaProbeRejected, match=match):
        probe_mp4(src)


@needs_ffmpeg
def test_truncated_media_rejected(real: Export, tmp_path):
    data = real.video.read_bytes()
    cut = tmp_path / "cut.mp4"
    cut.write_bytes(data[: len(data) // 2])
    with pytest.raises(MediaProbeRejected):
        probe_mp4(cut)


@needs_ffmpeg
def test_media_swapped_after_export_blocked_even_if_valid_video(real: Export, tmp_path):
    other = tmp_path / "other.mp4"
    _encode(other, frames=45)  # 另一支完全合法的影片，但與收據的 SHA-256 不符
    real.video.write_bytes(other.read_bytes())
    _blocked(real, "TAKE_MEDIA_DIGEST_MISMATCH")


def test_missing_tools_exit_4_and_no_evidence(fake: Export, tmp_path, monkeypatch, capsys):
    ledger = tmp_path / "l.sqlite"
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    code, out = _cli(capsys, fake.argv(ledger))
    assert code == 4 and out["status"] == "not_verified" and out["ok"] is False
    with EvidenceLedger(ledger) as db:
        assert db.read("org1", "campaign1") == []
    with pytest.raises(MediaProbeUnavailable):
        probe_mp4(fake.video)


# ───────── parse_probe（純邏輯，不需 ffmpeg） ─────────

def _info(**stream_over) -> dict:
    stream = {"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p", "width": 320,
              "height": 180, "avg_frame_rate": "30/1", "nb_read_frames": "30", "nb_frames": "30"}
    return {"format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"}, "streams": [stream | stream_over]}


def test_parse_probe_accepts_baseline():
    assert parse_probe(_info())["frame_count"] == 30


@pytest.mark.parametrize("over,match", [
    ({"nb_frames": "29"}, "indexed frame count"),
    ({"nb_read_frames": "N/A"}, "counted frames"),
    ({"nb_read_frames": "0", "nb_frames": "0"}, "no frames"),
    ({"avg_frame_rate": "0/0"}, "frame rate invalid"),
    ({"avg_frame_rate": "-30/1"}, "positive"),
    ({"nb_read_frames": "1801", "nb_frames": "1801"}, "longer than 60"),
    ({"width": True}, "dimensions"),
])
def test_parse_probe_rejects(over, match):
    with pytest.raises(MediaProbeRejected, match=match):
        parse_probe(_info(**over))


@pytest.mark.parametrize("info", [None, [], {}, {"format": {}, "streams": {}},
                                  {"format": {"format_name": "matroska,webm"}, "streams": [{}]}])
def test_parse_probe_rejects_malformed_documents(info):
    with pytest.raises(MediaProbeRejected):
        parse_probe(info)


def test_derived_export_view_with_extra_native_fields_is_accepted():
    """另一版 H3 exporter（`h3_native_take_export`）輸出的衍生快照含額外欄位，仍須相容。"""
    snap = _snapshot("b" * 64)
    take = snap["production_v1"]["takes"][0]
    take.update({"status": "media_hashed", "source_media_verified": True, "shot_id": "shot1",
                 "source_media_bytes": 123})
    snap["production_v1"]["workflow_runtime_v1"]["runs"][0].update(
        {"take_id": "take1", "shot_id": "shot1", "source": "h3_take_receipt"})
    snap["production_v1"]["generation_history"][0].update(
        {"source": "comfyui-confirmed-success-at-export", "completed": True})
    snap["native_export_provenance"] = {"schema": "h3-verified-take-export/v1", "derivedSnapshot": True}
    assert check_take_receipt(snap, h3_project_id="proj1", take_id="take1",
                              media_name="take001.mp4", media_sha256="b" * 64) == "job1"
