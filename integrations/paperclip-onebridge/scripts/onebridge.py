#!/usr/bin/env python3
"""Small, dependency-free Hermes skill bridge for the OneBridge REST API."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping

_ALLOWED_OUTPUTS = {"content", "design", "code", "test_report"}
_OUTPUT_ADAPTER = {"content": "flowise", "design": "open_design", "code": "hermes", "test_report": "hermes"}
_TERMINAL_STATES = {"succeeded", "waiting_approval", "failed", "blocked", "cancelled"}

class BridgeError(RuntimeError):
    pass

class HttpStatusError(BridgeError):
    def __init__(self, status: int, payload: Any = None) -> None:
        self.status = status
        self.payload = payload
        super().__init__(f"onebridge_http_{status}")

class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def normalize_base_url(value: str) -> str:
    raw = str(value or "").strip()
    try:
        parsed = urllib.parse.urlsplit(raw)
    except ValueError as exc:
        raise BridgeError("onebridge_base_url_invalid") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise BridgeError("onebridge_base_url_must_be_http_or_https")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise BridgeError("onebridge_base_url_must_not_contain_credentials_query_or_fragment")
    if parsed.scheme == "http" and parsed.hostname.lower() not in {"localhost", "127.0.0.1", "::1"}:
        raise BridgeError("onebridge_remote_http_requires_https")
    return raw.rstrip("/")

def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

def _redact(value: str) -> str:
    import re
    result = value
    result = re.sub(r"(?i)Bearer\s+\S+", "Bearer [redacted]", result)
    result = re.sub(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*\S+", r"\1=[redacted]", result)
    return result[:2000]

def _json_or_empty(raw: bytes) -> Any:
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BridgeError("onebridge_invalid_json_response") from exc

class OneBridgeClient:
    def __init__(self, env: Mapping[str, str] | None = None, opener: Any | None = None, timeout_seconds: float = 15.0) -> None:
        self.env = dict(os.environ if env is None else env)
        self.base_url = normalize_base_url(self.env.get("ONEBRIDGE_BASE_URL", ""))
        self.api_key = self.env.get("ONEBRIDGE_API_KEY", "").strip()
        self.tenant_id = self.env.get("ONEBRIDGE_TENANT_ID", "").strip()
        if not self.api_key:
            raise BridgeError("ONEBRIDGE_API_KEY_required")
        if not self.tenant_id:
            raise BridgeError("ONEBRIDGE_TENANT_ID_required")
        self.opener = opener or urllib.request.build_opener(NoRedirectHandler())
        self.timeout_seconds = max(2.0, min(float(timeout_seconds), 30.0))

    def request(self, method: str, path: str, body: Mapping[str, Any] | None = None, *, api_key: str | None = None) -> Any:
        url = self.base_url + "/" + path.lstrip("/")
        headers = {"Accept": "application/json"}
        key = self.api_key if api_key is None else api_key
        if key:
            headers["Authorization"] = "Bearer " + key
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        try:
            with self.opener.open(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
                status = int(getattr(response, "status", 200))
        except urllib.error.HTTPError as exc:
            payload = _json_or_empty(exc.read())
            raise HttpStatusError(int(exc.code), payload) from None
        except urllib.error.URLError as exc:
            raise BridgeError("onebridge_transport_error") from exc
        except TimeoutError:
            raise BridgeError("onebridge_request_timeout") from None
        if not 200 <= status < 300:
            raise HttpStatusError(status, _json_or_empty(raw))
        return _json_or_empty(raw)

    def preflight(self, required_outputs: list[str] | None = None) -> dict[str, Any]:
        health = self.request("GET", "health")
        if not isinstance(health, dict) or health.get("ok") is not True:
            raise BridgeError("onebridge_health_check_failed")
        if health.get("auth_required") is not True:
            raise BridgeError("onebridge_api_key_auth_must_be_enabled")
        if health.get("task_queue_enabled") is not True:
            raise BridgeError("onebridge_durable_task_queue_must_be_enabled")
        raw_adapters = self.request("GET", "api/v1/adapters")
        if not isinstance(raw_adapters, list):
            raise BridgeError("onebridge_adapter_registry_response_invalid")
        adapters = {str(item.get("name")): item for item in raw_adapters if isinstance(item, dict) and item.get("name")}
        available: dict[str, bool] = {}
        for output, adapter_name in _OUTPUT_ADAPTER.items():
            adapter = adapters.get(adapter_name)
            adapter_health = adapter.get("health", {}) if isinstance(adapter, dict) else {}
            version = str(adapter.get("version", "")) if isinstance(adapter, dict) else ""
            capabilities = adapter.get("capabilities", []) if isinstance(adapter, dict) else []
            available[output] = bool(
                isinstance(adapter_health, dict)
                and adapter_health.get("status") == "healthy"
                and not version.startswith("mock-")
                and isinstance(capabilities, list)
                and output in capabilities
            )
            if output in {"code", "test_report"} and self.env.get("ONEBRIDGE_HERMES_WORKER_QUALIFIED") != "1":
                available[output] = False
        for output in required_outputs or []:
            if output not in _ALLOWED_OUTPUTS:
                raise BridgeError(f"unsupported_output:{output}")
            if not available.get(output, False):
                raise BridgeError(f"required_output_adapter_unavailable:{output}")
        return {"ok": True, "auth_required": True, "task_queue_enabled": True, "available_outputs": available}

    def build_task_payload(self, goal: str, required_outputs: list[str], *, operation: str = "paperclip.issue.execute", approval: str = "before_publish", dedupe_suffix: str = "") -> dict[str, Any]:
        clean_goal = str(goal or "").strip()
        if not clean_goal or len(clean_goal) > 20_000:
            raise BridgeError("goal_must_be_between_1_and_20000_characters")
        outputs = sorted(str(item).strip() for item in required_outputs)
        if not outputs or len(outputs) != len(set(outputs)):
            raise BridgeError("required_outputs_must_be_nonempty_and_unique")
        unsupported = sorted(set(outputs) - _ALLOWED_OUTPUTS)
        if unsupported:
            raise BridgeError("unsupported_output:" + ",".join(unsupported))
        if approval not in {"before_publish", "none"}:
            raise BridgeError("approval_mode_invalid")
        if approval == "none" and self.env.get("ONEBRIDGE_ALLOW_UNREVIEWED") != "1":
            raise BridgeError("unreviewed_output_requires_operator_override")
        company_id = self.env.get("PAPERCLIP_COMPANY_ID", "unknown").strip() or "unknown"
        agent_id = self.env.get("PAPERCLIP_AGENT_ID", "unknown").strip() or "unknown"
        issue_id = self.env.get("PAPERCLIP_TASK_ID", "").strip() or self.env.get("PAPERCLIP_ISSUE_ID", "").strip()
        if not issue_id:
            raise BridgeError("PAPERCLIP_TASK_ID_or_PAPERCLIP_ISSUE_ID_required")
        user_id = self.env.get("ONEBRIDGE_USER_ID", "").strip() or f"paperclip:{agent_id}"
        stable_request = {
            "tenant_id": self.tenant_id, "company_id": company_id, "agent_id": agent_id,
            "issue_id": issue_id, "user_id": user_id, "goal": clean_goal, "operation": operation,
            "required_outputs": outputs, "approval": approval, "network": "restricted",
            "dedupe_suffix": str(dedupe_suffix or ""),
        }
        fingerprint = hashlib.sha256(_canonical_json(stable_request).encode("utf-8")).hexdigest()
        project_seed = f"{self.tenant_id}:{company_id}:{issue_id}"
        project_id = "pcp_" + hashlib.sha256(project_seed.encode("utf-8")).hexdigest()[:32]
        return {
            "task_id": "pc_" + fingerprint[:40],
            "project_id": project_id,
            "operation": operation,
            "input": {"goal": clean_goal, "required_outputs": outputs},
            "context": {"tenant_id": self.tenant_id, "user_id": user_id, "channel": "paperclip", "conversation_id": issue_id},
            "policy": {"network": "restricted", "approval": approval},
            "metadata": {
                "origin": "paperclip-hermes-skill",
                "paperclip_company_id": company_id,
                "paperclip_agent_id": agent_id,
                "paperclip_issue_id": issue_id,
                "request_fingerprint": fingerprint,
            },
        }

    def submit(self, goal: str, required_outputs: list[str], *, operation: str = "paperclip.issue.execute", approval: str = "before_publish", dedupe_suffix: str = "") -> dict[str, Any]:
        payload = self.build_task_payload(goal, required_outputs, operation=operation, approval=approval, dedupe_suffix=dedupe_suffix)
        self.preflight(required_outputs)
        task_id = payload["task_id"]
        reused = False
        try:
            result = self.request("POST", "api/v1/tasks", payload)
        except HttpStatusError as exc:
            if exc.status != 409:
                raise
            result = self.status(task_id)
            reused = True
        if not isinstance(result, dict) or result.get("task_id") != task_id:
            raise BridgeError("onebridge_submit_response_mismatch")
        return {
            "task_id": task_id, "project_id": payload["project_id"],
            "status": result.get("status"), "attempt": result.get("attempt"),
            "execution_status": result.get("execution_status"), "reused_existing": reused,
        }

    def status(self, task_id: str) -> dict[str, Any]:
        result = self.request("GET", "api/v1/tasks/" + urllib.parse.quote(task_id, safe=""))
        if not isinstance(result, dict):
            raise BridgeError("onebridge_task_status_invalid")
        if isinstance(result.get("error"), str):
            result["error"] = _redact(result["error"])
        return result

    def artifacts(self, task_id: str) -> list[dict[str, Any]]:
        result = self.request("GET", "api/v1/tasks/" + urllib.parse.quote(task_id, safe="") + "/artifacts")
        if not isinstance(result, list):
            raise BridgeError("onebridge_artifacts_response_invalid")
        return [
            {key: item.get(key) for key in (
                "artifact_id", "kind", "revision", "sha256", "status",
                "producer_adapter", "producer_version", "media_type",
            ) if key in item}
            for item in result if isinstance(item, dict)
        ]

    def wait(self, task_id: str, *, timeout_seconds: float = 300.0, interval_seconds: float = 3.0) -> dict[str, Any]:
        timeout = max(1.0, min(float(timeout_seconds), 3600.0))
        interval = max(1.0, min(float(interval_seconds), 30.0))
        deadline = time.monotonic() + timeout
        while True:
            current = self.status(task_id)
            if str(current.get("status", "")) in _TERMINAL_STATES:
                return {"task": current, "artifacts": self.artifacts(task_id), "timed_out": False}
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {"task": current, "timed_out": True}
            time.sleep(min(interval, remaining))

    def release_gate(self, task_id: str) -> dict[str, Any]:
        result = self.request("GET", "api/v1/tasks/" + urllib.parse.quote(task_id, safe="") + "/release-gate")
        if not isinstance(result, dict):
            raise BridgeError("onebridge_release_gate_response_invalid")
        return result

    def approve(self, task_id: str, artifact_ids: list[str], decision: str, reason: str = "") -> dict[str, Any]:
        approver_key = self.env.get("ONEBRIDGE_APPROVER_API_KEY", "").strip()
        approver_id = self.env.get("ONEBRIDGE_APPROVER_ID", "").strip()
        if not approver_key or not approver_id:
            raise BridgeError("separate_operator_approver_credentials_required")
        if decision not in {"approve", "reject"} or not artifact_ids:
            raise BridgeError("approval_request_invalid")
        result = self.request(
            "POST",
            "api/v1/tasks/" + urllib.parse.quote(task_id, safe="") + "/approve",
            {"artifact_ids": artifact_ids, "decision": decision, "actor": approver_id, "reason": str(reason)[:4000]},
            api_key=approver_key,
        )
        if not isinstance(result, dict):
            raise BridgeError("onebridge_approval_response_invalid")
        return result

def _emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))

def _parse_outputs(value: str) -> list[str]:
    return [part.strip() for part in str(value).split(",") if part.strip()]

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Paperclip/Hermes bridge for OneBridge.")
    sub = parser.add_subparsers(dest="command", required=True)
    preflight = sub.add_parser("preflight", help="Check OneBridge authentication, queue, and real adapters.")
    preflight.add_argument("--outputs", default="")
    submit = sub.add_parser("submit", help="Submit an idempotent, review-gated OneBridge task.")
    goal = submit.add_mutually_exclusive_group(required=True)
    goal.add_argument("--goal")
    goal.add_argument("--goal-file")
    submit.add_argument("--outputs", required=True)
    submit.add_argument("--operation", default="paperclip.issue.execute")
    submit.add_argument("--approval", choices=("before_publish", "none"), default="before_publish")
    submit.add_argument("--dedupe-suffix", default="")
    status = sub.add_parser("status", help="Read a OneBridge task status.")
    status.add_argument("--task-id", required=True)
    artifacts = sub.add_parser("artifacts", help="List safe artifact metadata for a task.")
    artifacts.add_argument("--task-id", required=True)
    wait = sub.add_parser("wait", help="Wait for a task to reach a terminal state.")
    wait.add_argument("--task-id", required=True)
    wait.add_argument("--timeout-sec", type=float, default=300)
    wait.add_argument("--interval-sec", type=float, default=3)
    gate = sub.add_parser("release-gate", help="Read the release gate without releasing anything.")
    gate.add_argument("--task-id", required=True)
    approve = sub.add_parser("approve", help="Operator-only approval using a separate API key.")
    approve.add_argument("--task-id", required=True)
    approve.add_argument("--artifact-ids", nargs="+", required=True)
    approve.add_argument("--decision", choices=("approve", "reject"), required=True)
    approve.add_argument("--reason", default="")
    return parser

def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        client = OneBridgeClient()
        if args.command == "preflight":
            _emit(client.preflight(_parse_outputs(args.outputs)))
        elif args.command == "submit":
            goal = args.goal
            if args.goal_file:
                with open(args.goal_file, "r", encoding="utf-8") as handle:
                    goal = handle.read()
            _emit(client.submit(goal, _parse_outputs(args.outputs), operation=args.operation, approval=args.approval, dedupe_suffix=args.dedupe_suffix))
        elif args.command == "status":
            _emit(client.status(args.task_id))
        elif args.command == "artifacts":
            _emit(client.artifacts(args.task_id))
        elif args.command == "wait":
            _emit(client.wait(args.task_id, timeout_seconds=args.timeout_sec, interval_seconds=args.interval_sec))
        elif args.command == "release-gate":
            _emit(client.release_gate(args.task_id))
        elif args.command == "approve":
            _emit(client.approve(args.task_id, args.artifact_ids, args.decision, args.reason))
        return 0
    except (BridgeError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": _redact(str(exc))}, ensure_ascii=False), file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
