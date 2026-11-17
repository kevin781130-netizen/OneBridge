"""Real localhost HTTP round-trip tests; no Paperclip, OneBridge, Hermes or model required."""
from __future__ import annotations

import importlib.util
import json
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


PATH = Path(__file__).resolve().parents[1] / "integrations/paperclip-onebridge/scripts/http_bridge.py"
SPEC = importlib.util.spec_from_file_location("paperclip_http_bridge", PATH)
assert SPEC is not None and SPEC.loader is not None
bridge = importlib.util.module_from_spec(SPEC)
import sys
sys.modules[SPEC.name] = bridge
SPEC.loader.exec_module(bridge)

TOKEN = "t" * 40
ISSUE = {
    "id": "issue-1", "companyId": "company-1", "assigneeAgentId": "agent-1",
    "title": "Make a verified landing page copy",
    "description": "Cite sources and avoid unsupported promises",
    "status": "todo",
}
EVENT = {
    "runId": "run-1", "agentId": "agent-1", "companyId": "company-1",
    "context": {"taskId": "issue-1", "wakeReason": "assignment"},
}


def respond(handler, code, payload):
    raw = json.dumps(payload).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


@contextmanager
def serving(handler_class):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    worker.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port)
    finally:
        server.shutdown()
        worker.join(timeout=2)
        server.server_close()


class QuietHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def fake_paperclip(issue):
    class Handler(QuietHandler):
        calls = []
        def do_GET(self):
            self.calls.append((self.path, self.headers.get("Authorization")))
            if self.path == "/api/issues/issue-1":
                respond(self, 200, issue)
            else:
                respond(self, 404, {"error": "not_found"})
    return Handler


def fake_onebridge(state):
    class Handler(QuietHandler):
        def do_GET(self):
            state["calls"].append(("GET", self.path, self.headers.get("Authorization")))
            if self.path == "/health":
                respond(self, 200, {"ok": True, "auth_required": True, "task_queue_enabled": True})
            elif self.path == "/api/v1/auth/me":
                respond(self, 200, {"workspace_id": state.get("workspace", "ws-1")})
            elif self.path == "/api/v1/adapters":
                respond(self, 200, [{
                    "name": "flowise", "version": state.get("adapter_version", "real-1"),
                    "health": {"status": "healthy"}, "capabilities": ["content"],
                }])
            elif self.path.startswith("/api/v1/tasks/"):
                task_id = self.path.removeprefix("/api/v1/tasks/")
                payload = state["tasks"].get(task_id)
                if payload is None:
                    respond(self, 404, {"error": "not_found"})
                else:
                    respond(self, 200, {
                        "task_id": task_id, "project_id": payload["project_id"], "status": "queued",
                    })
            else:
                respond(self, 404, {})
        def do_POST(self):
            state["calls"].append(("POST", self.path, self.headers.get("Authorization")))
            if self.path != "/api/v1/tasks":
                respond(self, 404, {})
                return
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["posted"].append(data)
            if data["task_id"] in state["tasks"]:
                respond(self, 409, {"detail": "task already exists"})
            else:
                state["tasks"][data["task_id"]] = data
                respond(self, 200, {"task_id": data["task_id"], "status": "queued"})
    return Handler


