# Release operator

Production changes can be driven through the approval-gated release operator.

## Flow

1. Build a release plan for a blue/green slot and adapter set.
2. Approve the exact plan fingerprint.
3. Execute the approval once.
4. Observe release status and deployment actions.
5. Reconcile only from the configured stable traffic health path when needed.

The plan fingerprint binds the target slot, deployment version and endpoint,
current active slot/version, adapter versions, stable smoke URL and whether an
external switching hook is enabled. If any of these change after approval,
execution fails closed as stale.

Approvals expire after 30 minutes by default and are single-use. A database lease allows only one OpenClaw release operation at a time. A crashed lease can be reclaimed after the lease window, but a consumed approval is never silently reused. Configure the windows with `ONEBRIDGE_RELEASE_APPROVAL_TTL_SECONDS` and `ONEBRIDGE_RELEASE_LEASE_TTL_SECONDS`.

## CLI

The package exposes:

```text
onebridge-release
```

Examples:

```bash
onebridge-release plan --slot green
onebridge-release approve --slot green --actor release-manager
onebridge-release execute --approval-id <approval-id>
onebridge-release status
onebridge-release status --release-id <release-id>
onebridge-release status --approval-id <approval-id>
onebridge-release reconcile
```

The default adapter set is `flowise,open_design,hermes`. Mock adapters cannot
enter a production plan.

## HTTP API

The control plane exposes:

```text
POST /api/v1/releases/production/plan
POST /api/v1/releases/production/approve
GET  /api/v1/releases/production/approvals/{approval_id}
POST /api/v1/releases/production/execute
GET  /api/v1/releases/production
GET  /api/v1/releases/production/status
GET  /api/v1/releases/production/{release_id}
POST /api/v1/releases/production/actions/reconcile
```

When API authentication is enabled, the authenticated workspace is recorded as
the approval/execution actor. When authentication is disabled, approval requires
an explicit actor.

Set:

```bash
export ONEBRIDGE_RELEASE_CONTROLLER_REQUIRED=true
```

to disable direct adapter and deployment promotion through the HTTP API. Direct
promotion is also disabled automatically when an external OpenClaw switching
hook is configured.

Reconciliation probes only the configured
`ONEBRIDGE_OPENCLAW_SMOKE_URL`. The stable path must report `active_slot`;
if it reports `version`, the registry also verifies that version before
reconciling a pending deployment action.
