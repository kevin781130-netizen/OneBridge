# Durable execution and blue-green deployment

## Durable task dispatch

OneBridge can run tasks asynchronously through the same durable queue abstraction
used by the worker layer.

Enable it with:

```bash
export ONEBRIDGE_TASK_QUEUE_URL=.onebridge/task-queue.sqlite
```

For multi-process or multi-host deployments use PostgreSQL:

```bash
export ONEBRIDGE_TASK_QUEUE_URL=postgresql://onebridge:password@postgres/onebridge
```

When the queue is configured, `POST /api/v1/tasks` persists the Task Contract
first and then idempotently schedules one `onebridge_task` execution job. The
job id is deterministic from the task id, so repeated dispatch does not create
duplicate work.

Run a worker with:

```bash
onebridge worker
```

Useful worker settings:

```bash
export ONEBRIDGE_WORKER_POLL_SECONDS=2
export ONEBRIDGE_WORKER_STALE_AFTER_SECONDS=3600
export ONEBRIDGE_WORKER_PRESERVE_FAILED_WORKSPACE=false
```

A worker claims one durable job, runs the existing OneBridge service pipeline,
persists artifacts/approval state through the control-plane database, and then
marks the queue job succeeded or failed. Failed/blocked tasks use the existing
task retry operation, which explicitly requeues the deterministic execution job.

The API exposes queue state without making the queue authoritative for workflow
state:

```text
GET /api/v1/tasks/{task_id}/execution
```

Task state remains authoritative in OneBridge's task table. The worker queue is
execution transport only.

### Crash recovery

SQLite and PostgreSQL queue backends record worker identity and claim timestamps.
Workers recover claims that have remained `running` beyond
`ONEBRIDGE_WORKER_STALE_AFTER_SECONDS`. The default is one hour, deliberately
well above the current bounded adapter timeouts so an ordinary long adapter call
is not immediately treated as abandoned.

## Docker Compose

`deployment/docker-compose.yml` now runs:

```text
PostgreSQL
   |
   +---- OneBridge API
   |
   +---- OneBridge Worker

API + Worker ---- shared OneBridge state volume
```

The API and worker use PostgreSQL for control-plane/task-queue coordination. A
shared state volume provides local object/checkpoint/audit storage for the
single-host compose profile. MinIO remains available for S3-compatible storage
qualification.

Start with:

```bash
docker compose -f deployment/docker-compose.yml up --build
```

The API is exposed at port 8000. Submitted tasks no longer require a synchronous
`/run` call in this profile; the worker consumes them automatically.

## OpenClaw blue-green registry

OneBridge persists two OpenClaw deployment slots: `blue` and `green`.

Register a candidate:

```text
POST /api/v1/deployments/openclaw/blue
{
  "endpoint": "https://openclaw-blue.example.com/health",
  "version": "2026.9.6"
}
```

Probe it:

```text
POST /api/v1/deployments/openclaw/blue/probe
```

Promote only after healthy evidence:

```text
POST /api/v1/deployments/openclaw/blue/promote
```

After the opposite slot has also been qualified and promoted, rollback is:

```text
POST /api/v1/deployments/openclaw/actions/rollback
```

Promotion is atomic in the OneBridge database: the previous active deployment
becomes `standby` and the selected healthy slot becomes `active`. Rollback
requires a healthy standby.

Health probes are intentionally narrow. Plain HTTP is accepted only for
explicit loopback endpoints; remote endpoints require HTTPS and use the existing
default-deny egress policy. Probe evidence stores status, latency, content type,
body SHA-256 and an optional JSON `ok` flag rather than storing the full
response body.

### Signed traffic actuator

OneBridge can now call a real deployment/traffic controller before it commits the
slot transition. Configure both values together:

```bash
export ONEBRIDGE_OPENCLAW_ACTUATOR_URL=https://deploy.example.com/onebridge/switch
export ONEBRIDGE_OPENCLAW_ACTUATOR_SECRET=<shared-secret>
```

The actuator receives a bounded JSON request containing an idempotency key,
service, action, previous slot, target slot, version and target endpoint.
OneBridge signs the exact request body with HMAC-SHA256 and sends:

```text
X-OneBridge-Timestamp
X-OneBridge-Signature: sha256=<hex>
X-OneBridge-Idempotency-Key
```

The actuator must return HTTP 2xx. It may also return
`{"ok":true,"active_slot":"green"}`; if `active_slot` is present it must match
the requested target slot.

OneBridge records each external switch in `deployment_actions` before the call.
A successful external actuation becomes `committed` only after the registry
transition commits. If external actuation succeeds but the local commit fails,
the journal is marked `reconcile_required` rather than pretending the two
systems are consistent.

Actuation history is available at:

```text
GET /api/v1/deployments/openclaw/history
```

Remote actuator URLs require HTTPS; loopback HTTP is allowed only with an
explicit port. Redirects are rejected and response bodies are bounded and stored
only as SHA-256 evidence.

## Adapter release train

The three production adapters can now be qualified as one release set:

```bash
onebridge release-adapters --adapters flowise,open_design,hermes
```

The release train performs a preflight that rejects missing or mock adapters,
runs and stores qualification evidence for the full set, and promotes versions
only when every requested adapter passes. Promotion of the compatible set is one
database transaction, so a failing Open Design or Hermes qualification does not
leave Flowise partially promoted.

To collect evidence without promotion:

```bash
onebridge release-adapters --adapters flowise,open_design,hermes --qualify-only
```

This command intentionally bypasses the normal
`ONEBRIDGE_REQUIRE_QUALIFIED_ADAPTERS` startup gate because its purpose is to
create the evidence needed to satisfy that gate. Normal API and worker startup
remain fail-closed when the gate is enabled.
