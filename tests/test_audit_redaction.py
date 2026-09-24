from pathlib import Path

from onebridge.audit import HashChainAuditLog


def test_audit_redacts_secret_payload(tmp_path: Path):
    path = tmp_path / "audit.jsonl"
    log = HashChainAuditLog(path)
    event = log.append(
        "adapter.call",
        actor="worker",
        payload={
            "api_key": "top-secret",
            "message": "authorization=Bearer abc123",
        },
    )
    raw = path.read_text(encoding="utf-8")
    assert "top-secret" not in raw
    assert "abc123" not in raw
    assert event.payload["api_key"] == "<redacted>"
    assert log.verify()["valid"] is True
