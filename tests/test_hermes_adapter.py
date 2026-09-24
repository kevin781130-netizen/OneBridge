import json
from pathlib import Path

import pytest

from onebridge.adapters.base import AdapterRequest
from onebridge.adapters.hermes import HermesAdapterError, HermesCommandAdapter
from onebridge.artifacts import LocalObjectStore
from onebridge.workers.supervisor import SupervisorResult


def request() -> AdapterRequest:
    return AdapterRequest(
        task_id="task_1",
        project_id="prj_1",
        goal="Build tested implementation",
        operation="project.create",
    )


def fake_success_runner(
    argv,
    *,
    cwd,
    workspace_root,
    env,
    timeout_seconds,
    policy,
):
    output_flag = argv.index("--output")
    output = Path(argv[output_flag + 1])
    output.mkdir(parents=True, exist_ok=True)
    (output / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (output / "tests.json").write_text(
        '{"passed": true}\n',
        encoding="utf-8",
    )
    (output / "result.json").write_text(
        json.dumps({
            "outputs": [
                {
                    "kind": "code",
                    "media_type": "text/x-python",
                    "filename": "app.py",
                },
                {
                    "kind": "test_report",
                    "media_type": "application/json",
                    "filename": "tests.json",
                },
            ]
        }),
        encoding="utf-8",
    )
    return SupervisorResult(
        returncode=0,
        output="ok",
        duration_seconds=0.1,
        termination_reason=None,
        output_bytes=2,
        output_truncated=False,
        workspace_growth_bytes=100,
        workspace_new_files=3,
    )


def test_hermes_command_adapter_collects_code_and_test_report(tmp_path: Path):
    adapter = HermesCommandAdapter(
        executable="hermes-test",
        arguments=("--request", "{request}", "--output", "{output}"),
        store=LocalObjectStore(tmp_path / "objects"),
        require_strong_sandbox=False,
        workspace_parent=tmp_path / "work",
        runner=fake_success_runner,
    )
    result = adapter.execute(request())
    assert [item.kind for item in result.outputs] == ["code", "test_report"]
    assert result.outputs[0].filename == "app.py"
    assert result.metadata["sandbox_backend"] == "none"


def test_hermes_requires_result_manifest(tmp_path: Path):
    def runner(argv, **kwargs):
        return SupervisorResult(
            returncode=0,
            output="ok",
            duration_seconds=0.1,
            termination_reason=None,
            output_bytes=2,
            output_truncated=False,
            workspace_growth_bytes=0,
            workspace_new_files=0,
        )

    adapter = HermesCommandAdapter(
        executable="hermes-test",
        arguments=("--request", "{request}", "--output", "{output}"),
        store=LocalObjectStore(tmp_path / "objects"),
        require_strong_sandbox=False,
        workspace_parent=tmp_path / "work",
        runner=runner,
    )
    with pytest.raises(HermesAdapterError):
        adapter.execute(request())
