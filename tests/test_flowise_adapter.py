import json

import pytest

from onebridge.adapters.base import AdapterRequest
from onebridge.adapters.flowise import (
    FlowiseAdapterError,
    FlowisePredictionAdapter,
    HttpResult,
)


def request(**inputs) -> AdapterRequest:
    return AdapterRequest(
        task_id="task_1",
        project_id="prj_1",
        goal="Create a launch page",
        operation="project.create",
        inputs=inputs,
    )


def test_flowise_prediction_adapter_uses_expected_endpoint_and_auth():
    observed = {}

    def transport(url, headers, body, timeout):
        observed["url"] = url
        observed["headers"] = dict(headers)
        observed["payload"] = json.loads(body)
        observed["timeout"] = timeout
        return HttpResult(
            status=200,
            body=b'{"chatMessageId":"msg_1","text":"ok"}',
            content_type="application/json",
        )

    adapter = FlowisePredictionAdapter(
        base_url="http://127.0.0.1:3000",
        chatflow_id="flow_123",
        api_key="secret-key",
        transport=transport,
    )
    result = adapter.execute(request())

    assert observed["url"].endswith("/api/v1/prediction/flow_123")
    assert observed["headers"]["Authorization"] == "Bearer secret-key"
    assert observed["payload"]["question"] == "Create a launch page"
    assert observed["payload"]["streaming"] is False
    assert result.external_task_id == "msg_1"
    assert result.outputs[0].kind == "content"


def test_flowise_override_is_allowlisted():
    def transport(url, headers, body, timeout):
        payload = json.loads(body)
        assert payload["overrideConfig"] == {"temperature": 0.2}
        return HttpResult(200, b'{"text":"ok"}', "application/json")

    adapter = FlowisePredictionAdapter(
        base_url="http://localhost:3000",
        chatflow_id="flow_1",
        allowed_override_keys=("temperature",),
        transport=transport,
    )
    adapter.execute(request(flowise_override={"temperature": 0.2}))

    with pytest.raises(FlowiseAdapterError):
        adapter.execute(
            request(flowise_override={"temperature": 0.2, "chatId": "blocked"})
        )


def test_remote_flowise_requires_explicit_egress_policy():
    adapter = FlowisePredictionAdapter(
        base_url="https://flowise.example.com",
        chatflow_id="flow_1",
        transport=lambda *args: HttpResult(200, b"{}", "application/json"),
    )
    with pytest.raises(ValueError):
        adapter.execute(request())