def post_event(url, event=EVENT, *, token=TOKEN, content_type="application/json"):
    request = urllib.request.Request(
        url + "/paperclip/dispatch",
        json.dumps(event).encode("utf-8"),
        {"Authorization": "Bearer " + token, "Content-Type": content_type},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as res:
            return res.status, json.load(res)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


class HttpBridgeTests(unittest.TestCase):
    def setup_stack(self, issue=None, *, live=True, workspace="ws-1", adapter_version="real-1"):
        from contextlib import ExitStack
        stack = ExitStack()
        self.addCleanup(stack.close)
        paperclip_handler = fake_paperclip(dict(ISSUE if issue is None else issue))
        state = {"calls": [], "posted": [], "tasks": {}, "workspace": workspace,
                 "adapter_version": adapter_version}
        pc = stack.enter_context(serving(paperclip_handler))
        ob = stack.enter_context(serving(fake_onebridge(state)))
        config = bridge.Config(
            pc, "paperclip-read-only-key", "company-1",
            ob, "onebridge-workspace-key", "ws-1", TOKEN, "content", live,
        )
        endpoint = stack.enter_context(serving(bridge.make_handler(bridge.Client(config))))
        return endpoint, state, paperclip_handler

    def test_real_http_round_trip_and_idempotent_replay(self):
        url, state, pc = self.setup_stack()
        status, result = post_event(url)
        self.assertEqual(status, 202)
        self.assertTrue(result["submitted"])
        self.assertEqual(result["mode"], "live")
        self.assertFalse(result["reused"])
        self.assertEqual(len(state["posted"]), 1)
        contract = state["posted"][0]
        self.assertEqual(contract["input"]["required_outputs"], ["content"])
        self.assertEqual(contract["context"]["tenant_id"], "ws-1")
        self.assertEqual(contract["policy"], {"network": "restricted", "approval": "before_publish"})
        self.assertIn("verified landing page", contract["input"]["goal"])
        self.assertEqual(contract["task_id"], result["task_id"])
        self.assertEqual(pc.calls[0][1], "Bearer paperclip-read-only-key")
        self.assertTrue(all(call[2] == "Bearer onebridge-workspace-key" for call in state["calls"]))

        duplicate = dict(EVENT, runId="another-heartbeat-run")
        status2, result2 = post_event(url, duplicate)
        self.assertEqual(status2, 202)
        self.assertTrue(result2["reused"])
        self.assertEqual(result2["task_id"], result["task_id"])
        self.assertEqual(len(state["tasks"]), 1)

    def test_dry_run_does_not_call_onebridge(self):
        url, state, _ = self.setup_stack(live=False)
        status, result = post_event(url)
        self.assertEqual(status, 202)
        self.assertEqual(result["mode"], "dry_run")
        self.assertFalse(result["submitted"])
        self.assertEqual(state["calls"], [])

    def test_webhook_auth_and_invalid_content_fail_closed(self):
        url, state, _ = self.setup_stack()
        self.assertEqual(post_event(url, token="wrong")[0], 401)
        self.assertEqual(post_event(url, content_type="text/plain")[0], 415)
        self.assertEqual(post_event(url, {"runId": "r", "agentId": "a", "context": {}})[0], 400)
        self.assertEqual(state["posted"], [])

    def test_issue_company_and_assignee_must_match_verified_source(self):
        for changed, expected in [
            ({"companyId": "another-company"}, "paperclip_issue_scope_mismatch"),
            ({"assigneeAgentId": "agent-2"}, "paperclip_issue_assignee_mismatch"),
            ({"status": "done"}, "paperclip_issue_closed"),
        ]:
            with self.subTest(changed=changed):
                url, state, _ = self.setup_stack(issue={**ISSUE, **changed})
                status, result = post_event(url)
                self.assertIn(status, (403, 409))
                self.assertEqual(result["error"], expected)
                self.assertEqual(state["posted"], [])

    def test_upstream_tenant_and_adapter_gates(self):
        for kwargs, expected_status, expected_code in [
            ({"workspace": "ws-other"}, 403, "onebridge_tenant_mismatch"),
            ({"adapter_version": "mock-v1"}, 503, "real_output_adapter_unavailable"),
        ]:
            with self.subTest(kwargs=kwargs):
                url, state, _ = self.setup_stack(**kwargs)
                status, result = post_event(url)
                self.assertEqual((status, result["error"]), (expected_status, expected_code))
                self.assertEqual(state["posted"], [])

    def test_rejects_non_loopback_configuration_and_short_token(self):
        for host in ("https://example.com", "http://192.168.0.5:3100",
                     "http://user:secret@127.0.0.1:3100", "http://127.0.0.1:3100/api"):
            with self.subTest(host=host):
                with self.assertRaises(bridge.BridgeError):
                    bridge.loopback_base(host)
        env = {
            "PAPERCLIP_BASE_URL": "http://127.0.0.1:3100",
            "PAPERCLIP_READ_API_KEY": "read-token",
            "PAPERCLIP_COMPANY_ID": "company-1",
            "ONEBRIDGE_BASE_URL": "http://127.0.0.1:8080",
            "ONEBRIDGE_API_KEY": "write-token",
            "ONEBRIDGE_TENANT_ID": "ws-1",
            "PAPERCLIP_BRIDGE_TOKEN": "short",
        }
        with self.assertRaisesRegex(bridge.BridgeError, "minimum_32"):
            bridge.Config.from_env(env)


if __name__ == "__main__":
    unittest.main()
