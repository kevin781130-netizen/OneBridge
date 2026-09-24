import json
from pathlib import Path

from onebridge.audit import HashChainAuditLog


def test_audit_hash_chain_detects_tampering(tmp_path: Path):
    path = tmp_path / "audit.jsonl"
    log = HashChainAuditLog(path)
    log.append("task.submitted", actor="user", task_id="task_1")
    log.append("artifact.created", actor="system", task_id="task_1", artifact_id="art_1")
    assert log.verify()["valid"] is True

    lines = path.read_text(encoding="utf-8").splitlines()
    item = json.loads(lines[0])
    item["actor"] = "tampered"
    lines[0] = json.dumps(item, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert log.verify()["valid"] is False
