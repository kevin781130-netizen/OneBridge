"""`onebridge` CLI 端對端測試：使用真實檔案位元組驗證 SHA-256（非僅驗證 JSON 格式）。

測試媒體為合成資料，不代表 PointFlux／Chromium／FFmpeg 曾在真實機器上執行。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from onebridge.cli import main
from onebridge.evidence_contracts.ledger import EvidenceLedger
from onebridge.evidence_contracts.validator import ContractError

SHA = lambda data: hashlib.sha256(data).hexdigest()
VIDEO = b"demo-video-bytes"
REPORT = b'{"score":0.9}'


def _rec(kind: str, rid: str, producer: str = "vera") -> dict:
    return {
        "schema_version": "trisynora/v1", "record_type": kind, "record_id": rid,
        "event_id": "event_" + rid, "tenant_id": "client", "project_id": "campaign",
        "produced_at": "2026-10-11T00:00:00Z", "producer": producer, "producer_version": "1",
        "source": {"system": producer, "reference": rid, "sha256": SHA(rid.encode())},
    }


def _bundle() -> list[dict]:
    return [
        _rec("ProjectSpec", "proj") | {
            "name": "Campaign",
            "delivery": {"width": 1280, "height": 720, "fps_num": 30, "fps_den": 1},
            "rights": {"reuse_allowed": False, "cross_tenant_learning_allowed": False,
                       "external_training_allowed": False}},
        _rec("ShotSpec", "shot") | {
            "project_spec_record_id": "proj", "shot_id": "shot-01", "duration_frames": 30,
            "fps_num": 30, "fps_den": 1, "intent": "Reveal", "acceptance_criteria": ["clarity"]},
        _rec("ArtifactRecord", "art", "pointflux") | {
            "shot_record_id": "shot",
            "artifact": {"relative_path": "video.bin", "sha256": SHA(VIDEO),
                         "mime": "application/octet-stream", "byte_size": len(VIDEO)}},
        _rec("EvaluationRecord", "eval", "pointflux") | {
            "artifact_record_id": "art", "artifact_sha256": SHA(VIDEO),
            "evaluator": "pointflux", "evaluator_version": "1", "status": "passed",
            "criteria": [{"name": "clarity", "status": "passed", "actual": 0.9, "threshold": 0.8}],
            "report": {"relative_path": "judge.json", "sha256": SHA(REPORT)}},
    ]


@pytest.fixture()
def env(tmp_path: Path):
    root = tmp_path / "evidence"
    root.mkdir()
    (root / "video.bin").write_bytes(VIDEO)
    (root / "judge.json").write_bytes(REPORT)
    bundle = tmp_path / "bundle.json"
    bundle.write_text(json.dumps(_bundle()), encoding="utf-8")
    return root, bundle


def _run(capsys, *argv: str) -> tuple[int, dict]:
    code = main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def test_cli_positive_verifies_real_bytes(env, capsys):
    root, bundle = env
    code, out = _run(capsys, "evidence-validate", str(bundle), "--evidence-root", str(root))
    assert code == 0 and out["ok"] is True
    assert out["evidence_files_verified"] is True
    assert (out["artifacts_verified"], out["reports_verified"]) == (1, 1)
    assert out["release_approved"] is False


def test_cli_schema_only_does_not_claim_byte_verification(env, capsys):
    _, bundle = env
    code, out = _run(capsys, "evidence-validate", str(bundle))
    assert code == 0 and out["evidence_files_verified"] is False
    assert out["artifacts_verified"] == 0 and out["reports_verified"] == 0


def test_cli_rejects_tampered_artifact_bytes(env, capsys):
    root, bundle = env
    (root / "video.bin").write_bytes(VIDEO + b"x")
    code, out = _run(capsys, "evidence-validate", str(bundle), "--evidence-root", str(root))
    assert code == 1 and out["ok"] is False and "sha256 mismatch" in out["error"]


def test_cli_rejects_tampered_report_bytes(env, capsys):
    root, bundle = env
    (root / "judge.json").write_bytes(b'{"score":1.0}')
    code, out = _run(capsys, "evidence-validate", str(bundle), "--evidence-root", str(root))
    assert code == 1 and "sha256 mismatch" in out["error"]


def test_cli_rejects_missing_evidence_file(env, capsys):
    root, bundle = env
    (root / "video.bin").unlink()
    code, out = _run(capsys, "evidence-validate", str(bundle), "--evidence-root", str(root))
    assert code == 1 and out["ok"] is False


def test_cli_rejects_missing_evidence_root(env, capsys, tmp_path):
    _, bundle = env
    code, out = _run(capsys, "evidence-validate", str(bundle), "--evidence-root", str(tmp_path / "nope"))
    assert code == 1 and out["ok"] is False


@pytest.mark.skipif(os.name == "nt", reason="建立 symlink 需要 Windows 特殊權限；該平台標記 NOT_RUN")
def test_cli_rejects_symlink_escaping_root(env, capsys, tmp_path):
    root, bundle = env
    outside = tmp_path / "outside.bin"
    outside.write_bytes(VIDEO)  # 位元組完全正確，但位於核准根目錄之外
    (root / "video.bin").unlink()
    (root / "video.bin").symlink_to(outside)
    code, out = _run(capsys, "evidence-validate", str(bundle), "--evidence-root", str(root))
    assert code == 1 and "outside root" in out["error"]


@pytest.mark.parametrize("bad", ["../escape.bin", "/etc/passwd", "C:\\x.bin", "file:///x"])
def test_cli_rejects_illegal_paths_in_bundle(env, capsys, tmp_path, bad):
    root, _ = env
    records = _bundle()
    records[2]["artifact"]["relative_path"] = bad
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(records), encoding="utf-8")
    code, out = _run(capsys, "evidence-validate", str(path), "--evidence-root", str(root))
    assert code == 1 and out["ok"] is False


def test_cli_rejects_human_review_without_verified_identity(env, capsys, tmp_path):
    root, _ = env
    review = _rec("HumanReview", "rev", "human")
    path = tmp_path / "review.json"
    path.write_text(json.dumps(_bundle() + [review]), encoding="utf-8")
    code, out = _run(capsys, "evidence-validate", str(path), "--evidence-root", str(root))
    assert code == 1 and out["ok"] is False


def test_installed_module_entrypoint_runs_in_subprocess(env):
    root, bundle = env
    proc = subprocess.run(
        [sys.executable, "-m", "onebridge.cli", "evidence-validate", str(bundle),
         "--evidence-root", str(root)],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["ok"] is True


def test_pointflux_cli_unwritable_ledger_fails_closed_without_traceback(env, capsys, tmp_path):
    root, _ = env
    argv = ["evidence-import-pointflux", "--evidence-root", str(root),
            "--render-manifest", "x.json", "--judgement", "y.json", "--artifact", "z.mp4",
            "--tenant-id", "t1", "--project-id", "p1", "--shot-id", "s1",
            "--project-name", "n", "--intent", "i", "--produced-at", "2026-11-04T00:00:00Z",
            "--ledger", str(tmp_path / "missing-dir" / "l.sqlite")]
    code, out = _run(capsys, *argv)
    assert code == 1 and out["ok"] is False


def test_ledger_busy_database_reports_original_error_and_writes_nothing(env, tmp_path):
    root, _ = env
    db = tmp_path / "ledger.sqlite"
    ledger = EvidenceLedger(db)
    ledger.conn.execute("PRAGMA busy_timeout=100")
    other = sqlite3.connect(db, isolation_level=None)
    other.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            ledger.append(_bundle(), evidence_root=root)
    finally:
        other.execute("ROLLBACK")
        other.close()
    # 鎖釋放後仍可正常寫入，且先前失敗未留下任何資料列。
    assert ledger.read("client", "campaign") == []
    assert ledger.append(_bundle(), evidence_root=root)["inserted"] == 4
    ledger.close()


def test_ledger_rejects_tampered_bytes_without_partial_write(env, tmp_path):
    root, _ = env
    (root / "video.bin").write_bytes(b"corrupt")
    with EvidenceLedger(tmp_path / "ledger.sqlite") as ledger:
        with pytest.raises(ContractError):
            ledger.append(_bundle(), evidence_root=root)
        assert ledger.read("client", "campaign") == []


def test_migrated_demo_bundle_verifies_against_its_real_asset_bytes(capsys):
    base = Path(__file__).resolve().parent.parent / "docs" / "examples" / "trisynora"
    code, out = _run(capsys, "evidence-validate", str(base / "demo-bundle.json"),
                     "--evidence-root", str(base / "assets"))
    assert code == 0 and out["artifacts_verified"] == 1 and out["reports_verified"] == 1


def test_evidence_contracts_not_wired_into_release_or_approval_authority():
    """證據模組僅供查驗，不得被放行／核准／稽核模組引用，避免形成第二套最終驗收機制。"""
    src = Path(__file__).resolve().parent.parent / "src" / "onebridge"
    allowed = {src / "cli.py"}
    offenders = [
        str(p.relative_to(src)) for p in src.rglob("*.py")
        if "evidence_contracts" not in p.parts and p not in allowed
        and "evidence_contracts" in p.read_text(encoding="utf-8")
    ]
    assert offenders == []
