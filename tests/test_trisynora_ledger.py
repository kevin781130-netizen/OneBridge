"""Durable replay, immutability, integrity, and fail-closed tests."""
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

from onebridge.evidence_contracts.ledger import EvidenceLedger
from onebridge.evidence_contracts.validator import ContractError

D = lambda data: hashlib.sha256(data).hexdigest()


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "ledger.sqlite"
        (self.root / "video.bin").write_bytes(b"demo-video")
        (self.root / "judge.json").write_bytes(b'{"score":0.9}')
        self.records = [self.make("ProjectSpec", "proj") | {
            "name": "Campaign", "delivery": {"width": 1280, "height": 720, "fps_num": 30, "fps_den": 1},
            "rights": {"reuse_allowed": False, "cross_tenant_learning_allowed": False, "external_training_allowed": False},
        }, self.make("ShotSpec", "shot") | {
            "project_spec_record_id": "proj", "shot_id": "shot-01", "duration_frames": 30,
            "fps_num": 30, "fps_den": 1, "intent": "Reveal", "acceptance_criteria": ["clarity"],
        }, self.make("ArtifactRecord", "art", "pointflux") | {
            "shot_record_id": "shot", "artifact": {"relative_path": "video.bin", "sha256": D(b"demo-video"),
                                                 "mime": "application/octet-stream", "byte_size": 10},
        }, self.make("EvaluationRecord", "eval", "pointflux") | {
            "artifact_record_id": "art", "artifact_sha256": D(b"demo-video"),
            "evaluator": "pointflux", "evaluator_version": "1", "status": "passed",
            "criteria": [{"name": "clarity", "status": "passed", "actual": 0.9, "threshold": 0.8}],
            "report": {"relative_path": "judge.json", "sha256": D(b'{"score":0.9}')},
        }]

    def make(self, kind, id, producer="vera"):
        return {"schema_version": "trisynora/v1", "record_type": kind, "record_id": id,
                "event_id": "event_" + id, "tenant_id": "client", "project_id": "campaign",
                "produced_at": "2026-10-11T00:00:00Z", "producer": producer,
                "producer_version": "1", "source": {"system": producer, "reference": id, "sha256": D(id.encode())}}

    def test_batch_atomic_and_replay_after_reopen(self):
        with EvidenceLedger(self.db) as ledger:
            out = ledger.append(self.records, evidence_root=self.root)
            self.assertEqual(out["inserted"], 4)
            head = out["chain_head"]
        with EvidenceLedger(self.db) as ledger:
            repeated = ledger.append(self.records, evidence_root=self.root)
            self.assertEqual(repeated["replayed"], 4)
            self.assertEqual(repeated["inserted"], 0)
            self.assertEqual(head, repeated["chain_head"])
            self.assertEqual(len(ledger.read("client", "campaign")), 4)

    def test_append_across_batches_resolves_prior_refs(self):
        with EvidenceLedger(self.db) as ledger:
            ledger.append(self.records[:2])
            ledger.append(self.records[2:], evidence_root=self.root)
            self.assertEqual(len(ledger.read("client", "campaign")), 4)

    def test_conflicting_event_never_modifies_history(self):
        with EvidenceLedger(self.db) as ledger:
            ledger.append(self.records[:2]); records = copy.deepcopy(self.records)
            records[0]["name"] = "forged"
            with self.assertRaises(ContractError): ledger.append(records)
            self.assertEqual(len(ledger.read("client", "campaign")), 2)

    def test_same_record_different_event_rejected(self):
        with EvidenceLedger(self.db) as ledger:
            ledger.append(self.records[:2]); bad = copy.deepcopy(self.records[0])
            bad["event_id"] = "new_event"
            with self.assertRaises(ContractError): ledger.append([bad])

    def test_missing_evidence_root_denied(self):
        with EvidenceLedger(self.db) as ledger:
            with self.assertRaisesRegex(ContractError, "verified evidence bytes"):
                ledger.append(self.records)

    def test_unverified_artifact_mutation_rollback(self):
        with EvidenceLedger(self.db) as ledger:
            ledger.append(self.records[:2]); (self.root / "video.bin").write_bytes(b"forged")
            with self.assertRaises(ContractError): ledger.append(self.records[2:], evidence_root=self.root)
            self.assertEqual(len(ledger.read("client", "campaign")), 2)

    def test_cross_project_batch_rejected(self):
        with EvidenceLedger(self.db) as ledger:
            bad = copy.deepcopy(self.records[:2]); bad[1]["project_id"] = "other"
            with self.assertRaises(ContractError): ledger.append(bad)

    def test_untrusted_review_never_ingested(self):
        with EvidenceLedger(self.db) as ledger:
            review = self.make("HumanReview", "review", "human") | {
                "artifact_record_id": "art", "artifact_sha256": D(b"demo-video"),
                "reviewer_id": "admin", "decision": "approved", "notes": "approved"}
            with self.assertRaisesRegex(ContractError, "authenticated"):
                ledger.append(self.records + [review], evidence_root=self.root)

    def test_tamper_detection(self):
        with EvidenceLedger(self.db) as ledger:
            ledger.append(self.records[:2]); ledger.conn.execute(
                "UPDATE evidence_events SET payload=? WHERE event_id=?", ('{"forged":true}', "event_proj"))
            with self.assertRaisesRegex(ContractError, "integrity"):
                ledger.read("client", "campaign")

    def test_tenant_id_scope_and_global_collision(self):
        with EvidenceLedger(self.db) as ledger:
            ledger.append(self.records[:2]); other = copy.deepcopy(self.records[:2])
            for r in other: r["tenant_id"] = "another"
            self.assertEqual(ledger.append(other)["inserted"], 2)
            self.assertEqual(len(ledger.read("another", "campaign")), 2)
            conflicting = copy.deepcopy(self.records[0]); conflicting["project_id"] = "other-project"
            with self.assertRaises(ContractError): ledger.append([conflicting])


if __name__ == "__main__": unittest.main()
