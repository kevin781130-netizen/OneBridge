from __future__ import annotations

import importlib.util
import io
import json
import urllib.error
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "integrations"
    / "paperclip-onebridge"
    / "scripts"
    / "onebridge.py"
)
SPEC = importlib.util.spec_from_file_location("paperclip_onebridge_bridge", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


class FakeResponse:
    def __init__(self, payload, status=200):
        self.status = status
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeOpener:
    def __init__(self, handlers):
        self.handlers = handlers
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        handler = self.handlers[(request.method, request.full_url.split("?", 1)[0])]
        result = handler(request, timeout)
        if isinstance(result, Exception):
            raise result
        return result


def env(**extra):
    result = {
        "ONEBRIDGE_BASE_URL": "http://127.0.0.1:8080",
        "ONEBRIDGE_API_KEY": "ob_test_key",
        "ONEBRIDGE_TENANT_ID": "ws_test",
        "PAPERCLIP_API_URL": "http://127.0.0.1:3100",
        "PAPERCLIP_API_KEY": "pc_test_jwt",
        "PAPERCLIP_COMPANY_ID": "company-1",
        "PAPERCLIP_AGENT_ID": "agent-1",
        "PAPERCLIP_TASK_ID": "issue-1",
        "PAPERCLIP_RUN_ID": "run-1",
    }
    result.update(extra)
    return result


def test_rejects_remote_plain_http_and_url_credentials():
    try:
        bridge.normalize_base_url("http://example.com:8080")
    except bridge.BridgeError as exc:
        assert "https" in str(exc)
    else:
        raise AssertionError("remote HTTP must be rejected")

    try:
        bridge.normalize_base_url("http://user:pass@127.0.0.1:8080")
    except bridge.BridgeError as exc:
        assert "credentials" in str(exc)
    else:
        raise AssertionError("URL credentials must be rejected")


def test_normalize_paperclip_api_url_appends_api_once_and_rejects_remote_http():
    assert bridge.normalize_paperclip_api_url("http://127.0.0.1:3100") == "http://127.0.0.1:3100/api"
    assert bridge.normalize_paperclip_api_url("https://paperclip.example/api/") == "https://paperclip.example/api"
    try:
        bridge.normalize_paperclip_api_url("http://paperclip.example")
    except bridge.BridgeError as exc:
        assert "https" in str(exc)
    else:
        raise AssertionError("remote Paperclip HTTP must be rejected")


def test_paperclip_identity_uses_run_token_and_verifies_agent_company():
    url = "http://127.0.0.1:3100/api/agents/me"

    def identity(request, _timeout):
        assert request.get_header("Authorization") == "Bearer pc_test_jwt"
        return FakeResponse({"id": "agent-1", "companyId": "company-1"})

    opener = FakeOpener({("GET", url): identity})
    client = bridge.OneBridgeClient(env=env(), opener=opener)
    result = client.paperclip_identity()
    assert result == {
        "verified": True,
        "agent_context": True,
        "company_context": True,
        "run_context": True,
        "task_context_present": True,
    }


def test_paperclip_identity_rejects_wrong_agent_or_company():
    url = "http://127.0.0.1:3100/api/agents/me"
    opener = FakeOpener({
        ("GET", url): lambda _request, _timeout: FakeResponse(
            {"id": "agent-other", "companyId": "company-1"}
        )
    })
    client = bridge.OneBridgeClient(env=env(), opener=opener)
    try:
        client.paperclip_identity()
    except bridge.BridgeError as exc:
        assert "paperclip_agent_identity_mismatch" in str(exc)
    else:
        raise AssertionError("wrong Paperclip agent must be rejected")


def test_task_id_is_stable_across_paperclip_heartbeats():
    first = bridge.OneBridgeClient(env=env(), opener=object())
    second = bridge.OneBridgeClient(env=env(PAPERCLIP_RUN_ID="run-2"), opener=object())

    first_payload = first.build_task_payload("Prepare a verified brief", ["content"])
    second_payload = second.build_task_payload("Prepare a verified brief", ["content"])

    assert first_payload["task_id"] == second_payload["task_id"]
    assert first_payload["project_id"] == second_payload["project_id"]
    assert first_payload["metadata"]["request_fingerprint"] == second_payload["metadata"]["request_fingerprint"]


def test_different_goal_creates_a_different_task_id():
    client = bridge.OneBridgeClient(env=env(), opener=object())
    first = client.build_task_payload("Prepare brief A", ["content"])
    second = client.build_task_payload("Prepare brief B", ["content"])
    assert first["task_id"] != second["task_id"]


def test_preflight_requires_authenticated_durable_queue_and_real_adapter():
    client = bridge.OneBridgeClient(env=env(), opener=object())
    calls = []

    def request(method, path, body=None, *, api_key=None):
        calls.append((method, path))
        if path == "health":
            return {"ok": True, "auth_required": True, "task_queue_enabled": False}
        raise AssertionError("adapter registry must not be queried after failed health gate")

    client.request = request
    try:
        client.preflight(["content"])
    except bridge.BridgeError as exc:
        assert "durable_task_queue" in str(exc)
    else:
        raise AssertionError("preflight must fail without durable queue")
    assert calls == [("GET", "health")]


def test_preflight_rejects_onebridge_key_for_wrong_tenant():
    client = bridge.OneBridgeClient(env=env(), opener=object())

    def request(method, path, body=None, *, api_key=None):
        if path == "health":
            return {"ok": True, "auth_required": True, "task_queue_enabled": True}
        if path == "api/v1/auth/me":
            return {"workspace_id": "ws_someone_else"}
        raise AssertionError("adapter discovery must stop after tenant mismatch")

    client.request = request
    try:
        client.preflight(["content"])
    except bridge.BridgeError as exc:
        assert "onebridge_tenant_scope_mismatch" in str(exc)
    else:
        raise AssertionError("tenant mismatch must be blocked")


def test_preflight_blocks_hermes_until_worker_backend_is_qualified():
    adapter_registry = [
        {"name": "hermes", "version": "command-v1", "health": {"status": "healthy"}, "capabilities": ["code", "test_report"]}
    ]
    client = bridge.OneBridgeClient(env=env(), opener=object())

    def request(method, path, body=None, *, api_key=None):
        if path == "health":
            return {"ok": True, "auth_required": True, "task_queue_enabled": True}
        if path == "api/v1/auth/me":
            return {"workspace_id": "ws_test"}
        if path == "api/v1/adapters":
            return adapter_registry
        raise AssertionError(path)

    client.request = request
    try:
        client.preflight(["code"])
    except bridge.BridgeError as exc:
        assert "required_output_adapter_unavailable:code" in str(exc)
    else:
        raise AssertionError("Hermes worker must remain blocked until explicitly qualified")

    qualified = bridge.OneBridgeClient(env=env(ONEBRIDGE_HERMES_WORKER_QUALIFIED="1"), opener=object())
    qualified.request = request
    assert qualified.preflight(["code"])["available_outputs"]["code"] is True


def test_submit_recovers_duplicate_409_without_creating_second_task():
    base = "http://127.0.0.1:8080"
    task_id = "pc_" + "a" * 40
    project_id = "pcp_" + "b" * 32

    def health(_request, _timeout):
        return FakeResponse({"ok": True, "auth_required": True, "task_queue_enabled": True})

    def adapters(_request, _timeout):
        return FakeResponse([
            {
                "name": "flowise",
                "version": "1.0.0",
                "health": {"status": "healthy"},
                "capabilities": ["content"],
            }
        ])

    def duplicate(request, _timeout):
        body = json.loads(request.data.decode("utf-8"))
        assert body["context"]["tenant_id"] == "ws_test"
        assert body["policy"] == {"network": "restricted", "approval": "before_publish"}
        return urllib.error.HTTPError(
            request.full_url,
            409,
            "Conflict",
            {},
            io.BytesIO(b'{"detail":"task_id already exists"}'),
        )

    def existing(_request, _timeout):
        return FakeResponse({
            "task_id": task_id,
            "project_id": project_id,
            "status": "queued",
            "attempt": 1,
        })

    opener = FakeOpener({
        ("GET", "http://127.0.0.1:3100/api/agents/me"): lambda request, _timeout: FakeResponse(
            {"id": "agent-1", "companyId": "company-1"}
        ),
        ("GET", base + "/health"): health,
        ("GET", base + "/api/v1/auth/me"): lambda _request, _timeout: FakeResponse(
            {"workspace_id": "ws_test"}
        ),
        ("GET", base + "/api/v1/adapters"): adapters,
        ("POST", base + "/api/v1/tasks"): duplicate,
        ("GET", base + "/api/v1/tasks/" + task_id): existing,
    })
    client = bridge.OneBridgeClient(env=env(), opener=opener)
    client.build_task_payload = lambda *args, **kwargs: {
        "task_id": task_id,
        "project_id": project_id,
        "operation": "paperclip.issue.execute",
        "input": {"goal": "x", "required_outputs": ["content"]},
        "context": {"tenant_id": "ws_test"},
        "policy": {"network": "restricted", "approval": "before_publish"},
        "metadata": {},
    }

    result = client.submit("x", ["content"])
    assert result["task_id"] == task_id
    assert result["status"] == "queued"
    assert result["reused_existing"] is True
    assert [request.method for request in opener.requests].count("POST") == 1


def test_unreviewed_output_requires_explicit_operator_override():
    client = bridge.OneBridgeClient(env=env(), opener=object())
    try:
        client.build_task_payload("Create content", ["content"], approval="none")
    except bridge.BridgeError as exc:
        assert "operator_override" in str(exc)
    else:
        raise AssertionError("unreviewed output must be blocked by default")


def test_approval_requires_separate_operator_credentials():
    client = bridge.OneBridgeClient(env=env(), opener=object())
    try:
        client.approve("task-1", ["artifact-1"], "approve")
    except bridge.BridgeError as exc:
        assert "separate_operator" in str(exc)
    else:
        raise AssertionError("agent credentials must not approve their own artifacts")