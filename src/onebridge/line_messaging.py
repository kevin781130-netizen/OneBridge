from __future__ import annotations

import base64
import hashlib
import hmac
import json
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .contracts import TaskContract
from .line_progress import ChannelProgress, map_task_progress


LINE_API_BASE = "https://api.line.me"
MAX_LINE_RESPONSE_BYTES = 1 * 1024 * 1024
MAX_LINE_WEBHOOK_BYTES = 2 * 1024 * 1024
MAX_LINE_TEXT_CHARS = 5000


@dataclass(frozen=True, slots=True)
class LineHttpResult:
    status: int
    body: bytes
    content_type: str


LineTransport = Callable[
    [str, str, Mapping[str, str], bytes, float],
    LineHttpResult,
]


@dataclass(frozen=True, slots=True)
class LineInboundMessage:
    event_type: str
    message_type: str
    text: str
    reply_token: str
    source_type: str
    source_id: str
    user_id: str
    webhook_event_id: str


class LineMessagingError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def verify_line_signature(
    raw_body: bytes,
    signature: str | None,
    channel_secret: str,
) -> bool:
    if not signature or not channel_secret:
        return False
    digest = hmac.new(
        channel_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).digest()
    expected = base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(expected, signature.strip())


def parse_line_webhook(
    raw_body: bytes,
    *,
    signature: str | None,
    channel_secret: str,
) -> list[LineInboundMessage]:
    if len(raw_body) > MAX_LINE_WEBHOOK_BYTES:
        raise LineMessagingError("line_webhook_too_large")
    if not verify_line_signature(raw_body, signature, channel_secret):
        raise LineMessagingError("line_webhook_signature_invalid")

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LineMessagingError("line_webhook_invalid_json") from exc
    if not isinstance(payload, dict):
        raise LineMessagingError("line_webhook_invalid_json")

    result: list[LineInboundMessage] = []
    events = payload.get("events")
    if not isinstance(events, list):
        return result

    for event in events[:100]:
        if not isinstance(event, dict) or event.get("type") != "message":
            continue
        message = event.get("message")
        source = event.get("source")
        if not isinstance(message, dict) or not isinstance(source, dict):
            continue
        if message.get("type") != "text":
            continue

        source_type = str(source.get("type") or "")
        source_id = str(
            source.get("groupId")
            or source.get("roomId")
            or source.get("userId")
            or ""
        )
        user_id = str(source.get("userId") or source_id)
        reply_token = str(event.get("replyToken") or "")
        text = str(message.get("text") or "")
        event_id = str(event.get("webhookEventId") or "")
        if not source_id or not reply_token or not text:
            continue

        result.append(
            LineInboundMessage(
                event_type="message",
                message_type="text",
                text=text[:20_000],
                reply_token=reply_token[:500],
                source_type=source_type[:40],
                source_id=source_id[:300],
                user_id=user_id[:300],
                webhook_event_id=event_id[:300],
            )
        )
    return result


def _default_transport(
    url: str,
    method: str,
    headers: Mapping[str, str],
    body: bytes,
    timeout: float,
) -> LineHttpResult:
    if not url.startswith(LINE_API_BASE + "/"):
        raise LineMessagingError("line_api_origin_blocked")
    request = urllib.request.Request(
        url,
        data=body,
        headers=dict(headers),
        method=method,
    )
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirect(),
    )
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        response = exc
    try:
        raw = response.read(MAX_LINE_RESPONSE_BYTES + 1)
        if len(raw) > MAX_LINE_RESPONSE_BYTES:
            raise LineMessagingError("line_response_too_large")
        return LineHttpResult(
            status=int(getattr(response, "status", getattr(response, "code", 0))),
            body=raw,
            content_type=str(
                response.headers.get("Content-Type") or "application/json"
            )[:200],
        )
    finally:
        response.close()


