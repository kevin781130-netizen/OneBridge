import sys
from pathlib import Path

from onebridge.workers.supervisor import SupervisorPolicy, run_supervised


def test_supervisor_runs_bounded_command(tmp_path: Path):
    result = run_supervised(
        [sys.executable, "-c", "print('ok')"],
        cwd=tmp_path,
        workspace_root=tmp_path,
        timeout_seconds=5,
    )
    assert result.returncode == 0
    assert "ok" in result.output
    assert result.termination_reason is None


def test_supervisor_enforces_output_budget(tmp_path: Path):
    policy = SupervisorPolicy(
        retained_output_bytes=32,
        total_output_bytes=128,
        workspace_growth_bytes=None,
        max_new_files=None,
    )
    result = run_supervised(
        [sys.executable, "-c", "print('x' * 10000)"],
        cwd=tmp_path,
        workspace_root=tmp_path,
        timeout_seconds=5,
        policy=policy,
    )
    assert result.returncode == 125
    assert result.termination_reason == "output_budget_exceeded"
