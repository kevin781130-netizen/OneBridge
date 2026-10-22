import pytest

from onebridge.config import Settings
from onebridge.feedback_desk import build_feedback_desk_adapter


def configured_settings(**overrides):
    values = {
        "feedback_desk_mcp_url": None,
        "feedback_desk_project_slug": None,
        "feedback_desk_token": None,
        "feedback_desk_timeout_seconds": 30,
        "feedback_desk_required_outputs": ("code", "test_report"),
    }
    values.update(overrides)
    return Settings(**values)


def test_feedback_desk_integration_is_optional_when_unconfigured():
    assert build_feedback_desk_adapter(configured_settings()) is None


def test_feedback_desk_requires_all_connection_settings():
    settings = configured_settings(
        feedback_desk_mcp_url="https://feedback.example.com/mcp",
        feedback_desk_project_slug="demo",
    )
    with pytest.raises(ValueError, match="requires"):
        build_feedback_desk_adapter(settings)


def test_feedback_desk_remote_http_is_rejected():
    settings = configured_settings(
        feedback_desk_mcp_url="http://192.0.2.10/mcp",
        feedback_desk_project_slug="demo",
        feedback_desk_token="test-token",
    )
    with pytest.raises(ValueError, match="mcp_insecure_http_blocked"):
        build_feedback_desk_adapter(settings)


def test_feedback_desk_https_uses_bearer_token_and_explicit_egress_policy():
    settings = configured_settings(
        feedback_desk_mcp_url="https://feedback.example.com/mcp",
        feedback_desk_project_slug="demo",
        feedback_desk_token="test-token",
    )
    adapter = build_feedback_desk_adapter(settings)

    assert adapter is not None
    assert adapter.project_slug == "demo"
    assert adapter.client.authorization == "Bearer test-token"
    assert adapter.client.egress_policy is not None
    assert adapter.client.url == "https://feedback.example.com/mcp"


@pytest.mark.parametrize(
    "url",
    [
        "https://feedback.example.com/",
        "https://feedback.example.com/mcp?token=secret",
        "https://user:pass@feedback.example.com/mcp",
    ],
)
def test_feedback_desk_rejects_unsafe_endpoint_shapes(url):
    settings = configured_settings(
        feedback_desk_mcp_url=url,
        feedback_desk_project_slug="demo",
        feedback_desk_token="test-token",
    )
    with pytest.raises(ValueError, match="absolute /mcp URL"):
        build_feedback_desk_adapter(settings)


def test_feedback_desk_rejects_unknown_required_output():
    settings = configured_settings(
        feedback_desk_mcp_url="https://feedback.example.com/mcp",
        feedback_desk_project_slug="demo",
        feedback_desk_token="test-token",
        feedback_desk_required_outputs=("shell_command",),
    )
    with pytest.raises(ValueError, match="output kinds"):
        build_feedback_desk_adapter(settings)
