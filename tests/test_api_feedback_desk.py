from pathlib import Path
import json

import pytest
from fastapi.testclient import TestClient

from onebridge.api import create_app
from onebridge.config import Settings
from onebridge.feedback_desk import FeedbackDeskReadOnlyAdapter
from onebridge.identity import IdentityStore


ALLOWED_WORKSPACE = "ws_feedback_allowed"
OTHER_WORKSPACE = "ws_feedback_other"


class FakeMCPClient:
    def __init__(self, *, fail_tool: str | None = None):
        self.fail_tool = fail_tool
        self.calls: list[tuple[str, dict]] = []

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name == self.fail_tool:
            from onebridge.mcp_http import McpHttpError
            raise McpHttpError("upstream_unavailable")

        if name == "list_reports":
            payload = {
                "project": "demo",
                "reports": [
                    {"public_id": "r-1", "status": "new", "title": "Crash on launch"}
                ],
            }
        elif name == "get_report":
            payload = {
                "public_id": arguments["report"],
                "status": "new",
                "untrusted_content": {
                    "title": "Crash on launch",
                    "description": "App crashes after opening.",
                    "messages": [],
                    "attachments": [],
                },
                "handoff_notes": [],
            }
        else:
            raise AssertionError(f"Unexpected MCP tool: {name}")

        return {
            "content": [
                {"type": "text", "text": json.dumps(payload, ensure_ascii=False)}
            ]
        }


def make_settings(tmp_path: Path, *, allowed_workspaces=(ALLOWED_WORKSPACE,)):
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'onebridge.db'}",
        state_root=tmp_path,
        storage_backend="local",
        storage_root=tmp_path / "objects",
        checkpoint_root=tmp_path / "checkpoints",
        audit_log=tmp_path / "audit.jsonl",
        identity_db=tmp_path / "identity.db",
        require_api_key=True,
        feedback_desk_allowed_workspaces=allowed_workspaces,
    )


def make_client(tmp_path, monkeypatch, *, allowed_workspaces=(ALLOWED_WORKSPACE,), fail_tool=None):
    config = make_settings(tmp_path, allowed_workspaces=allowed_workspaces)
    adapter = FeedbackDeskReadOnlyAdapter(
        client=FakeMCPClient(fail_tool=fail_tool),
        project_slug="demo",
    )
    monkeypatch.setattr(
        "onebridge.api.build_feedback_desk_adapter",
        lambda _settings: adapter,
    )
    client = TestClient(create_app(config))
    identities = IdentityStore(config.identity_db)
    identities.create_workspace("Allowed", workspace_id=ALLOWED_WORKSPACE)
    identities.create_workspace("Other", workspace_id=OTHER_WORKSPACE)
    _, allowed_key = identities.create_api_key(ALLOWED_WORKSPACE, "feedback-test")
    _, other_key = identities.create_api_key(OTHER_WORKSPACE, "other-test")
    return client, adapter, f"Bearer {allowed_key}", f"Bearer {other_key}"


def test_feedback_desk_list_requires_authentication(tmp_path, monkeypatch):
    client, _, _, _ = make_client(tmp_path, monkeypatch)

    response = client.get("/api/v1/integrations/feedback-desk/reports")

    assert response.status_code == 401


def test_feedback_desk_list_rejects_workspace_outside_allowlist(tmp_path, monkeypatch):
    client, _, _, other_auth = make_client(tmp_path, monkeypatch)

    response = client.get(
        "/api/v1/integrations/feedback-desk/reports",
        headers={"Authorization": other_auth},
    )

    assert response.status_code == 403


def test_feedback_desk_list_imports_only_via_read_tool(tmp_path, monkeypatch):
    client, adapter, allowed_auth, _ = make_client(tmp_path, monkeypatch)

    response = client.get(
        "/api/v1/integrations/feedback-desk/reports?status=new&unread=true",
        headers={"Authorization": allowed_auth},
    )

    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["reports"][0]["public_id"] == "r-1"
    assert adapter.client.calls == [
        (
            "list_reports",
            {
                "project": "demo",
                "status": ["new"],
                "unread": True,
            },
        )
    ]


def test_feedback_desk_report_import_creates_reviewable_task(tmp_path, monkeypatch):
    client, adapter, allowed_auth, _ = make_client(tmp_path, monkeypatch)

    response = client.post(
        "/api/v1/integrations/feedback-desk/reports/r-1/tasks",
        headers={"Authorization": allowed_auth},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "feedback-desk"
    assert body["source_report_ref"] == "r-1"
    assert body["task"]["status"] == "queued"
    assert adapter.client.calls == [
        ("get_report", {"project": "demo", "report": "r-1"})
    ]


def test_feedback_desk_upstream_failure_is_sanitized(tmp_path, monkeypatch):
    client, _, allowed_auth, _ = make_client(
        tmp_path,
        monkeypatch,
        fail_tool="list_reports",
    )

    response = client.get(
        "/api/v1/integrations/feedback-desk/reports",
        headers={"Authorization": allowed_auth},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "Feedback Desk report listing failed"
    assert "upstream_unavailable" not in response.text


def test_feedback_desk_requires_workspace_allowlist_even_with_valid_key(tmp_path, monkeypatch):
    client, _, allowed_auth, _ = make_client(
        tmp_path,
        monkeypatch,
        allowed_workspaces=(),
    )

    response = client.get(
        "/api/v1/integrations/feedback-desk/reports",
        headers={"Authorization": allowed_auth},
    )

    assert response.status_code == 403
    assert "allowlist" in response.json()["detail"]
