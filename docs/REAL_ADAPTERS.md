# Real adapter integration contracts

OneBridge keeps upstream payloads at the adapter boundary. The core continues to
use Task Contract v1 and Artifact Manifest records regardless of which upstream
product is configured.

## Open Design over MCP

Configure:

```bash
export ONEBRIDGE_OPEN_DESIGN_MCP_URL=http://127.0.0.1:9000/mcp
export ONEBRIDGE_OPEN_DESIGN_TOOL=render_design
```

Loopback HTTP is accepted only on an explicit port and the `/mcp` path. Remote
endpoints must use HTTPS and are placed behind a host/path/method egress rule.

OneBridge performs the MCP initialize -> initialized -> tools/call sequence. The
configured tool receives:

```json
{
  "task_id": "task_...",
  "project_id": "prj_...",
  "goal": "Create a launch page",
  "operation": "project.create",
  "inputs": {},
  "artifacts": [
    {
      "artifact_id": "art_...",
      "kind": "content",
      "media_type": "application/json",
      "sha256": "...",
      "uri": "...",
      "text": "{...}"
    }
  ]
}
```

Text upstream artifacts are materialized through OneBridge's object store and
inlined only when they are within the bounded context limit.

The preferred MCP result is:

```json
{
  "structuredContent": {
    "artifacts": [
      {
        "kind": "design",
        "media_type": "text/html",
        "filename": "index.html",
        "text": "<!doctype html>..."
      }
    ]
  }
}
```

Binary artifacts may use `content_base64`. A plain MCP text result is accepted
as a single text/HTML design fallback. Every output passes OneBridge filename,
media-type, size, and required-kind validation before artifact persistence.

## Hermes isolated command bridge

Configure an executable and an argv template. OneBridge never invokes a shell.

```bash
export ONEBRIDGE_HERMES_EXECUTABLE=hermes
export ONEBRIDGE_HERMES_ARGS_JSON='["--request","{request}","--output","{output}"]'
export ONEBRIDGE_HERMES_TIMEOUT_SECONDS=300
export ONEBRIDGE_HERMES_REQUIRE_STRONG_SANDBOX=true
```

Supported placeholders are:

- `{request}`: JSON request path.
- `{output}`: output directory.
- `{workspace}`: disposable per-task workspace.

With strong sandbox enabled, Linux uses the existing bubblewrap boundary and
macOS uses the qualified seatbelt boundary. If a strong sandbox is unavailable,
execution fails closed.

The generated request contains task/project identifiers, goal, operation,
metadata, materialized upstream artifacts, and the required output kinds. The
Hermes process must write `result.json` inside the output directory:

```json
{
  "outputs": [
    {
      "kind": "code",
      "media_type": "text/x-python",
      "filename": "app.py"
    },
    {
      "kind": "test_report",
      "media_type": "application/json",
      "filename": "test_report.json"
    }
  ]
}
```

The referenced files must also exist below the output directory. Paths cannot
escape the workspace, symlinks are rejected, process time/output/workspace
growth is bounded, and the command environment excludes provider credentials.

One Hermes invocation may return both `code` and `test_report`; OneBridge
persists both from that single execution instead of running Hermes twice.

## OpenTelemetry

OpenTelemetry is optional. Install:

```bash
pip install -e '.[telemetry]'
```

Configure an OTLP/HTTP base endpoint:

```bash
export ONEBRIDGE_OTEL_ENDPOINT=https://otel-collector.example.com
export ONEBRIDGE_OTEL_SERVICE_NAME=onebridge
# optional comma-separated key=value pairs
export ONEBRIDGE_OTEL_HEADERS='Authorization=Bearer <collector-token>'
```

OneBridge emits task lifecycle counters, adapter call/failure counters, adapter
duration histograms, task-run spans, release spans, and adapter execution spans.
Telemetry attributes intentionally use control-plane identifiers, adapter names,
versions, status and error type rather than user prompts or artifact contents.


## Live qualification

After configuring a real adapter, run the explicit live qualification fixture:

```bash
onebridge qualify --adapter flowise
onebridge qualify --adapter open_design
onebridge qualify --adapter hermes
```

The command refuses to qualify a mock adapter. It checks adapter health, performs
one bounded fixture execution, validates every declared output kind, and prints a
JSON qualification record containing adapter/version, health latency, observed
output kinds, duration and validation errors. This is intentionally operator
initiated because real qualification may call an upstream service or run Hermes.
