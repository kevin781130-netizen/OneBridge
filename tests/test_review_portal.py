from pathlib import Path

from fastapi.testclient import TestClient

from onebridge.api import create_app
from onebridge.config import Settings


def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'db.sqlite'}",
        state_root=tmp_path,
        storage_root=tmp_path / "objects",
        checkpoint_root=tmp_path / "checkpoints",
        audit_log=tmp_path / "audit.jsonl",
        identity_db=tmp_path / "identity.db",
    )


def test_review_portal_renders_without_embedding_task_data(tmp_path: Path):
    client = TestClient(create_app(settings(tmp_path)))
    response = client.get("/review/task_demo")
    assert response.status_code == 200
    assert "OneBridge Review Portal" in response.text
    assert "task_demo" in response.text
    assert "localStorage" not in response.text


def test_text_artifact_content_endpoint(tmp_path: Path):
    client = TestClient(create_app(settings(tmp_path)))
    created = client.post(
        "/api/v1/tasks",
        json={
            "input": {
                "goal": "Build page",
                "required_outputs": ["design"],
            },
            "context": {"tenant_id": "local", "user_id": "user"},
        },
    )
    task_id = created.json()["task_id"]
    assert client.post(f"/api/v1/tasks/{task_id}/run").status_code == 200

    artifacts = client.get(
        f"/api/v1/tasks/{task_id}/artifacts"
    ).json()
    design = next(item for item in artifacts if item["kind"] == "design")

    content = client.get(
        f"/api/v1/tasks/{task_id}/artifacts/{design['artifact_id']}/content"
    )
    assert content.status_code == 200
    assert content.json()["media_type"] == "text/html"
    assert "<html" in content.json()["content"]
