from onebridge.redaction import redact_text, redact_value


def test_redacts_nested_secret_values():
    payload = {
        "api_key": "secret-value",
        "nested": {"authorization": "Bearer abc123"},
        "message": "token=abcdef",
    }
    redacted = redact_value(payload)
    assert redacted["api_key"] == "<redacted>"
    assert redacted["nested"]["authorization"] == "<redacted>"
    assert "abcdef" not in redacted["message"]


def test_redacts_bearer_text():
    value = redact_text("Authorization: Bearer abcDEF123")
    assert "abcDEF123" not in value
    assert "<redacted>" in value
