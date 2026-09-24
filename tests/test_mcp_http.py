import json

from onebridge.mcp_http import McpHttpClient, McpHttpResponse


def test_mcp_initializes_and_calls_tool_with_session():
    seen = []

    def transport(url, headers, body, timeout):
        payload = json.loads(body)
        seen.append((payload, dict(headers)))
        method = payload["method"]
        if method == "initialize":
            return McpHttpResponse(
                200,
                json.dumps({
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "serverInfo": {"name": "test", "version": "1"},
                    },
                }).encode(),
                "application/json",
                {"mcp-session-id": "session-1"},
            )
        if method == "notifications/initialized":
            return McpHttpResponse(
                202,
                b"",
                "application/json",
                {},
            )
        return McpHttpResponse(
            200,
            json.dumps({
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {
                    "content": [{"type": "text", "text": "ok"}],
                },
            }).encode(),
            "application/json",
            {},
        )

    client = McpHttpClient(
        url="http://127.0.0.1:9000/mcp",
        transport=transport,
    )
    result = client.call_tool("render_design", {"goal": "page"})

    assert result["content"][0]["text"] == "ok"
    assert [item[0]["method"] for item in seen] == [
        "initialize",
        "notifications/initialized",
        "tools/call",
    ]
    assert seen[-1][1]["MCP-Session-Id"] == "session-1"


def test_mcp_parses_sse_jsonrpc():
    def transport(url, headers, body, timeout):
        payload = json.loads(body)
        if payload["method"] == "initialize":
            body_value = (
                'event: message\n'
                'data: '
                + json.dumps({
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "serverInfo": {"name": "test", "version": "1"},
                    },
                })
                + '\n\n'
            ).encode()
            return McpHttpResponse(
                200,
                body_value,
                "text/event-stream",
                {},
            )
        if payload["method"] == "notifications/initialized":
            return McpHttpResponse(202, b"", "application/json", {})
        return McpHttpResponse(
            200,
            (
                'data: '
                + json.dumps({
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {"structuredContent": {"artifacts": []}},
                })
                + '\n'
            ).encode(),
            "text/event-stream",
            {},
        )

    client = McpHttpClient(
        url="http://localhost:9000/mcp",
        transport=transport,
    )
    result = client.call_tool("render_design", {})
    assert result["structuredContent"]["artifacts"] == []
