from onebridge.adapters.base import AdapterOutput
from onebridge.adapters.validation import validate_adapter_outputs


def test_valid_json_output_passes():
    report = validate_adapter_outputs(
        [
            AdapterOutput(
                kind="content",
                media_type="application/json",
                content=b'{"ok": true}',
                filename="content.json",
            )
        ],
        expected_kind="content",
    )
    assert report.valid


def test_invalid_json_output_fails():
    report = validate_adapter_outputs(
        [
            AdapterOutput(
                kind="content",
                media_type="application/json",
                content=b"{not-json}",
                filename="content.json",
            )
        ],
        expected_kind="content",
    )
    assert not report.valid


def test_unsafe_filename_fails():
    report = validate_adapter_outputs(
        [
            AdapterOutput(
                kind="design",
                media_type="text/html",
                content=b"<html></html>",
                filename="../index.html",
            )
        ],
        expected_kind="design",
    )
    assert not report.valid
