#!/usr/bin/env python3
"""Operator-triggered OneBridge -> Paperclip status/artifact link sync.

Uses Python stdlib and existing http_bridge.py; never changes Paperclip issue
status, approvals, or OneBridge tasks. Preview-only unless explicitly enabled.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import urllib.parse
from typing import Any, Mapping

from http_bridge import BridgeError, Client, Config, nonempty

_STATUSES = {
    "received", "queued", "running", "waiting_approval",
    "blocked", "failed", "succeeded", "cancelled",
}
_TERMINAL_BAD = {"failed", "cancelled"}
_MAX_ARTIFACTS = 50
_KINDS = {"content", "design", "code", "test_report", "release", "other"}
_ARTIFACT_STATES = {
    "draft", "generated", "testing", "awaiting_review", "approved",
    "rejected", "released", "superseded",
}


def contract_for_issue(client: Client, issue_id: str, agent_id: str) -> dict[str, Any]:
    event = {
        "runId": "operator-result-sync", "agentId": agent_id,
        "companyId": client.config.paperclip_company,
        "context": {"taskId": issue_id},
    }
    # Re-derives the exact deterministic task ID from the current authoritative
    # Paperclip issue; no arbitrary task ID accepted from caller.
    return client.contract(event)


def read_result(client: Client, contract: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    c = client.config
    identity = client.json_request(c.onebridge_url, c.onebridge_key, "GET", "/api/v1/auth/me")
    if not isinstance(identity, dict) or identity.get("workspace_id") != c.tenant:
        raise BridgeError("onebridge_tenant_mismatch", 403)
    task_id = contract["task_id"]
    path = "/api/v1/tasks/" + urllib.parse.quote(task_id, safe="")
    task = client.json_request(c.onebridge_url, c.onebridge_key, "GET", path)
    if (
        not isinstance(task, dict) or task.get("task_id") != task_id
        or task.get("project_id") != contract["project_id"]
        or task.get("status") not in _STATUSES
    ):
        raise BridgeError("onebridge_task_identity_or_status_invalid", 502)
    manifests = client.json_request(c.onebridge_url, c.onebridge_key, "GET", path + "/artifacts")
    if not isinstance(manifests, list) or len(manifests) > _MAX_ARTIFACTS:
        raise BridgeError("onebridge_manifest_list_invalid", 502)
    compact: list[dict[str, Any]] = []
    for item in manifests:
        if not isinstance(item, dict):
            raise BridgeError("onebridge_manifest_invalid", 502)
        if item.get("task_id") != task_id or item.get("project_id") != contract["project_id"]:
            raise BridgeError("onebridge_manifest_scope_mismatch", 403)
        artifact_id = nonempty(item.get("artifact_id"), "artifact_id")
        kind, state = item.get("kind"), item.get("status")
        revision, digest = item.get("revision"), item.get("sha256")
        if (
            kind not in _KINDS or state not in _ARTIFACT_STATES
            or type(revision) is not int or revision < 1
            or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest)
        ):
            raise BridgeError("onebridge_manifest_metadata_invalid", 502)
        compact.append({
            "id": artifact_id, "kind": kind, "revision": revision,
            "status": state, "sha256": digest.lower(),
        })
    compact.sort(key=lambda a: (a["kind"], a["id"], a["revision"]))
    return task["status"], compact


def make_work_product(client: Client, contract: dict[str, Any], status: str,
                      manifests: list[dict[str, Any]]) -> dict[str, Any]:
    c = client.config
    task_id = contract["task_id"]
    review_url = c.onebridge_url + "/review/" + urllib.parse.quote(task_id, safe="")
    # An artifact count and digest of a bounded manifest list are safe to expose.
    digest = hashlib.sha256(json.dumps(manifests, sort_keys=True, separators=(",", ":"))
                            .encode("utf-8")).hexdigest()[:16]
    summary = (
        "OneBridge task status: " + status + " | artifacts: " +
        str(len(manifests)) + " | manifest digest: " + digest +
        " | Review and release are controlled ONLY in OneBridge; "
        "Paperclip task status is NOT automatically changed."
    )
    if status == "waiting_approval":
        summary += " | OneBridge approval is pending."
    return {
        "type": "preview_url",
        "provider": "onebridge",
        "externalId": task_id,
        "title": "OneBridge production result",
        "url": review_url,
        "status": "failed" if status in _TERMINAL_BAD else "active",
        "reviewState": "none",
        "summary": summary,
        "metadata": {
            "onebridgeTaskId": task_id,
            "onebridgeProjectId": contract["project_id"],
            "onebridgeStatus": status,
            "artifactCount": len(manifests),
            "manifestDigest": digest,
        },
    }


def sync(client: Client, issue_id: str, agent_id: str, *, apply: bool = False,
         write_key: str = "") -> dict[str, Any]:
    issue_id = nonempty(issue_id, "issue_id")
    agent_id = nonempty(agent_id, "agent_id")
    if apply and not write_key.strip():
        raise BridgeError("paperclip_write_key_required")
    contract = contract_for_issue(client, issue_id, agent_id)
    status, manifests = read_result(client, contract)
    product = make_work_product(client, contract, status, manifests)
    c = client.config
    result: dict[str, Any] = {
        "ok": True, "mode": "apply" if apply else "preview",
        "paperclip_issue_id": issue_id, "onebridge_task_id": contract["task_id"],
        "onebridge_status": status, "artifact_count": len(manifests),
        "review_url": product["url"], "updated": False,
    }
    if not apply:
        return result

    issue_path = "/api/issues/" + urllib.parse.quote(issue_id, safe="")
    products = client.json_request(c.paperclip_url, write_key, "GET", issue_path + "/work-products")
    if not isinstance(products, list):
        raise BridgeError("paperclip_work_products_invalid", 502)
    found = [
        p for p in products if isinstance(p, dict)
        and p.get("provider") == "onebridge"
        and p.get("externalId") == contract["task_id"]
    ]
    if len(found) > 1:
        raise BridgeError("paperclip_duplicate_onebridge_work_products", 409)
    if found:
        existing = found[0]
        if existing.get("type") != "preview_url" or existing.get("issueId") not in (None, issue_id):
            raise BridgeError("paperclip_work_product_collision", 409)
        if all(existing.get(k) == product[k] for k in (
            "type", "provider", "externalId", "title", "url", "status", "reviewState", "summary", "metadata"
        )):
            return {**result, "operation": "unchanged"}
        wid = nonempty(existing.get("id"), "work_product_id")
        update = {k: product[k] for k in ("title", "url", "status", "summary", "metadata")}
        saved = client.json_request(c.paperclip_url, write_key, "PATCH",
                                    "/api/work-products/" + urllib.parse.quote(wid, safe=""), update)
        operation = "updated"
    else:
        saved = client.json_request(c.paperclip_url, write_key, "POST",
                                    issue_path + "/work-products", product)
        operation = "created"
    if not isinstance(saved, dict) or saved.get("provider") != "onebridge" or (
        saved.get("externalId") != contract["task_id"]
    ):
        raise BridgeError("paperclip_work_product_receipt_invalid", 502)
    return {**result, "updated": True, "operation": operation}


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync a OneBridge result card to Paperclip")
    parser.add_argument("--issue-id", required=True, help="Existing Paperclip issue ID")
    parser.add_argument("--agent-id", required=True, help="Assigned Paperclip agent ID")
    parser.add_argument("--apply", action="store_true", help="Explicitly write a Paperclip work-product card")
    args = parser.parse_args()
    try:
        config = Config.from_env()
        if args.apply and os.getenv("PAPERCLIP_RESULT_SYNC_LIVE") != "1":
            raise BridgeError("result_sync_live_flag_required")
        key = os.getenv("PAPERCLIP_WRITE_API_KEY", "") if args.apply else ""
        result = sync(Client(config), args.issue_id, args.agent_id,
                      apply=args.apply, write_key=key)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except BridgeError as exc:
        print(json.dumps({"ok": False, "error": exc.code}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
