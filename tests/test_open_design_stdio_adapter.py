import io
import json
import zipfile
from pathlib import Path

import pytest

from onebridge.adapters.base import AdapterRequest
from onebridge.adapters.factory import build_adapter_registry
from onebridge.adapters.open_design import OpenDesignAdapterError, OpenDesignMCPAdapter
from onebridge.adapters.open_design_stdio import OpenDesignStdioAdapter
from onebridge.artifacts import LocalObjectStore
from onebridge.config import Settings


class FakeOdClient:
    session_id = None

    def __init__(self, responses):
        self.responses = responses
        self.calls = []
        self.closed = False

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        response = self.responses[name]
        if isinstance(response, list):
            response = response.pop(0)
        return {"content": [{"type": "text", "text": json.dumps(response)}]}

    def close(self):
        self.closed = True


def make_request():
    return AdapterRequest(
        task_id="task_1", project_id="prj_1", goal="Design a landing page",
        operation="project.create", inputs={"brand": "Acme"},
        artifact_inputs=[],
    )


def design_bundle(files=None, **overrides):
    payload = {
        "entryFile": "site/index.html",
        "truncated": False,
        "skippedFileCount": 0,
        "files": files if files is not None else [
            {"name": "site/index.html", "mime": "text/html",
             "binary": False, "content": '<link href="styles.css"><h1>Acme</h1>'},
            {"name": "site/styles.css", "mime": "text/css",
             "binary": False, "content": "h1 { color: black; }"},
        ],
    }
    payload.update(overrides)
    return payload


def responses(bundle=None, projects=None):
    return {
        "list_projects": {"projects": projects if projects is not None else []},
        "create_project": {"project": {"id": "created"}},
        "start_run": {"runId": "od_run_1"},
        "get_run": [
            {"status": "queued"},
            {"status": "running"},
            {"status": "succeeded", "entryFile": "site/index.html"},
        ],
        "get_artifact": bundle if bundle is not None else design_bundle(),
        "cancel_run": {"status": "cancelled"},
    }


def make_adapter(tmp_path: Path, client):
    return OpenDesignStdioAdapter(
        client_factory=lambda: client,
        store=LocalObjectStore(tmp_path / "objects"),
        poll_seconds=0.001,
    )


def test_native_stdio_contract_preserves_sources_and_stable_id(tmp_path: Path):
    client = FakeOdClient(responses())
    adapter = make_adapter(tmp_path, client)
    result = adapter.execute(make_request())
    assert result.external_task_id == "od_run_1"
    assert [o.filename for o in result.outputs] == ["open_design_sources.zip"]
    assert result.outputs[0].kind == "design"
    assert result.outputs[0].media_type == "application/zip"
    assert result.metadata["entry_file"] == "site/index.html"
    assert result.metadata["archive"] is True
    with zipfile.ZipFile(io.BytesIO(result.outputs[0].content)) as z:
        assert sorted(z.namelist()) == ["site/index.html", "site/styles.css"]
        assert b"color: black" in z.read("site/styles.css")
    names = [name for name, _ in client.calls]
    assert names == [
        "list_projects", "create_project", "start_run",
        "get_run", "get_run", "get_run", "get_artifact",
    ]
    project_id = adapter._project_id("task_1")
    assert client.calls[1][1]["id"] == project_id
    started = client.calls[2][1]
    assert started["project"] == project_id
    assert started["requestId"]
    assert "Acme" in started["prompt"]
    assert client.calls[-1][1] == {
        "project": project_id, "include": "all",
        "maxBytes": 18 * 1024 * 1024, "entry": "site/index.html",
    }
    assert client.closed is True


def test_single_text_file_remains_previewable(tmp_path: Path):
    bundle = design_bundle(files=[
        {"name": "site/index.html", "mime": "text/html",
         "binary": False, "content": "<h1>Only file</h1>"},
    ])
    client = FakeOdClient(responses(bundle))
    result = make_adapter(tmp_path, client).execute(make_request())
    assert len(result.outputs) == 1
    assert result.outputs[0].filename == "index.html"
    assert result.outputs[0].media_type == "text/html"
    assert result.metadata["archive"] is False


