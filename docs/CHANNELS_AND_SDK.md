# Channels, ContextForge and SDK

## Native OpenClaw plugin

The repository ships a native tool-plugin package at:

```text
integrations/openclaw-onebridge/
```

It uses OpenClaw's tool-plugin SDK and declares the stable tool contract:

```text
onebridge_submit
onebridge_status
onebridge_artifacts
onebridge_approve
onebridge_retry
onebridge_cancel
```

The plugin makes HTTP calls to the OneBridge control plane only. It owns no
workflow state and does not access the OneBridge database or worker processes.

Build locally:

```bash
cd integrations/openclaw-onebridge
npm install
npm run build
```

The repository CI compiles this package with Node 24 and a pinned OpenClaw
compile dependency while retaining a peer compatibility floor for distribution.

## LINE Messaging API

Configure all three values together:

```bash
export ONEBRIDGE_LINE_CHANNEL_SECRET=<channel-secret>
export ONEBRIDGE_LINE_CHANNEL_ACCESS_TOKEN=<channel-access-token>
export ONEBRIDGE_LINE_TENANT_ID=<onebridge-tenant-id>
```

Optional required outputs:

```bash
export ONEBRIDGE_LINE_REQUIRED_OUTPUTS=content,design,code,test_report
```

Webhook endpoint:

```text
POST /integrations/line/webhook
```

The endpoint verifies the `X-Line-Signature` against the exact raw request body
before parsing JSON. Text messages are converted to durable OneBridge tasks.
`/status <task_id>` returns a channel-safe progress message.

OneBridge also exposes:

```text
POST /api/v1/tasks/{task_id}/line/push-progress
```

That endpoint uses the task's stored LINE conversation binding; callers cannot
supply an arbitrary recipient id. Provider error dumps and secrets are not
forwarded to the channel.

## Review Portal previews

The Review Portal at `/review/<task_id>` can fetch bounded binary previews for:

- PNG
- JPEG
- WebP
- GIF
- PDF

SVG is intentionally excluded because active SVG content can carry script-like
behavior. Preview responses are bounded, no-store, `nosniff`, and sandboxed.
Bearer credentials typed into the portal stay in page memory and are attached
to preview fetches rather than embedded in preview URLs.

## Adapter SDK

Third-party Python adapters are discovered through the entry-point group:

```text
onebridge.adapters
```

Discovery alone does not import plugin code. Loading occurs only for explicit
operator configuration:

```bash
export ONEBRIDGE_ADAPTER_PLUGINS=my_adapter,another_adapter
```

A plugin entry point may expose an adapter instance or a factory accepting
`AdapterPluginContext`. Loaded adapters must implement the OneBridge adapter
contract: name, version, health, capabilities, execute, and cancel.

Custom routing can map an existing artifact kind to an explicitly loaded adapter:

```bash
export ONEBRIDGE_OUTPUT_ROUTES_JSON='{"design":"my_design_adapter"}'
```

Duplicate adapter names fail closed.

## ContextForge

ContextForge is a bounded, explicit-scope context assembler generalized from
Vera's context-selection patterns. A task opts in using
`policy.knowledge_scopes`. ContextForge:

- selects only explicitly requested scopes,
- loads only registered providers,
- de-duplicates identical content by SHA-256,
- sorts by provider priority,
- enforces item and byte budgets,
- reports missing scopes and skipped items instead of silently widening context.

Context-provider packages use the entry-point group:

```text
onebridge.context_providers
```

and are loaded only when explicitly configured:

```bash
export ONEBRIDGE_CONTEXT_PLUGINS=my_knowledge_provider
```

A context plugin factory returns a mapping of scope names to providers. The
resulting bounded context bundle is added to adapter input as
`context_bundle` whenever a task requests knowledge scopes.

## Production path

A production deployment can combine explicit plugins with the qualification gate:

```bash
export ONEBRIDGE_REQUIRE_QUALIFIED_ADAPTERS=true
```

Real adapters must then be registered, qualified, promoted to active, and backed
by stored passing qualification evidence before the control plane starts.
