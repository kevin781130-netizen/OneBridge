import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "integrations" / "openclaw-onebridge"


def test_openclaw_plugin_manifest_matches_tool_source():
    manifest = json.loads(
        (PLUGIN / "openclaw.plugin.json").read_text(encoding="utf-8")
    )
    package = json.loads(
        (PLUGIN / "package.json").read_text(encoding="utf-8")
    )
    source = (PLUGIN / "src" / "index.ts").read_text(encoding="utf-8")

    expected = [
        "onebridge_submit",
        "onebridge_status",
        "onebridge_artifacts",
        "onebridge_approve",
        "onebridge_retry",
        "onebridge_cancel",
    ]
    assert manifest["contracts"]["tools"] == expected
    assert package["openclaw"]["extensions"] == ["./dist/index.js"]
    assert package["peerDependencies"]["openclaw"] == ">=2026.5.17"
    for name in expected:
        assert f'name: "{name}"' in source
