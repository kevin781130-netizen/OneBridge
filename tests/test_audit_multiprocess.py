from multiprocessing import Process
from pathlib import Path

from onebridge.audit import HashChainAuditLog


def append_events(path: str, actor: str, count: int) -> None:
    audit = HashChainAuditLog(path)
    for index in range(count):
        audit.append(
            "worker.event",
            actor=actor,
            payload={"index": index},
        )


def test_multiprocess_appends_keep_single_valid_hash_chain(tmp_path: Path):
    path = tmp_path / "audit.jsonl"
    processes = [
        Process(
            target=append_events,
            args=(str(path), f"worker-{index}", 20),
        )
        for index in range(3)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    result = HashChainAuditLog(path).verify()
    assert result["valid"] is True
    assert result["events"] == 60
