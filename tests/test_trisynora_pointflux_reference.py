"""PointFlux reference adapter（自 TRISYNORA 遷入的 10 個原始測試）；離線、不呼叫任何外部產品。"""
import copy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from onebridge.evidence_contracts.adapters.pointflux_reference import ReferenceError, build_records


class PointFluxReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        scene = json.dumps({
            "version": "1.4", "seed": 1, "particles": 500,
            "source": {"id": "logo", "type": "image", "uri": "/assets/logo.png"},
            "targets": {"heart": {"id": "heart", "type": "image",
                                  "uri": "/assets/heart.png"}},
            "timeline": [{"id": "morph_1", "effect": "morph", "at": 1.25,
                          "duration": 2.5, "targetId": "heart"}],
            "camera": {"id": "main", "type": "perspective",
                       "position": [0, 0, 1], "target": [0, 0, 0], "fov": 45},
            "output": {"width": 1920, "height": 1080, "fps": 24,
                       "background": "#000000"},
        }, separators=(",", ":")).encode()
        self.scene_bytes = scene
        (self.root / "shot.pf.json").write_bytes(scene)
        self.scene_sha = hashlib.sha256(scene).hexdigest()
        self.shot = {"shotId": "shot_A", "name": "Light reveal",
                     "origin": {"engine": "pointflux", "sourceVersion": "1.4", "sourceSha256": self.scene_sha},
                     "durationFrames": 120, "fps": 24,
                     "camera": {"position": [0, 0, 1], "target": [0, 0, 0], "fovDegrees": 45},
                     "output": {"width": 1920, "height": 1080},
                     "timeline": [{"eventId": "morph_1", "kind": "morph", "atFrame": 30,
                                   "durationFrames": 60}],
                     "assets": [{"role": "source", "uri": "assets/logo.png"},
                                {"role": "target", "uri": "assets/heart.png"}],
                     "handoff": {"mode": "reference-only", "requiresExplicitBind": True}}
        self.exchange = {"format": "pointflux-h3-shot-exchange/v1",
                         "projectId": "project_A", "shots": [self.shot]}

    def build(self, override=None, mapping=None, **options):
        src = self.exchange if override is None else override
        return build_records(json.dumps(src).encode(), tenant_id="tenant_A",
                             project_name="Commercial demo",
                             produced_at="2026-10-18T12:00:00+08:00",
                             scene_root=self.root,
                             scene_paths={"shot_A": "shot.pf.json"} if mapping is None else mapping,
                             **options)

    def test_metadata_only_valid_references(self):
        a = self.build()
        b = self.build()
        self.assertEqual(a, b)
        self.assertEqual([x["record_type"] for x in a], ["ProjectSpec", "ShotSpec"])
        self.assertEqual(a[1]["source"]["sha256"], self.scene_sha)
        self.assertEqual(a[1]["project_spec_record_id"], a[0]["record_id"])
        self.assertEqual(a[0]["delivery"], {"width": 1920, "height": 1080,
                                            "fps_num": 24, "fps_den": 1})
        self.assertFalse(a[0]["rights"]["external_training_allowed"])
        self.assertEqual((self.root / "shot.pf.json").read_bytes(), self.scene_bytes)

    def test_records_conform_to_published_v1_validator(self):
        from onebridge.evidence_contracts.validator import validate_bundle
        result = validate_bundle(self.build())
        self.assertIsInstance(result, dict)

    def test_parent_directory_filename_is_not_accepted(self):
        bad = copy.deepcopy(self.exchange)
        bad["shots"][0]["assets"][0]["uri"] = "assets/a..b.png"
        with self.assertRaises(ReferenceError):
            self.build(bad)

    def test_source_bytes_are_required_not_just_declared_digest(self):
        changed = copy.deepcopy(self.exchange)
        changed["shots"][0]["origin"]["sourceSha256"] = "0" * 64
        with self.assertRaisesRegex(ReferenceError, "digest mismatch"):
            self.build(changed)
        (self.root / "shot.pf.json").write_text('{"version":"1.4"}')
        with self.assertRaisesRegex(ReferenceError, "digest mismatch"):
            self.build()

    def test_rejects_untrusted_handoff_and_unknown_fields(self):
        for patch in (
            lambda s: s["handoff"].update({"requiresExplicitBind": False}),
            lambda s: s.update({"prompt": "overwrite director"}),
            lambda s: s["assets"][0].update({"uri": "../secret.json"}),
            lambda s: s["origin"].update({"engine": "h3"}),
            lambda s: s.update({"fps": 0}),
            lambda s: s["timeline"][0].update({"atFrame": 121}),
        ):
            bad = copy.deepcopy(self.exchange)
            patch(bad["shots"][0])
            with self.subTest(bad=bad), self.assertRaises(ReferenceError):
                self.build(bad)

    def test_forged_camera_timeline_output_or_asset_is_refused(self):
        for patch in (
            lambda s: s["camera"].update({"position": [1, 2, 3]}),
            lambda s: s["timeline"][0].update({"atFrame": 31}),
            lambda s: s["assets"][0].update({"uri": "assets/fake.png"}),
            lambda s: s["output"].update({"width": 1280}),
        ):
            bad = copy.deepcopy(self.exchange)
            patch(bad["shots"][0])
            with self.subTest(bad=bad), self.assertRaises(ReferenceError):
                self.build(bad)

    def test_rejects_missing_map_and_unsafe_paths(self):
        for mapping in ({}, {"shot_A": "../shot.pf.json"},
                        {"shot_A": "/etc/passwd"}, {"shot_A": "shot.pf.json", "extra": "x"}):
            with self.subTest(mapping=mapping), self.assertRaises(ReferenceError):
                self.build(mapping=mapping)

    def test_project_delivery_must_be_unambiguous(self):
        bad = copy.deepcopy(self.exchange)
        duplicate = copy.deepcopy(self.shot)
        duplicate["shotId"] = "shot_B"
        duplicate["output"]["width"] = 1280
        bad["shots"].append(duplicate)
        with self.assertRaisesRegex(ReferenceError, "mixed delivery"):
            self.build(bad, mapping={"shot_A": "shot.pf.json", "shot_B": "shot.pf.json"})

    def test_short_native_ids_not_silently_mapped(self):
        bad = copy.deepcopy(self.exchange)
        bad["shots"][0]["shotId"] = "X"
        with self.assertRaisesRegex(ReferenceError, "too short"):
            self.build(bad, mapping={"X": "shot.pf.json"})
        bad = copy.deepcopy(self.exchange)
        bad["projectId"] = "X"
        with self.assertRaisesRegex(ReferenceError, "too short"):
            self.build(bad)

    def test_duplicate_json_keys_and_nonfinite_are_rejected(self):
        with self.assertRaises(ReferenceError):
            build_records(b'{"format":"x","format":"y"}', tenant_id="tenant_A",
                          project_name="x", produced_at="2026-10-18T12:00:00Z",
                          scene_root=self.root, scene_paths={"shot_A": "shot.pf.json"})
        with self.assertRaises(ReferenceError):
            build_records(b'{"shots":NaN}', tenant_id="tenant_A",
                          project_name="x", produced_at="2026-10-18T12:00:00Z",
                          scene_root=self.root, scene_paths={"shot_A": "shot.pf.json"})



