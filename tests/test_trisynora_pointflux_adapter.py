"""PointFlux v1 temporal judgement contract, local proof, and ledger integration.

Test media are deliberately synthetic: these tests do NOT assert that Chromium,
FFmpeg, or PointFlux executed on a customer's workstation.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from onebridge.evidence_contracts.adapters.pointflux import make_bundle
from onebridge.evidence_contracts.ledger import EvidenceLedger
from onebridge.evidence_contracts.validator import ContractError


class PointFluxAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "run").mkdir()
        (self.root / "judge").mkdir()
        self.artifact = "run/clip.mp4"
        (self.root / self.artifact).write_bytes(b"\x00\x00\x00\x14ftypisom00000000")
        self.scene_id = "1" * 64
        self.manifest = {
            "manifestVersion": "1", "sceneSha256": self.scene_id,
            "pointfluxVersion": "1.2.0", "fps": 30, "width": 1920,
            "height": 1080, "frameCount": 60, "startFrame": 0, "lastFrame": 59,
        }
        self.judge = {
            "version": "1", "sceneSha256": self.scene_id,
            "capture": {"frames": [0, 30, 59], "fps": 30, "width": 320,
                        "height": 180, "referenceFrames": [], "evidenceVersion": "1"},
            "frames": [{"frame": x} for x in [0, 30, 59]],
            "acceptance": {
                "version": "1", "accepted": True,
                "summary": {"required": 1, "requiredPassed": 1, "total": 2, "totalPassed": 1},
                "criteria": [
                    {"id": "safe-frame", "required": True, "pass": True,
                     "observed": 0.92, "threshold": 0.9,
                     "reason": "criterion-satisfied"},
                    {"id": "optional", "required": False, "pass": False,
                     "observed": 0.6, "threshold": 0.9,
                     "reason": "criterion-not-satisfied"},
                ],
            },
        }
        self.write()
        self.opts = dict(evidence_root=self.root, render_manifest="run/render-manifest.json",
                         judgement="judge/temporal-judgement.json", artifact=self.artifact,
                         tenant_id="org1", project_id="film1", shot_id="shot1", project_name="Film",
                         intent="Cinematic opening", produced_at="2026-10-11T10:00:00Z")

    def write(self):
        (self.root / "run/render-manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        (self.root / "judge/temporal-judgement.json").write_text(json.dumps(self.judge), encoding="utf-8")

    def records(self):
        return make_bundle(**self.opts)

    def test_real_format_compatible_positive_import(self):
        recs = self.records()
        self.assertEqual([r["record_type"] for r in recs], ["ProjectSpec", "ShotSpec", "ArtifactRecord", "EvaluationRecord"])
        self.assertEqual(recs[-1]["status"], "passed")
        self.assertEqual(recs[-1]["criteria"], [{"name": "safe-frame", "status": "passed", "actual": 0.92, "threshold": 0.9}])
        self.assertFalse(recs[0]["rights"]["external_training_allowed"])

    def test_failed_required_criterion_maps_to_failed_not_pass(self):
        c = self.judge["acceptance"]["criteria"][0]
        c.update({"pass": False, "observed": 0.2, "reason": "criterion-not-satisfied"})
        self.judge["acceptance"].update({"accepted": False, "summary": {"required": 1, "requiredPassed": 0, "total": 2, "totalPassed": 0}})
        self.write()
        self.assertEqual(self.records()[-1]["status"], "failed")

    def test_accepted_state_cannot_disagree_with_required_criteria(self):
        self.judge["acceptance"]["accepted"] = False
        self.write()
        with self.assertRaisesRegex(ContractError, "contradicts"):
            self.records()

    def test_mismatched_scene_sha_never_imported(self):
        self.judge["sceneSha256"] = "2" * 64
        self.write()
        with self.assertRaisesRegex(ContractError, "sceneSha256"):
            self.records()

    def test_mismatched_fps_never_imported(self):
        self.judge["capture"]["fps"] = 24
        self.write()
        with self.assertRaisesRegex(ContractError, "FPS"):
            self.records()

    def test_frame_outside_render_never_imported(self):
        self.judge["capture"]["frames"] = [0, 60]
        self.judge["frames"] = [{"frame": 0}, {"frame": 60}]
        self.write()
        with self.assertRaisesRegex(ContractError, "interval"):
            self.records()

    def test_non_mp4_bytes_cannot_claim_video(self):
        (self.root / self.artifact).write_bytes(b"false rendered output")
        with self.assertRaisesRegex(ContractError, "MP4"):
            self.records()

    def test_cross_directory_artifact_forbidden(self):
        (self.root / "judge/forged.mp4").write_bytes((self.root / self.artifact).read_bytes())
        self.opts["artifact"] = "judge/forged.mp4"
        with self.assertRaisesRegex(ContractError, "render-manifest directory"):
            self.records()

    def test_symlink_escape_rejected(self):
        outside = Path(self.tmp.name).parent / (self.root.name + "-outside.mp4")
        outside.write_bytes((self.root / self.artifact).read_bytes())
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        (self.root / self.artifact).unlink()
        (self.root / self.artifact).symlink_to(outside)
        with self.assertRaises(ContractError):
            self.records()

    def test_duplicate_import_and_tampering_are_atomic(self):
        db = self.root / "ledger.sqlite"
        records = self.records()
        with EvidenceLedger(db) as ledger:
            self.assertEqual(ledger.append(records, evidence_root=self.root)["inserted"], 4)
            self.assertEqual(ledger.append(records, evidence_root=self.root)["replayed"], 4)
            self.assertEqual(len(ledger.read("org1", "film1")), 4)
            (self.root / self.artifact).write_bytes(b"tamper")
            with self.assertRaises(ContractError):
                ledger.append(records, evidence_root=self.root)
            self.assertEqual(len(ledger.read("org1", "film1")), 4)

    def test_path_traversal_is_rejected(self):
        self.opts["judgement"] = "../report.json"
        with self.assertRaisesRegex(ContractError, "traversal"):
            self.records()

    def test_no_human_approval_created(self):
        self.assertTrue(all(r["record_type"] != "HumanReview" for r in self.records()))

    def test_stable_ids_same_inputs(self):
        a, b = self.records(), self.records()
        self.assertEqual(a, b)

    def test_missing_capture_refused(self):
        self.judge.pop("capture")
        self.write()
        with self.assertRaises(ContractError):
            self.records()


if __name__ == "__main__":
    unittest.main()