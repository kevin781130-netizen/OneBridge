#!/usr/bin/env python3
"""Minimal, fail-closed Paperclip HTTP adapter -> OneBridge task dispatcher.

Standalone and stdlib-only. Default mode previews, never submits. Neither
Paperclip nor OneBridge source code needs modification.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping

MAX_BODY = 64 * 1024
MAX_RESPONSE = 256 * 1024
ALLOWED_OUTPUTS = {"content": "flowise", "design": "open_design", "code": "hermes", "test_report": "hermes"}


class BridgeError(Exception):
    def __init__(self, code: str, status: int = 400) -> None:
        self.code, self.status = code, status
        super().__init__(code)


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def loopback_base(raw: str) -> str:
    try:
        p = urllib.parse.urlsplit(raw)
        port = p.port  # Raises on invalid port.
    except ValueError as exc:
        raise BridgeError("invalid_loopback_url") from exc
    if (
        p.scheme != "http" or p.hostname not in {"127.0.0.1", "localhost", "::1"}
        or not port or p.username or p.password or p.query or p.fragment
        or p.path not in {"", "/"}
    ):
        raise BridgeError("loopback_url_required")
    return raw.rstrip("/")


def nonempty(value: Any, field: str, max_length: int = 200) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise BridgeError("invalid_" + field)
    return value.strip()


@dataclass(frozen=True)
class Config:
    paperclip_url: str
    paperclip_key: str
    paperclip_company: str
    onebridge_url: str
    onebridge_key: str
    tenant: str
    incoming_token: str
    output: str = "content"
    live: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Config":
        e = os.environ if env is None else env
        def required(key: str) -> str:
            return nonempty(e.get(key, ""), key, 1000)
        output = e.get("PAPERCLIP_BRIDGE_OUTPUT", "content")
        if output not in ALLOWED_OUTPUTS:
            raise BridgeError("unsupported_output")
        token = required("PAPERCLIP_BRIDGE_TOKEN")
        if len(token) < 32:
            raise BridgeError("bridge_token_minimum_32_characters")
        return cls(
            paperclip_url=loopback_base(required("PAPERCLIP_BASE_URL")),
            paperclip_key=required("PAPERCLIP_READ_API_KEY"),
            paperclip_company=required("PAPERCLIP_COMPANY_ID"),
            onebridge_url=loopback_base(required("ONEBRIDGE_BASE_URL")),
            onebridge_key=required("ONEBRIDGE_API_KEY"),
            tenant=required("ONEBRIDGE_TENANT_ID"),
            incoming_token=token,
            output=output,
            live=e.get("PAPERCLIP_BRIDGE_LIVE", "") == "1",
        )


class Client:
    def __init__(self, config: Config, opener: Any = None) -> None:
        self.config = config
        self.opener = opener or urllib.request.build_opener(NoRedirects())

    def json_request(self, origin: str, key: str, method: str, path: str,
                     payload: dict[str, Any] | None = None) -> Any:
        url = origin + path
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Accept": "application/json",
                "Authorization": "Bearer " + key,
                **({"Content-Type": "application/json"} if body is not None else {}),
            },
            method=method,
        )
        try:
            with self.opener.open(req, timeout=8) as res:
                raw = res.read(MAX_RESPONSE + 1)
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                raise BridgeError("task_conflict", 409) from None
            raise BridgeError("upstream_http_" + str(exc.code), 502) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise BridgeError("upstream_unreachable", 502) from exc
        if len(raw) > MAX_RESPONSE:
            raise BridgeError("upstream_response_too_large", 502)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise BridgeError("upstream_invalid_json", 502) from exc

    def get_issue(self, issue_id: str) -> dict[str, Any]:
        c = self.config
        obj = self.json_request(c.paperclip_url, c.paperclip_key, "GET",
                                "/api/issues/" + urllib.parse.quote(issue_id, safe=""))
        if not isinstance(obj, dict):
            raise BridgeError("invalid_paperclip_issue", 502)
        return obj

    def preflight(self) -> None:
        c = self.config
        health = self.json_request(c.onebridge_url, c.onebridge_key, "GET", "/health")
        if not isinstance(health, dict) or health.get("ok") is not True or (
            health.get("auth_required") is not True
            or health.get("task_queue_enabled") is not True
        ):
            raise BridgeError("onebridge_auth_or_durable_queue_unavailable", 503)
        identity = self.json_request(c.onebridge_url, c.onebridge_key, "GET", "/api/v1/auth/me")
        if not isinstance(identity, dict) or identity.get("workspace_id") != c.tenant:
            raise BridgeError("onebridge_tenant_mismatch", 403)
        adapters = self.json_request(c.onebridge_url, c.onebridge_key, "GET", "/api/v1/adapters")
        if not isinstance(adapters, list):
            raise BridgeError("onebridge_adapter_registry_invalid", 502)
        name = ALLOWED_OUTPUTS[c.output]
        available = any(
            isinstance(a, dict) and a.get("name") == name
            and isinstance(a.get("health"), dict)
            and a["health"].get("status") == "healthy"
            and not str(a.get("version", "")).startswith("mock-")
            and isinstance(a.get("capabilities"), list)
            and c.output in a["capabilities"]
            for a in adapters
        )
        if not available:
            raise BridgeError("real_output_adapter_unavailable", 503)
        # The Hermes command worker needs separate sandbox/runtime qualification.
        if c.output in {"code", "test_report"}:
            raise BridgeError("hermes_not_qualified_in_http_bridge", 503)

    def contract(self, event: dict[str, Any]) -> dict[str, Any]:
        c = self.config
        agent_id = nonempty(event.get("agentId"), "agent_id")
        run_id = nonempty(event.get("runId"), "run_id")
        context = event.get("context")
        if not isinstance(context, dict):
            raise BridgeError("invalid_context")
        issue_id = nonempty(context.get("taskId"), "task_id")
        if "companyId" in event and event["companyId"] != c.paperclip_company:
            raise BridgeError("company_mismatch", 403)

        issue = self.get_issue(issue_id)
        if issue.get("id") != issue_id or issue.get("companyId") != c.paperclip_company:
            raise BridgeError("paperclip_issue_scope_mismatch", 403)
        if issue.get("assigneeAgentId") != agent_id:
            raise BridgeError("paperclip_issue_assignee_mismatch", 403)
        if issue.get("status") in {"done", "cancelled", "canceled"}:
            raise BridgeError("paperclip_issue_closed", 409)
        title = nonempty(issue.get("title"), "issue_title", 500)
        desc = issue.get("description") or ""
        if not isinstance(desc, str):
            raise BridgeError("invalid_issue_description")
        goal = title if not desc.strip() else title + "\n\n" + desc.strip()
        if len(goal) > 20_000:
            raise BridgeError("issue_goal_too_large")

        stable = {
            "tenant": c.tenant, "company": c.paperclip_company, "issue": issue_id,
            "agent": agent_id, "output": c.output, "goal": goal,
        }
        fingerprint = hashlib.sha256(json.dumps(stable, sort_keys=True, ensure_ascii=False,
                                                  separators=(",", ":")).encode("utf-8")).hexdigest()
        project_id = "pch_" + hashlib.sha256(
            (c.tenant + ":" + c.paperclip_company + ":" + issue_id).encode("utf-8")
        ).hexdigest()[:32]
        return {
            "contract_version": "1.0",
            "task_id": "pch_" + fingerprint[:40],
            "project_id": project_id,
            "operation": "paperclip.issue.execute",
            "input": {"goal": goal, "required_outputs": [c.output]},
            "context": {
                "tenant_id": c.tenant,
                "user_id": "paperclip:" + agent_id,
                "channel": "paperclip_http",
                "conversation_id": issue_id,
            },
            "policy": {"network": "restricted", "approval": "before_publish"},
            "metadata": {
                "origin": "paperclip-http-adapter",
                "paperclip_issue_id": issue_id,
                "paperclip_agent_id": agent_id,
                "paperclip_company_id": c.paperclip_company,
                "paperclip_run_id": run_id,
                "fingerprint": fingerprint,
            },
        }

    def dispatch(self, event: dict[str, Any]) -> dict[str, Any]:
        contract = self.contract(event)
        result = {"ok": True, "task_id": contract["task_id"], "project_id": contract["project_id"]}
        if not self.config.live:
            return {**result, "mode": "dry_run", "submitted": False}
        self.preflight()
        c = self.config
        reused = False
        try:
            res = self.json_request(c.onebridge_url, c.onebridge_key,
                                    "POST", "/api/v1/tasks", contract)
        except BridgeError as exc:
            if exc.code != "task_conflict":
                raise
            res = self.json_request(c.onebridge_url, c.onebridge_key, "GET",
                                    "/api/v1/tasks/" + urllib.parse.quote(contract["task_id"], safe=""))
            reused = True
        if not isinstance(res, dict) or res.get("task_id") != contract["task_id"]:
            raise BridgeError("onebridge_task_response_mismatch", 502)
        if reused and res.get("project_id") != contract["project_id"]:
            raise BridgeError("onebridge_existing_project_mismatch", 409)
        return {**result, "mode": "live", "submitted": True, "reused": reused,
                "task_status": res.get("status")}


def make_handler(client: Client) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            # Don't log payloads, URLs with tokens or attacker-controlled strings.
            return

        def send_json(self, status: int, data: dict[str, Any]) -> None:
            raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            if self.path == "/health":
                self.send_json(200, {"ok": True, "mode": "live" if client.config.live else "dry_run"})
            else:
                self.send_json(404, {"ok": False, "error": "not_found"})

        def do_POST(self) -> None:
            if self.path != "/paperclip/dispatch":
                self.send_json(404, {"ok": False, "error": "not_found"})
                return
            auth = self.headers.get("Authorization", "")
            if not hmac.compare_digest(auth, "Bearer " + client.config.incoming_token):
                self.send_json(401, {"ok": False, "error": "unauthorized"})
                return
            try:
                n = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                n = 0
            if n < 1 or n > MAX_BODY:
                self.send_json(413, {"ok": False, "error": "invalid_body_size"})
                return
            if self.headers.get("Content-Type", "").split(";", 1)[0].lower() != "application/json":
                self.send_json(415, {"ok": False, "error": "json_required"})
                return
            try:
                event = json.loads(self.rfile.read(n).decode("utf-8"))
                if not isinstance(event, dict):
                    raise BridgeError("invalid_event")
                result = client.dispatch(event)
                self.send_json(202, result)
            except (UnicodeDecodeError, json.JSONDecodeError, BridgeError) as exc:
                if isinstance(exc, BridgeError):
                    self.send_json(exc.status, {"ok": False, "error": exc.code})
                else:
                    self.send_json(400, {"ok": False, "error": "invalid_json"})

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Paperclip HTTP -> OneBridge bridge (stdlib only)")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("invalid port")
    try:
        client = Client(Config.from_env())
    except BridgeError as exc:
        print(json.dumps({"ok": False, "error": exc.code}), file=sys.stderr)
        return 2
    # Explicit loopback binding only. Never expose tokens on external interfaces.
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(client))
    print("PAPERCLIP_HTTP_BRIDGE_READY mode=" + ("live" if client.config.live else "dry_run"),
          flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
