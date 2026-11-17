import sys
from pathlib import Path

import pytest

from onebridge.mcp_stdio import McpStdioClient, McpStdioError


def fake_server(tmp_path: Path) -> Path:
    script = tmp_path / "fake_od_mcp.py"
    script.write_text(
        """
import json
import os
import sys

for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "notifications/initialized":
        continue
    if method == "initialize":
        result = {"protocolVersion": "2025-06-18", "capabilities": {},
                  "serverInfo": {"name": "fake-od", "version": "1"}}
    else:
        print(json.dumps({"jsonrpc": "2.0", "method": "notifications/progress",
                          "params": {}}), flush=True)
        result = {"content": [{"type": "text", "text": json.dumps({
            "name": request["params"]["name"],
            "clean_env": "OPENAI_API_KEY" not in os.environ,
        })}]}
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
""",
        encoding="utf-8",
    )
    return script


def test_real_subprocess_mock_mcp_handshake_and_tool(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "must-never-cross-boundary")
    command = (sys.executable, "-u", str(fake_server(tmp_path)), "mcp")
    with McpStdioClient(command=command, timeout_seconds=5) as client:
        result = client.call_tool("list_projects", {})
        assert '"clean_env": true' in result["content"][0]["text"]
        assert '"name": "list_projects"' in result["content"][0]["text"]
        next_result = client.call_tool("get_run", {"runId": "test"})
        assert '"name": "get_run"' in next_result["content"][0]["text"]


def test_stdio_requires_mcp_subcommand_and_rejects_shell_launchers():
    with pytest.raises(ValueError, match="requires_mcp_subcommand"):
        McpStdioClient(command=("node", "od.mjs"))
    with pytest.raises(ValueError, match="shell_launcher_forbidden"):
        McpStdioClient(command=("od.cmd", "mcp"))


def test_stdio_missing_binary_fails_closed():
    with McpStdioClient(command=("onebridge-does-not-exist-xyz", "mcp")) as client:
        with pytest.raises(McpStdioError, match="launch_failed"):
            client.call_tool("list_projects", {})
