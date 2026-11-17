"""Dependency-free localhost tests for operator-triggered OneBridge result sync."""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "integrations/paperclip-onebridge/scripts"
sys.path.insert(0, str(SCRIPTS))
import http_bridge as hb
import sync_result as rs


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def send(self, status, obj):
        raw = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@contextmanager
def serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(srv.server_port)
    finally:
        srv.shutdown()
        thread.join(timeout=2)
        srv.server_close()


class ResultSyncTests(unittest.TestCase):
    def setup_stack(self, *, status="queued", artifacts=None, tenant="tenant-1",
                    issue_status="todo", assignee="agent-1"):
        from contextlib import ExitStack
        ctx = ExitStack()
        self.addCleanup(ctx.close)
        issue = {"id": "issue-1", "companyId": "company-1", "assigneeAgentId": assignee,
                 "title": "Write launch copy", "description": "Keep it verifiable.",
                 "status": issue_status}
        state = {"status": status, "artifacts": artifacts or [], "tenant": tenant,
                 "products": [], "writes": [], "ob_reads": 0}
        class PC(Handler):
            def do_GET(self):
                if self.path == "/api/issues/issue-1":
                    self.send(200, issue)
                elif self.path == "/api/issues/issue-1/work-products":
                    if self.headers.get("Authorization") != "Bearer paperclip-write-key":
                        self.send(403, {"error": "forbidden"})
                    else:
                        self.send(200, state["products"])
                else:
                    self.send(404, {"error": "not_found"})

            def do_POST(self):
                if self.headers.get("Authorization") != "Bearer paperclip-write-key":
                    self.send(403, {"error": "forbidden"})
                    return
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                data.update({"id": "wp-1", "issueId": "issue-1"})
                state["products"].append(data)
                state["writes"].append(("POST", data.copy()))
                self.send(201, data)

            def do_PATCH(self):
                if self.headers.get("Authorization") != "Bearer paperclip-write-key":
                    self.send(403, {"error": "forbidden"})
                    return
                if self.path != "/api/work-products/wp-1":
                    self.send(404, {"error": "not_found"})
                    return
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                state["products"][0].update(data)
                state["writes"].append(("PATCH", data.copy()))
                self.send(200, state["products"][0])

        class OB(Handler):
            def do_GET(self):
                state["ob_reads"] += 1
                path = self.path
                if path == "/api/v1/auth/me":
                    self.send(200, {"workspace_id": state["tenant"]})
                elif path.startswith("/api/v1/tasks/") and path.endswith("/artifacts"):
                    self.send(200, state["artifacts"])
                elif path.startswith("/api/v1/tasks/"):
                    tid = path.removeprefix("/api/v1/tasks/")
                    self.send(200, {"task_id": tid, "project_id": state["project_id"],
                                    "status": state["status"]})
                else:
                    self.send(404, {})

        pc = ctx.enter_context(serve(PC))
        ob = ctx.enter_context(serve(OB))
        client = hb.Client(hb.Config(pc, "paperclip-read-key", "company-1",
                                    ob, "onebridge-key", "tenant-1", "x" * 40))
        expected = rs.contract_for_issue(client, "issue-1", "agent-1")
        state["project_id"] = expected["project_id"]
        state["task_id"] = expected["task_id"]
        return client, state

    def test_preview_never_writes_to_paperclip(self):
        client, state = self.setup_stack()
        result = rs.sync(client, "issue-1", "agent-1")
        self.assertEqual(result["mode"], "preview")
        self.assertEqual(result["onebridge_status"], "queued")
        self.assertEqual(state["writes"], [])

    def test_apply_creates_single_card_and_replay_is_noop(self):
        client, state = self.setup_stack(status="waiting_approval")
        result = rs.sync(client, "issue-1", "agent-1",
                         apply=True, write_key="paperclip-write-key")
        self.assertEqual(result["operation"], "created")
        self.assertTrue(result["updated"])
        p = state["products"][0]
        self.assertEqual(p["externalId"], state["task_id"])
        self.assertIn("waiting_approval", p["summary"])
        self.assertEqual(p["reviewState"], "none")
        self.assertEqual(p["status"], "active")
        second = rs.sync(client, "issue-1", "agent-1",
                         apply=True, write_key="paperclip-write-key")
        self.assertEqual(second["operation"], "unchanged")
        self.assertEqual(len(state["writes"]), 1)

    def test_status_change_patches_existing_card_not_issue_state(self):
        client, state = self.setup_stack()
        rs.sync(client, "issue-1", "agent-1", apply=True, write_key="paperclip-write-key")
        state["status"] = "failed"
        result = rs.sync(client, "issue-1", "agent-1",
                         apply=True, write_key="paperclip-write-key")
        self.assertEqual(result["operation"], "updated")
        self.assertEqual(state["products"][0]["status"], "failed")
        self.assertEqual(state["writes"][-1][0], "PATCH")
        self.assertEqual(state["products"][0]["reviewState"], "none")

    def test_manifest_digest_changes_and_is_bounded(self):
        client, state = self.setup_stack()
        rs.sync(client, "issue-1", "agent-1", apply=True, write_key="paperclip-write-key")
        before = state["products"][0]["metadata"]["manifestDigest"]
        state["artifacts"] = [{
            "task_id": state["task_id"], "project_id": state["project_id"],
            "artifact_id": "artifact-1", "kind": "content",
            "revision": 1, "status": "awaiting_review", "sha256": "a" * 64,
        }]
        result = rs.sync(client, "issue-1", "agent-1",
                         apply=True, write_key="paperclip-write-key")
        self.assertEqual(result["artifact_count"], 1)
        self.assertNotEqual(before, state["products"][0]["metadata"]["manifestDigest"])
        self.assertEqual(state["products"][0]["metadata"]["artifactCount"], 1)
        self.assertNotIn("artifact-1", state["products"][0]["summary"])

    def test_fail_closed_on_wrong_tenant_and_scope(self):
        client, state = self.setup_stack(tenant="tenant-other")
        with self.assertRaisesRegex(hb.BridgeError, "tenant_mismatch"):
            rs.sync(client, "issue-1", "agent-1", apply=True,
                    write_key="paperclip-write-key")
        self.assertEqual(state["writes"], [])
        state["tenant"] = "tenant-1"
        state["artifacts"] = [{
            "task_id": "different-task", "project_id": state["project_id"],
            "artifact_id": "bad", "kind": "content",
            "revision": 1, "status": "generated", "sha256": "a" * 64,
        }]
        with self.assertRaisesRegex(hb.BridgeError, "manifest_scope_mismatch"):
            rs.sync(client, "issue-1", "agent-1", apply=True,
                    write_key="paperclip-write-key")
        self.assertEqual(state["writes"], [])

    def test_write_key_is_required_even_for_real_api(self):
        client, state = self.setup_stack()
        with self.assertRaisesRegex(hb.BridgeError, "write_key_required"):
            rs.sync(client, "issue-1", "agent-1", apply=True)
        self.assertEqual(state["writes"], [])

    def test_rejects_wrong_agent_or_closed_issue(self):
        client, state = self.setup_stack(assignee="agent-2")
        with self.assertRaisesRegex(hb.BridgeError, "assignee_mismatch"):
            rs.sync(client, "issue-1", "agent-1")
        self.assertEqual(state["writes"], [])
        client, state = self.setup_stack(issue_status="done")
        with self.assertRaisesRegex(hb.BridgeError, "issue_closed"):
            rs.sync(client, "issue-1", "agent-1")
        self.assertEqual(state["writes"], [])

    def test_duplicate_work_products_fail_without_write(self):
        client, state = self.setup_stack()
        product = {
            "type": "preview_url", "provider": "onebridge", "externalId": state["task_id"],
            "id": "wp-1", "issueId": "issue-1",
        }
        state["products"] = [product, dict(product, id="wp-2")]
        with self.assertRaisesRegex(hb.BridgeError, "duplicate_onebridge_work_products"):
            rs.sync(client, "issue-1", "agent-1", apply=True, write_key="paperclip-write-key")
        self.assertEqual(state["writes"], [])


if __name__ == "__main__":
    unittest.main()
