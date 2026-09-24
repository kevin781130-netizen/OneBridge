import json

from onebridge.deployment_actuator import (
    DeploymentActuationRequest,
    HttpDeploymentActuator,
)


class FakeResponse:
    status = 200
    code = 200
    headers = {"Content-Type": "application/json"}

    def read(self, limit):
        return json.dumps({
            "ok": True,
            "active_slot": "green",
        }).encode()

    def close(self):
        return None


class FakeOpener:
    def __init__(self):
        self.request = None

    def open(self, request, timeout):
        self.request = request
        return FakeResponse()


def test_http_actuator_signs_request_and_requires_matching_slot(monkeypatch):
    opener = FakeOpener()
    monkeypatch.setattr(
        "onebridge.deployment_actuator.urllib.request.build_opener",
        lambda *args: opener,
    )
    actuator = HttpDeploymentActuator(
        url="http://127.0.0.1:9009/switch",
        shared_secret="-".join(["unit", "test", "deployment", "secret"]),
    )
    result = actuator.apply(
        DeploymentActuationRequest(
            request_id="depact_1",
            service="openclaw",
            action="promote",
            from_slot="blue",
            to_slot="green",
            version="2026.9.6",
            target_endpoint="https://green.example.com/health",
        )
    )

    assert result.applied is True
    assert result.reported_active_slot == "green"
    headers = dict(opener.request.header_items())
    assert headers["X-onebridge-idempotency-key"] == "depact_1"
    assert headers["X-onebridge-signature"].startswith("sha256=")
