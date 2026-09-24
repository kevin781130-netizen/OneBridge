import json
from pathlib import Path

from onebridge.extensions import load_approved_extensions


def test_loads_approved_loopback_design_extension(tmp_path: Path):
    (tmp_path / "extensions.json").write_text(
        json.dumps({
            "schema_version": 1,
            "servers": {
                "open_design": {
                    "type": "remote",
                    "url": "http://127.0.0.1:8787/mcp",
                    "enabled": True,
                    "approved": True,
                    "category": "design",
                    "timeout": 5000,
                }
            },
        }),
        encoding="utf-8",
    )
    snapshot = load_approved_extensions(tmp_path)
    assert snapshot.available
    assert snapshot.servers["open_design"]["category"] == "design"


def test_blocks_secret_fields_in_extension_config(tmp_path: Path):
    (tmp_path / "extensions.json").write_text(
        json.dumps({
            "schema_version": 1,
            "servers": {
                "open_design": {
                    "type": "remote",
                    "url": "https://design.example.com/mcp",
                    "enabled": True,
                    "approved": True,
                    "category": "design",
                    "headers": {"Authorization": "secret"},
                }
            },
        }),
        encoding="utf-8",
    )
    snapshot = load_approved_extensions(tmp_path)
    assert not snapshot.available
    assert snapshot.reason == "extension_secrets_or_unknown_fields_blocked"