def test_existing_project_is_reused_and_start_request_id_is_stable(tmp_path: Path):
    first = FakeOdClient(responses())
    adapter = make_adapter(tmp_path, first)
    adapter.execute(make_request())
    request_id = next(args["requestId"] for name, args in first.calls if name == "start_run")

    second = FakeOdClient(responses(projects=[{"id": adapter._project_id("task_1")}]))
    adapter2 = make_adapter(tmp_path, second)
    adapter2.execute(make_request())
    assert "create_project" not in [name for name, _ in second.calls]
    assert next(args["requestId"] for name, args in second.calls if name == "start_run") == request_id


@pytest.mark.parametrize("bundle,reason", [
    (design_bundle(truncated=True), "incomplete_bundle"),
    (design_bundle(skippedFileCount=1), "incomplete_bundle"),
    (design_bundle(entryFile="../secret"), "unsafe_path"),
    (design_bundle(entryFile="missing.html"), "missing_entry"),
    (design_bundle(files=[
        {"name": "site/index.html", "mime": "text/html",
         "binary": True, "content": None},
    ]), "binary_file_unsupported"),
    (design_bundle(files=[
        {"name": "site/index.html", "mime": "text/html",
         "content": "<html>x</html>"},
        {"name": "site/index.html", "mime": "text/html",
         "content": "<html>y</html>"},
    ]), "duplicate_path"),
])
def test_incomplete_or_unsafe_bundle_fails_closed(tmp_path: Path, bundle, reason):
    client = FakeOdClient(responses(bundle))
    with pytest.raises(OpenDesignAdapterError, match=reason):
        make_adapter(tmp_path, client).execute(make_request())
    assert client.closed


def test_upstream_failed_run_never_fetches_or_retries(tmp_path: Path):
    state = responses()
    state["get_run"] = [{"status": "failed"}]
    client = FakeOdClient(state)
    with pytest.raises(OpenDesignAdapterError, match="run_failed"):
        make_adapter(tmp_path, client).execute(make_request())
    names = [name for name, _ in client.calls]
    assert names.count("start_run") == 1
    assert "get_artifact" not in names


def test_cancel_run_uses_explicit_id(tmp_path: Path):
    client = FakeOdClient(responses())
    make_adapter(tmp_path, client).cancel("run_55")
    assert client.calls == [("cancel_run", {"runId": "run_55"})]
    assert client.closed


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'db.sqlite'}",
        state_root=tmp_path, storage_root=tmp_path / "objects",
        checkpoint_root=tmp_path / "checkpoints",
        audit_log=tmp_path / "audit.jsonl", identity_db=tmp_path / "identity.db",
    )


def test_opt_in_factory_and_legacy_http_unchanged(tmp_path: Path):
    cfg = make_settings(tmp_path)
    cfg.open_design_mcp_url = None
    cfg.open_design_stdio_command = ("node", "/opt/od.mjs", "mcp")
    store = LocalObjectStore(tmp_path / "objects")
    assert isinstance(build_adapter_registry(cfg, store=store).get("open_design"), OpenDesignStdioAdapter)

    cfg.open_design_stdio_command = ()
    cfg.open_design_mcp_url = "http://127.0.0.1:9000/mcp"
    assert isinstance(build_adapter_registry(cfg, store=store).get("open_design"), OpenDesignMCPAdapter)


def test_factory_rejects_both_transports(tmp_path: Path):
    cfg = make_settings(tmp_path)
    cfg.open_design_stdio_command = ("node", "/opt/od.mjs", "mcp")
    cfg.open_design_mcp_url = "http://127.0.0.1:9000/mcp"
    with pytest.raises(ValueError, match="mutually exclusive"):
        build_adapter_registry(cfg, store=LocalObjectStore(tmp_path / "objects"))
