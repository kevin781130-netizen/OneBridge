import json

from onebridge.contracts import ApprovalRequest, TaskContract
from onebridge.openclaw_shim import OpenClawOneBridgeShim, ShimHttpResult


def contract() -> TaskContract:
    return TaskContract.model_validate({
        "input": {"goal": "Build page", "required_outputs": ["content"]},
        "context": {"tenant_id": "tenant", "user_id": "user"},
    })


def test_shim_maps_minimal_tool_surface():
    calls = []

    def transport(url, method, headers, body, timeout):
        calls.append({
            "url": url,
            "method": method,
            "headers": dict(headers),
            "body": json.loads(body) if body else None,
        })
        if url.endswith("/artifacts"):
            return ShimHttpResult(200, b"[]", "application/json")
        return ShimHttpResult(
            200,
            b'{"task_id":"task_1","status":"queued"}',
            "application/json",
        )

    shim = OpenClawOneBridgeShim(
        base_url="http://127.0.0.1:8000",
        api_key="ob_test_key",
        transport=transport,
    )

    shim.submit(contract())
    shim.status("task_1")
    shim.artifacts("task_1")
    shim.approve(
        "task_1",
        ApprovalRequest(
            artifact_ids=["art_1"],
            decision="approve",
            actor="user",
        ),
    )
    shim.retry("task_1")
    shim.cancel("task_1")

    assert [item["method"] for item in calls] == [
        "POST",
        "GET",
        "GET",
        "POST",
        "POST",
        "POST",
    ]
    assert all(
        item["headers"]["Authorization"] == "Bearer ob_test_key"
        for item in calls
    )
    assert calls[0]["url"].endswith("/api/v1/tasks")
    assert calls[-1]["url"].endswith("/api/v1/tasks/task_1/cancel")


def test_shim_rejects_remote_plain_http():
    import pytest

    with pytest.raises(ValueError):
        OpenClawOneBridgeShim(
            base_url="http://example.com:8000",
        )
