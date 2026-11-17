# Paperclip HTTP Adapter -> OneBridge (Hermes-free, stdlib-only)

**Status:** isolated integration branch, offline/mock-HTTP qualification only. No full
Paperclip installation, Hermes agent, model, database, or paid service is required
to run its contract tests. **Do not interpret dry-run / mock CI as live integration.**

## Purpose and boundaries

Paperclip's built-in HTTP adapter sends an execution webhook containing
\`agentId\`, \`runId\`, and \`context\`. OneBridge accepts a different body:
\`POST /api/v1/tasks\` requires a complete \`TaskContract\`. This tiny,
standalone Python HTTP listener maps one *assigned Paperclip issue* into one
review-gated OneBridge task. It does **not** modify either application's core.

- Reads the referenced Paperclip issue with a separately provisioned, minimal
  read-permission API key (\`GET /api/issues/<id>\`).
- Confirms Paperclip company and assigned agent, rather than trusting webhook
  fields as authoritative.
- Validates OneBridge's authenticated tenant, durable queue, real adapter
  capability, and healthy status before *live* dispatch.
- Uses a stable task ID across different Paperclip heartbeat run IDs and
  handles a task-ID collision by reading back the existing OneBridge task.
- Fixes \`policy.network=restricted\` and \`policy.approval=before_publish\`.
- Supports one configured output kind; \`code\` and \`test_report\` deliberately
  remain blocked because the Hermes worker sandbox/runtime is not qualified.
- Default is **dry_run**, which reads issue context and calculates an ID but
  never submits to OneBridge; live dispatch requires an explicit operator flag.
- Only binds to \`127.0.0.1\`, only connects to loopback HTTP service roots,
  disables HTTP redirects, bounds request/response sizes, and requires a
  separate >=32-character shared incoming Bearer token. Keep tokens out of
  prompts, examples in committed files, and agent-visible logs.

## Limitations: important

1. **Dispatch-only**: HTTP 202 means the task request was accepted/queued in
   OneBridge (or previewed in dry-run), **not** that the task finished. Paperclip
   HTTP adapter treats any 2xx as a completed webhook invocation. There is no
   Paperclip issue status / work-product callback yet. Adding that would require
   a separate authorized, audited implementation.
2. **No full autonomous loop**. This does not install Hermes or turn on browsing,
   sales, CRM, payment, or auto-publishing.
3. A Paperclip task must exist and be assigned to the invoking HTTP agent.
   Unassigned, closed, missing, wrong-company and non-task wakeups fail closed.
4. Live tasks require a **real, healthy** OneBridge output adapter, and a
   durable OneBridge worker/queue. Paperclip itself must be installed and
   running to send real webhooks; that is not required for mock tests.
5. User must separately configure Paperclip API permissions, OneBridge
   workspace/API key, output adapter and task queue; no credential creation is
   automated by this script. Paperclip HTTP adapter's callback URL must be
   allowlisted when targeting local/private addresses.

## Testing: Mac/Windows/Linux, without installing anything

From OneBridge repository root, with Python 3.11+:

    python3 -m unittest discover -s tests -p test_paperclip_http_bridge.py -v

This starts **three short-lived localhost HTTP servers** (fake Paperclip, fake
OneBridge, and the actual bridge listener). Tests cover auth, issue assignment
and company boundary, fail-closed adapter and tenant checks, secure defaults,
live mocked submission, replayed heartbeat deduplication, and dry-run behavior.
They do not contact the internet or spend model tokens.

In GitHub Actions this suite also runs in the normal Python 3.11/3.12 matrix.

## Operator-only live setup (future, after prerequisites)

Provide these protected environment variables through the operator's secrets
system. **Do not store actual values in source control or display credentials.**

| Variable | Meaning |
| --- | --- |
| \`PAPERCLIP_BASE_URL\` | Paperclip root, e.g. \`http://127.0.0.1:3100\` (no \`/api\`) |
| \`PAPERCLIP_READ_API_KEY\` | Separate authorized key able to read only assigned issue |
| \`PAPERCLIP_COMPANY_ID\` | Expected company ID |
| \`ONEBRIDGE_BASE_URL\` | OneBridge root, e.g. \`http://127.0.0.1:8080\` |
| \`ONEBRIDGE_API_KEY\` | Workspace-scoped OneBridge key |
| \`ONEBRIDGE_TENANT_ID\` | Matching OneBridge workspace |
| \`PAPERCLIP_BRIDGE_TOKEN\` | A unique random >=32-char token, different from both API keys |
| \`PAPERCLIP_BRIDGE_OUTPUT\` | Optional; \`content\` (Flowise), \`design\` (Open Design) |
| \`PAPERCLIP_BRIDGE_LIVE\` | Default absent/0 (dry run). **Only 1 enables submission** |

Run (with the environment supplied securely):

    python3 integrations/paperclip-onebridge/scripts/http_bridge.py --port 8765

Paperclip's **HTTP** adapter should point to:

    http://127.0.0.1:8765/paperclip/dispatch

Configure an incoming HTTP header \`Authorization: Bearer <bridge token>\`.
Paperclip HTTP's \`execute.ts\` overrides \`agentId\`, \`runId\` and \`context\`
from its actual runtime; do not fabricate them in a payload template.

**Critical: Paperclip's private-network guard** rejects localhost/private
outbound HTTP destinations unless the operator explicitly adds this exact
origin to Paperclip's *server environment* **before launching Paperclip**:

    PAPERCLIP_HTTP_ADAPTER_PRIVATE_ENDPOINT_ALLOWLIST=http://127.0.0.1:8765

Only allowlist this local listener; don't broadly disable SSRF protections.
The local listener returns a sanitized \`/health\` JSON response.

Do not configure \`PAPERCLIP_BRIDGE_LIVE=1\` before real service readiness
and a successful dry-run, and never expose the localhost listener externally.
No changes to OneBridge Main or to Paperclip upstream are needed.

## Relevant pinned upstream contract

- Paperclip: \`paperclipai/paperclip\` commit \`7685b7a4\`,
  \`server/src/adapters/http/execute.ts\` (webhook body), 
  \`server/src/adapters/http/remote-fetch.ts\` (private-network allowlist),
  \`server/src/routes/issues.ts\` (\`GET /issues/:id\`).
- OneBridge base: commit \`53b5b72\`, \`src/onebridge/contracts.py\`
  and \`src/onebridge/api.py\`.