class LineMessagingClient:
    def __init__(
        self,
        *,
        channel_access_token: str,
        timeout_seconds: float = 10.0,
        transport: LineTransport | None = None,
    ) -> None:
        token = str(channel_access_token or "").strip()
        if not token:
            raise ValueError("line_channel_access_token_required")
        self.channel_access_token = token
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 30.0))
        self.transport = transport or _default_transport

    def _post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        retry_key: str | None = None,
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.channel_access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "OneBridge-LINE/1",
        }
        if retry_key:
            headers["X-Line-Retry-Key"] = retry_key
        body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        response = self.transport(
            LINE_API_BASE + path,
            "POST",
            headers,
            body,
            self.timeout_seconds,
        )
        if not 200 <= response.status < 300:
            raise LineMessagingError(
                f"line_http_error:{response.status}"
            )
        if not response.body:
            return {}
        try:
            value = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LineMessagingError("line_invalid_json_response") from exc
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _text_message(text: str) -> dict[str, str]:
        value = str(text or "").strip()
        if not value:
            raise ValueError("line_text_required")
        return {
            "type": "text",
            "text": value[:MAX_LINE_TEXT_CHARS],
        }

    def reply_text(self, reply_token: str, text: str) -> dict[str, Any]:
        return self._post(
            "/v2/bot/message/reply",
            {
                "replyToken": str(reply_token),
                "messages": [self._text_message(text)],
            },
        )

    def push_text(
        self,
        target: str,
        text: str,
        *,
        retry_key: str | None = None,
    ) -> dict[str, Any]:
        key = retry_key or str(uuid.uuid4())
        return self._post(
            "/v2/bot/message/push",
            {
                "to": str(target),
                "messages": [self._text_message(text)],
            },
            retry_key=key,
        )

    def push_progress(
        self,
        target: str,
        progress: ChannelProgress,
    ) -> dict[str, Any]:
        message = progress.message
        if progress.task_id:
            message += f"\nTask: {progress.task_id}"
        if progress.artifact_count is not None:
            message += f"\nArtifacts: {progress.artifact_count}"
        return self.push_text(target, message)


class LineWebhookController:
    """LINE ingress that submits durable OneBridge tasks or answers /status."""

    def __init__(
        self,
        *,
        service,
        client: LineMessagingClient,
        channel_secret: str,
        tenant_id: str,
        required_outputs: tuple[str, ...],
    ) -> None:
        if not channel_secret:
            raise ValueError("line_channel_secret_required")
        if not tenant_id:
            raise ValueError("line_tenant_id_required")
        self.service = service
        self.client = client
        self.channel_secret = channel_secret
        self.tenant_id = tenant_id
        self.required_outputs = tuple(required_outputs)

    def handle(
        self,
        raw_body: bytes,
        *,
        signature: str | None,
    ) -> list[dict[str, Any]]:
        events = parse_line_webhook(
            raw_body,
            signature=signature,
            channel_secret=self.channel_secret,
        )
        results: list[dict[str, Any]] = []
        for event in events:
            text = event.text.strip()
            if text.lower().startswith("/status "):
                task_id = text.split(None, 1)[1].strip()
                try:
                    status = self.service.status(task_id)
                    artifact_count = len(self.service.artifacts(task_id))
                    progress = map_task_progress(
                        status["status"],
                        task_id=task_id,
                        artifact_count=artifact_count,
                        error=status.get("error"),
                    )
                    self.client.reply_text(
                        event.reply_token,
                        progress.message + f"\nTask: {task_id}",
                    )
                    results.append({
                        "action": "status",
                        "task_id": task_id,
                        "status": status["status"],
                    })
                except KeyError:
                    self.client.reply_text(
                        event.reply_token,
                        "找不到這個 OneBridge Task。",
                    )
                    results.append({
                        "action": "status_missing",
                        "task_id": task_id,
                    })
                continue

            contract = TaskContract.model_validate({
                "operation": "project.create",
                "input": {
                    "goal": text,
                    "required_outputs": list(self.required_outputs),
                },
                "context": {
                    "tenant_id": self.tenant_id,
                    "user_id": event.user_id,
                    "channel": "line",
                    "conversation_id": event.source_id,
                },
                "policy": {
                    "approval": "before_publish",
                    "network": "restricted",
                },
                "metadata": {
                    "line_webhook_event_id": event.webhook_event_id,
                    "line_source_type": event.source_type,
                },
            })
            status = self.service.submit(contract)
            self.client.reply_text(
                event.reply_token,
                "任務已接收，等待 OneBridge 處理。"
                f"\nTask: {contract.task_id}",
            )
            results.append({
                "action": "submitted",
                "task_id": contract.task_id,
                "status": status["status"],
            })
        return results
