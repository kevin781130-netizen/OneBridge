import sys
from pathlib import Path

import pytest

from onebridge.adapters.process import ProcessAdapterError, run_adapter_process


def test_process_adapter_persists_redacted_logs(tmp_path: Path):
    result = run_adapter_process(
        sys.executable,
        ["-c", "print('token=abcdef')"],
        cwd=tmp_path,
        log_dir=tmp_path / "logs",
        timeout_seconds=5,
    )
    assert result.exit_code == 0
    raw = result.stdout_path.read_text(encoding="utf-8")
    assert "abcdef" not in raw
    assert "<redacted>" in raw


def test_process_adapter_rejects_nonzero_exit(tmp_path: Path):
    with pytest.raises(ProcessAdapterError):
        run_adapter_process(
            sys.executable,
            ["-c", "raise SystemExit(3)"],
            cwd=tmp_path,
            log_dir=tmp_path / "logs",
            timeout_seconds=5,
        )
