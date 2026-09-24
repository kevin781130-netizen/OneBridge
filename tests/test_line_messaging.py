import base64
import hashlib
import hmac
import json

import pytest

from onebridge.line_messaging import (
    LineInboundMessage,
    LineMessagingError,
    LineWebhookController,
    parse_line_webhook,
    verify_line_signature,
)


SECRET = "line-secret"


def signed(body: bytes) -> str:
    digest = hmac.new(SECRET.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def webhook(text: str = "Build a page") -> bytes:
    return json.dumps(
        {
            "destination": "Ubot",
            "events": [
                {
                    "type": "message",
                    "replyToken": "reply-1",
                    "webhookEventId": "event-1",
                    "source": {
                        "type": "user",
                        "userId": "Uuser",
                    },
                    "message": {
                        "id": "message-1",
                        "type": "text",
                        "text": text,
                    },
                }
            ],
        },
        separators=(",", ":"),
    ).encode()


def test_line_signature_verifies_exact_raw_body():
    body = webhook()
    signature = signed(body)
    assert verify_line_signature(body, signature, SECRET)
    assert not verify_line_signature(body + b" ", signature, SECRET)


def test_line_webhook_verifies_before_parsing():
    body = webhook()
    values = parse_line_webhook(
        body,
        signature=signed(body),
        channel_secret=SECRET,
    )
    assert len(values) == 1
    assert values[0].text == "Build a page"
    assert values[0].source_id == "Uuser"

    with pytest.raises(LineMessagingError):
        parse_line_webhook(
            body,
            signature="invalid",
            channel_secret=SECRET,
        )


class FakeService:
    def __init__(self):
        self.contracts = []

    def submit(self, contract):
        self.contracts.append(contract)
        return {
            "task_id": contract.task_id,
            "status": "queued",
        }

    def status(self, task_id):
        if task_id != "task_1":
            raise KeyError(task_id)
        return {"task_id": task_id, "status": "running", "error": None}

    def artifacts(self, task_id):
        return []


class FakeScheduler:
    def __init__(self):
        self.task_ids = []

    def schedule(self, task_id):
        self.task_ids.append(task_id)

        class Dispatch:
            job_id = "job-line"
            status = "queued"

        return Dispatch()


class FakeClient:
    def __init__(self):
        self.replies = []

    def reply_text(self, token, text):
        self.replies.append((token, text))
        return {}


def test_line_controller_submits_text_as_onebridge_task():
    service = FakeService()
    client = FakeClient()
    controller = LineWebhookController(
        service=service,
        client=client,
        channel_secret=SECRET,
        tenant_id="tenant-line",
        required_outputs=("content", "design"),
    )
    body = webhook("Launch product")
    result = controller.handle(body, signature=signed(body))

    assert result[0]["action"] == "submitted"
    contract = service.contracts[0]
    assert contract.input.goal == "Launch product"
    assert contract.context.channel == "line"
    assert contract.context.conversation_id == "Uuser"
    assert contract.input.required_outputs == ["content", "design"]
    assert client.replies[0][0] == "reply-1"


def test_line_controller_status_command():
    service = FakeService()
    client = FakeClient()
    controller = LineWebhookController(
        service=service,
        client=client,
        channel_secret=SECRET,
        tenant_id="tenant-line",
        required_outputs=("content",),
    )
    body = webhook("/status task_1")
    result = controller.handle(body, signature=signed(body))
    assert result[0]["action"] == "status"
    assert "OneBridge 正在處理任務" in client.replies[0][1]


def test_line_controller_dispatches_submitted_task_when_scheduler_present():
    service = FakeService()
    client = FakeClient()
    scheduler = FakeScheduler()
    controller = LineWebhookController(
        service=service,
        client=client,
        channel_secret=SECRET,
        tenant_id="tenant-line",
        required_outputs=("content",),
        task_scheduler=scheduler,
    )
    body = webhook("Ship this")
    result = controller.handle(body, signature=signed(body))

    task_id = service.contracts[0].task_id
    assert scheduler.task_ids == [task_id]
    assert result[0]["status"] == "queued"