# ───────── OneBridge 新增：CLI、路徑安全、SHA-256、相容性 ─────────

import os
import pytest

from onebridge.cli import main as onebridge_main
from onebridge.evidence_contracts.ledger import EvidenceLedger
from onebridge.evidence_contracts.validator import ContractError, validate_bundle


class Env(PointFluxReferenceTests):
    """沿用原測試的資料建構，供 CLI 測試使用（自身不被收集，避免原測試重複執行）。"""

    __test__ = False

    def runTest(self):  # pragma: no cover - 僅為可實例化
        pass

    def files(self, tmp: Path):
        ex, mp = tmp / "exchange.json", tmp / "map.json"
        if not ex.exists():  # 僅首次建立，讓測試可先竄改再呼叫 argv
            ex.write_text(json.dumps(self.exchange), encoding="utf-8")
            mp.write_text(json.dumps({"shot_A": "shot.pf.json"}), encoding="utf-8")
        return ex, mp

    def argv(self, tmp: Path, out: Path, **over) -> list[str]:
        ex, mp = self.files(tmp)
        opts = {"exchange": ex, "scene-root": self.root, "scene-map": mp, "tenant-id": "tenant_A",
                "project-name": "Commercial demo", "produced-at": "2026-10-18T12:00:00+08:00", "out": out}
        opts.update(over)
        cmd = ["evidence-pointflux-reference"]
        for key, value in opts.items():
            cmd += ["--" + key, str(value)]
        return cmd


@pytest.fixture()
def env(tmp_path: Path):
    e = Env()
    e.setUp()
    yield e
    e.temp.cleanup()


def _run(capsys, argv):
    code = onebridge_main(argv)
    return code, json.loads(capsys.readouterr().out)


def test_cli_positive_writes_valid_metadata_only(env, tmp_path, capsys):
    out = tmp_path / "records.json"
    code, res = _run(capsys, env.argv(tmp_path, out))
    assert code == 0 and res["ok"] is True and res["records"] == 2
    assert (res["nativeImported"], res["renderVerified"], res["humanApproved"], res["releaseApproved"]) == (False,) * 4
    records = json.loads(out.read_text(encoding="utf-8"))
    assert [r["record_type"] for r in records] == ["ProjectSpec", "ShotSpec"]
    assert validate_bundle(records)["release_approved"] is False
    assert records[1]["source"]["sha256"] == env.scene_sha


