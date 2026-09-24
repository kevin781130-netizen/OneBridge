import base64
from pathlib import Path

from onebridge.adapters.base import AdapterRequest
from onebridge.adapters.open_design import OpenDesignMCPAdapter
from onebridge.artifacts import LocalObjectStore


class FakeClient:
    session_id = "mcp-session"

    def __init__(self, result):
        self.result = result
        self.calls = []

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return self.result


def request(artifact_inputs=None):
    return AdapterRequest(
        task_id="task_1",
        project_id="prj_1",
        goal="Create landing page",
        operation="project.create",
        inputs={"brand": "Acme"},
        artifact_inputs=artifact_inputs or [],
    )


def test_open_design_collects_structured_artifact(tmp_path: Path):
    client = FakeClient({
        "structuredContent": {
            "artifacts": [{
                "kind": "design",
                "media_type": "text/html",
                "filename": "index.html",
                "text": "<html><body>Hello</body></html>",
            }]
        }
    })
    adapter = OpenDesignMCPAdapter(
        client=client,
        store=LocalObjectStore(tmp_path / "objects"),
    )
    result = adapter.execute(request())

    assert result.outputs[0].kind == "design"
    assert result.outputs[0].filename == "index.html"
    assert b"Hello" in result.outputs[0].content
    assert client.calls[0][0] == "render_design"


def test_open_design_accepts_base64_artifact(tmp_path: Path):
    client = FakeClient({
        "structuredContent": {
            "artifacts": [{
                "kind": "design",
                "media_type": "image/png",
                "filename": "preview.png",
                "content_base64": base64.b64encode(b"png-bytes").decode(),
            }]
        }
    })
    adapter = OpenDesignMCPAdapter(
        client=client,
        store=LocalObjectStore(tmp_path / "objects"),
    )
    result = adapter.execute(request())
    assert result.outputs[0].content == b"png-bytes"


def test_open_design_materializes_text_upstream_context(tmp_path: Path):
    store = LocalObjectStore(tmp_path / "objects")
    stored = store.put_bytes(
        b'{"headline":"Launch"}',
        filename="content.json",
    )
    client = FakeClient({
        "content": [{
            "type": "text",
            "text": "<html><h1>Launch</h1></html>",
        }]
    })
    adapter = OpenDesignMCPAdapter(client=client, store=store)
    adapter.execute(request([{
        "artifact_id": "art_content",
        "kind": "content",
        "media_type": "application/json",
        "sha256": stored.sha256,
        "uri": stored.uri,
    }]))

    artifact_context = client.calls[0][1]["artifacts"][0]
    assert artifact_context["text"] == '{"headline":"Launch"}'
