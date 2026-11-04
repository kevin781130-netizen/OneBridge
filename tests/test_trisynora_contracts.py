"""Contract adversarial checks; no services, models or GPUs are required."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from jsonschema import Draft202012Validator
from onebridge.evidence_contracts.validator import ContractError, SCHEMAS, validate_bundle

D = lambda value: hashlib.sha256(value).hexdigest()


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "output.bin").write_bytes(b"test video bytes only")
        (self.root / "report.json").write_bytes(b'{"qa":"pass"}')
        self.digest = D(b"test video bytes only")
        self.report_digest = D(b'{"qa":"pass"}')
        self.records = self.fixture()

    def common(self, kind, rid, *, producer="minimax-h3"):
        return {
            "schema_version": "trisynora/v1", "record_type": kind,
            "record_id": rid, "event_id": f"evt_{rid}",
            "tenant_id": "client_01", "project_id": "ad_01",
            "produced_at": "2026-10-11T09:30:00Z", "producer": producer,
            "producer_version": "demo-commit-1",
            "source": {"system": producer, "reference": f"runs/{rid}", "sha256": D(rid.encode())},
        }

    def fixture(self):
        project = self.common("ProjectSpec", "project_1") | {
            "name": "Demo", "delivery": {"width": 1920, "height": 1080, "fps_num": 30, "fps_den": 1},
            "rights": {"reuse_allowed": False, "cross_tenant_learning_allowed": False, "external_training_allowed": False},
        }
        shot = self.common("ShotSpec", "shot_1") | {
            "project_spec_record_id": "project_1", "shot_id": "s1", "duration_frames": 300,
            "fps_num": 30, "fps_den": 1, "intent": "Logo morph", "acceptance_criteria": ["maintain silhouette"],
        }
        art = self.common("ArtifactRecord", "artifact_1", producer="pointflux") | {
            "shot_record_id": "shot_1",
            "artifact": {"relative_path": "output.bin", "sha256": self.digest,
                         "mime": "application/octet-stream", "byte_size": len(b"test video bytes only")},
        }
        evaluation = self.common("EvaluationRecord", "eval_1", producer="pointflux") | {
            "artifact_record_id": "artifact_1", "artifact_sha256": self.digest,
            "evaluator": "pointflux-judge", "evaluator_version": "v1", "status": "passed",
            "criteria": [{"name": "silhouette", "status": "passed", "actual": 0.99, "threshold": 0.95}],
            "report": {"relative_path": "report.json", "sha256": self.report_digest},
        }
        return [project, shot, art, evaluation]

    def new_artifact(self, ident="artifact_2"):
        return self.common("ArtifactRecord", ident, producer="pointflux") | {
            "shot_record_id": "shot_1",
            "artifact": {"relative_path": "output.bin", "sha256": self.digest,
                         "mime": "application/octet-stream", "byte_size": len(b"test video bytes only")},
        }

    def review(self):
        return self.common("HumanReview", "review_1", producer="human") | {
            "artifact_record_id": "artifact_1", "artifact_sha256": self.digest,
            "reviewer_id": "person_1", "decision": "approved", "notes": "Looks good", 
        }

    def assert_invalid(self, records, match=None, **kwargs):
        with self.assertRaises(ContractError) as caught:
            validate_bundle(records, **kwargs)
        if match:
            self.assertIn(match, str(caught.exception))

    def test_all_seven_schemas_self_validate(self):
        kinds = ["ProjectSpec", "ShotSpec", "ArtifactRecord", "EvaluationRecord", "HumanReview", "DecisionRecord", "RevisionEdge"]
        for kind in kinds:
            Draft202012Validator.check_schema(json.loads((SCHEMAS / f"{kind}.schema.json").read_text()))

    def test_evidence_validation(self):
        result = validate_bundle(self.records, evidence_root=self.root)
        self.assertTrue(result["ok"])
        self.assertTrue(result["evidence_files_verified"])
        self.assertEqual((result["artifacts_verified"], result["reports_verified"]), (1, 1))
        self.assertFalse(result["release_approved"])

    def test_out_of_order_events(self):
        result = validate_bundle(list(reversed(self.records)), evidence_root=self.root)
        self.assertEqual(result["record_count"], 4)

    def test_structural_only_no_false_proof(self):
        result = validate_bundle(self.records)
        self.assertTrue(result["schema_valid"])
        self.assertFalse(result["evidence_files_verified"])
        self.assertEqual(result["artifacts_verified"], 0)

    def test_idempotent_duplicate(self):
        records = self.records + [copy.deepcopy(self.records[0])]
        r = validate_bundle(records)
        self.assertEqual(r["deduplicated_in_bundle"], 1)
        self.assertEqual(r["record_count"], 4)

    def test_event_id_collision(self):
        records = copy.deepcopy(self.records)
        duplicate = copy.deepcopy(records[0]); duplicate["name"] = "Forged"
        self.assert_invalid(records + [duplicate], "event_id reused")

    def test_record_id_collision(self):
        duplicate = copy.deepcopy(self.records[0]); duplicate["event_id"] = "evt_other"
        self.assert_invalid(self.records + [duplicate], "record_id reused")

    def test_unknown_record_type(self):
        bad = copy.deepcopy(self.records)
        bad[0]["record_type"] = "UnsafeApproval"
        self.assert_invalid(bad, "unknown record_type")

    def test_unknown_authorization_field(self):
        bad = copy.deepcopy(self.records)
        bad[2]["release_approved"] = True
        self.assert_invalid(bad, "Additional properties")

    def test_hash_format(self):
        bad = copy.deepcopy(self.records)
        bad[2]["artifact"]["sha256"] = "1234"
        self.assert_invalid(bad, "does not match")

    def test_hash_of_actual_bytes(self):
        (self.root / "output.bin").write_bytes(b"tampered")
        self.assert_invalid(self.records, "sha256 mismatch", evidence_root=self.root)

    def test_size_of_actual_bytes(self):
        bad = copy.deepcopy(self.records)
        bad[2]["artifact"]["byte_size"] = 10
        self.assert_invalid(bad, "byte_size mismatch", evidence_root=self.root)

    def test_evaluation_report_integrity(self):
        (self.root / "report.json").write_bytes(b"tampered")
        self.assert_invalid(self.records, "sha256 mismatch", evidence_root=self.root)

    def test_unexecuted_cannot_pass(self):
        bad = copy.deepcopy(self.records)
        bad[3]["status"] = "not-run"
        self.assert_invalid(bad, "unexecuted")

    def test_pass_must_have_passed_criteria(self):
        bad = copy.deepcopy(self.records)
        bad[3]["criteria"] = []
        self.assert_invalid(bad, "nonempty")

    def test_failed_requires_failure(self):
        bad = copy.deepcopy(self.records)
        bad[3]["status"] = "failed"
        self.assert_invalid(bad, "failed criterion")

    def test_cross_tenant_forged_reference(self):
        bad = copy.deepcopy(self.records)
        bad[2]["tenant_id"] = "client_02"
        self.assert_invalid(bad, "cross-tenant")

    def test_cross_project_forged_reference(self):
        bad = copy.deepcopy(self.records)
        bad[2]["project_id"] = "ad_02"
        self.assert_invalid(bad, "cross-project")

    def test_missing_reference(self):
        bad = copy.deepcopy(self.records)
        bad[3]["artifact_record_id"] = "missing"
        self.assert_invalid(bad, "invalid artifact_record_id")

    def test_wrong_artifact_digest_reference(self):
        bad = copy.deepcopy(self.records)
        bad[3]["artifact_sha256"] = D(b"other")
        self.assert_invalid(bad, "digest disagrees")

    def test_human_forgery_from_model_blocked(self):
        self.assert_invalid(self.records + [self.review()], "untrusted human review")

    def test_authenticated_integration_may_supply_review_identity(self):
        review = self.review()
        good = validate_bundle(self.records + [review],
                               trusted_human_reviewers={review["event_id"]: "person_1"})
        self.assertEqual(good["human_review_count"], 1)
        self.assertFalse(good["release_approved"])
        self.assert_invalid(self.records + [review], "untrusted", trusted_human_reviewers={review["event_id"]: "another"})

    def test_decision_and_revision(self):
        other = self.new_artifact()
        change = self.common("RevisionEdge", "revision_1", producer="pointflux") | {
            "from_artifact_record_id": "artifact_1", "to_artifact_record_id": "artifact_2",
            "change_summary": "Changed morph timing", "change_digest": D(b"delta")}
        decision = self.common("DecisionRecord", "decision_1") | {
            "chosen_artifact_record_id": "artifact_2", "candidate_artifact_record_ids": ["artifact_1", "artifact_2"],
            "evaluation_record_ids": ["eval_1"], "rationale": "Improved silhouette"}
        r = validate_bundle(self.records + [other, change, decision], evidence_root=self.root)
        self.assertEqual(r["record_count"], 7)

    def test_decision_rejects_evaluation_of_non_candidate(self):
        other = self.new_artifact()
        decision = self.common("DecisionRecord", "decision_1") | {
            "chosen_artifact_record_id": "artifact_2",
            "candidate_artifact_record_ids": ["artifact_2"],
            "evaluation_record_ids": ["eval_1"],
            "rationale": "Incorrectly claims unrelated evaluation",
        }
        self.assert_invalid(self.records + [other, decision], "non-candidate")

    def test_revision_evaluation_must_match_endpoint(self):
        other = self.new_artifact()
        edge = self.common("RevisionEdge", "edge_with_wrong_eval") | {
            "from_artifact_record_id": "artifact_1",
            "to_artifact_record_id": "artifact_2",
            "change_summary": "Unknown", "change_digest": D(b"change"),
            "after_evaluation_record_id": "eval_1",
        }
        self.assert_invalid(self.records + [other, edge], "different artifact")

    def test_revision_cycle(self):
        other = self.new_artifact()
        a = self.common("RevisionEdge", "edge_a") | {
            "from_artifact_record_id": "artifact_1", "to_artifact_record_id": "artifact_2",
            "change_summary": "A", "change_digest": D(b"a")}
        b = self.common("RevisionEdge", "edge_b") | {
            "from_artifact_record_id": "artifact_2", "to_artifact_record_id": "artifact_1",
            "change_summary": "B", "change_digest": D(b"b")}
        self.assert_invalid(self.records + [other, a, b], "cycle")

    def test_path_escape(self):
        bad = copy.deepcopy(self.records)
        bad[2]["artifact"]["relative_path"] = "../output.bin"
        self.assert_invalid(bad, evidence_root=self.root)

    def test_symlink_escape(self):
        parent = self.root.parent
        outside = parent / "trisynora-outside.bin"
        outside.write_bytes(b"outside")
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        link = self.root / "out-link"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        bad = copy.deepcopy(self.records)
        bad[2]["artifact"].update(relative_path="out-link", sha256=D(b"outside"), byte_size=7)
        self.assert_invalid(bad, "outside root", evidence_root=self.root)

    def test_cli_json_output_and_exit_status(self):
        path = self.root / "bundle.json"
        path.write_text(json.dumps(self.records))
        good = subprocess.run([sys.executable, "-m", "onebridge.evidence_contracts.validator", str(path),
                               "--evidence-root", str(self.root)], text=True, capture_output=True)
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertTrue(json.loads(good.stdout)["ok"])
        (self.root / "report.json").write_bytes(b"bad")
        failed = subprocess.run([sys.executable, "-m", "onebridge.evidence_contracts.validator", str(path),
                                 "--evidence-root", str(self.root)], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 1)
        self.assertFalse(json.loads(failed.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()