def test_output_is_compatible_with_existing_ledger(env, tmp_path, capsys):
    """與既有 EvidenceLedger 相容（僅驗證相容性；此 CLI 本身不寫入 ledger）。"""
    out = tmp_path / "records.json"
    assert _run(capsys, env.argv(tmp_path, out))[0] == 0
    records = json.loads(out.read_text(encoding="utf-8"))
    with EvidenceLedger(tmp_path / "l.sqlite") as ledger:
        assert ledger.append(records)["inserted"] == 2
        assert ledger.append(records)["replayed"] == 2


def test_cli_never_overwrites_existing_output(env, tmp_path, capsys):
    out = tmp_path / "records.json"
    out.write_text("keep", encoding="utf-8")
    code, res = _run(capsys, env.argv(tmp_path, out))
    assert code == 1 and res["ok"] is False and out.read_text(encoding="utf-8") == "keep"


def test_cli_rejects_output_inside_scene_root(env, tmp_path, capsys):
    code, res = _run(capsys, env.argv(tmp_path, env.root / "records.json"))
    assert code == 1 and "inside source project" in res["error"]
    assert not (env.root / "records.json").exists()


def test_cli_rejects_tampered_scene_bytes(env, tmp_path, capsys):
    (env.root / "shot.pf.json").write_bytes(env.scene_bytes + b" ")
    code, res = _run(capsys, env.argv(tmp_path, tmp_path / "o.json"))
    assert code == 1 and "digest mismatch" in res["error"] and not (tmp_path / "o.json").exists()


@pytest.mark.parametrize("bad", ["../shot.pf.json", "/etc/passwd", "a\\b.json", "missing.json"])
def test_cli_rejects_unsafe_or_missing_scene_paths(env, tmp_path, capsys, bad):
    ex, mp = env.files(tmp_path)
    mp.write_text(json.dumps({"shot_A": bad}), encoding="utf-8")
    code, res = _run(capsys, env.argv(tmp_path, tmp_path / "o.json"))
    assert code == 1 and res["ok"] is False


@pytest.mark.skipif(os.name == "nt", reason="Windows 建立 symlink 需特殊權限：NOT_RUN")
def test_cli_rejects_symlinked_scene_even_with_correct_bytes(env, tmp_path, capsys):
    outside = tmp_path / "outside.pf.json"
    outside.write_bytes(env.scene_bytes)
    (env.root / "shot.pf.json").unlink()
    (env.root / "shot.pf.json").symlink_to(outside)
    code, res = _run(capsys, env.argv(tmp_path, tmp_path / "o.json"))
    assert code == 1 and res["ok"] is False


@pytest.mark.skipif(os.name == "nt", reason="Windows 建立 symlink 需特殊權限：NOT_RUN")
def test_cli_rejects_symlinked_scene_root(env, tmp_path, capsys):
    link = tmp_path / "rootlink"
    link.symlink_to(env.root, target_is_directory=True)
    code, res = _run(capsys, env.argv(tmp_path, tmp_path / "o.json", **{"scene-root": link}))
    assert code == 1 and "symlink" in res["error"]


def test_cli_rejects_malformed_inputs(env, tmp_path, capsys):
    ex, mp = env.files(tmp_path)
    ex.write_text('{"format":"x","format":"y"}', encoding="utf-8")
    code, res = _run(capsys, env.argv(tmp_path, tmp_path / "o1.json"))
    assert code == 1 and "DUPLICATE_JSON_KEY" in res["error"]
    mp.write_text("[]", encoding="utf-8")
    ex.write_text(json.dumps(env.exchange), encoding="utf-8")
    code, res = _run(capsys, env.argv(tmp_path, tmp_path / "o2.json"))
    assert code == 1 and res["ok"] is False
    code, res = _run(capsys, env.argv(tmp_path, tmp_path / "o3.json", **{"produced-at": "2026-10-18T12:00:00"}))
    assert code == 1 and "timestamp" in res["error"]


def test_scene_path_failures_are_contract_errors(env):
    with pytest.raises(ContractError):
        env.build(mapping={"shot_A": "nope.pf.json"})


def test_reference_adapter_not_wired_to_authority_modules():
    src = Path(__file__).resolve().parent.parent / "src" / "onebridge"
    bad = [p.name for p in src.rglob("*.py") if "evidence_contracts" not in p.parts
           and p.name != "cli.py" and "pointflux_reference" in p.read_text(encoding="utf-8")]
    assert bad == []
