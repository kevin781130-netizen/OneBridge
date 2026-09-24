# OneBridge extraction map

This repository intentionally reuses patterns already developed in sibling repositories owned by the same account. The goal is to avoid rebuilding proven infrastructure while keeping OneBridge's public contract independent.

| OneBridge area | Source project | Reused/generalized pattern | OneBridge module |
|---|---|---|---|
| Approval boundary | Vera | explicit operator approval; fail closed | `approval.py` |
| Strong sandbox | Vera | bubblewrap / seatbelt bounded workspace execution | `workers/sandbox.py` |
| Checkpoint lineage | Vera | digest-bound dependency checkpoint reuse | `checkpoints.py`, `workflow_graph.py` |
| Audit/recovery | Vera | immutable/digest-validated run evidence + secret redaction | `audit.py`, `redaction.py` |
| Worker queue | CutPilot | durable SQLite + PostgreSQL SKIP LOCKED claim/finish/fail/requeue, idempotent dispatch, stale claim recovery, disposable job workspaces and generic execution loop | `workers/queue.py`, `workers/postgres_queue.py`, `workers/factory.py`, `workers/workspace.py`, `workers/executor.py` |
| Background task execution | CutPilot | polling worker daemon pattern generalized to execute OneBridge task state machine outside API requests | `workers/task_runner.py`, `cli.py`, `api.py` |
| Object storage | CutPilot | local + S3/MinIO abstraction | `artifacts.py` |
| Workspace identity | CutPilot | salted API-key digests, revocation, bearer auth and tenant scope | `identity.py`, `auth.py`, `api.py` |
| Revision/rollback | FlowCraft-AI + CutPilot | snapshots, revision IDs, human text revisions, bounded compare and supersede lineage | `revisions.py`, `review.py`, `service.py` |
| Adapter capability contract | LyricGuard | explicit provider capabilities and health metadata | `adapters/capabilities.py` |
| Compatibility / blue-green | OneBridge plan + provider patterns | persistent evidence-gated adapter promotion plus blue/green OpenClaw deployment slots, health probes, atomic promotion and rollback | `compatibility.py`, `compatibility_service.py`, `deployment_switch.py` |
| Release integrity | SONICRAFT AI Strings + Vera | SHA-256 manifest verification, latest-approved revision gate and fail-closed release artifact | `integrity.py`, `release.py`, `release_gate.py` |
| Typed workflow validation | MiniMax-H3 / FlowCraft-AI | deterministic validation before execution and adapter output acceptance | `workflow_graph.py`, `adapters/validation.py` |
| External CLI adapter execution | FlowSonic | bounded subprocess invocation, persisted/redacted logs, version probe | `adapters/process.py` |
| Process supervision | Vera | timeout/output/workspace budgets and process-tree cleanup | `workers/supervisor.py` |
| Network egress | Vera | explicit default-deny host/path/method policy | `network_policy.py`, `endpoint_policy.py` |
| Supply-chain CI | Vera | SAST, secret scan, reproducible CycloneDX SBOM | `.github/workflows/security-supply-chain.yml` |
| Flowise integration | OneBridge + official Flowise API | Prediction API adapter with allowlisted overrideConfig and mock fallback | `adapters/flowise.py`, `adapters/factory.py` |
| OpenClaw ingress shim | OneBridge plan + Vera network boundary | stateless HTTP submit/status/artifacts/approve/retry/cancel with loopback/HTTPS policy | `openclaw_shim.py` |

| Context selection | Vera | explicit scopes, bounded selection, visible fallback/skips | `contextforge.py` |
| Plugin discovery | LyricGuard provider registry + OneBridge boundary | explicit allowlisted entry-point loading, duplicate rejection, configurable routing | `sdk.py`, `adapters/factory.py` |
| Review preview | CutPilot artifact handling + OneBridge review boundary | bounded object-store materialization and safe media allowlist | `preview.py`, `review_portal.py` |
| LINE channel | OneBridge plan + LINE Messaging API contract | raw-body HMAC verification, reply/push transport, task/progress mapping | `line_messaging.py`, `line_progress.py` |
| OpenClaw native plugin | OneBridge plan + current OpenClaw plugin SDK | tool-only TypeScript package retaining OneBridge as state owner | `integrations/openclaw-onebridge/` |

## Extraction rules

- Upstream-specific payloads stay inside adapters.
- OneBridge Core depends on stable contracts, not internal classes from sibling projects.
- Third-party source is not vendored by default.
- Every extracted pattern is renamed and narrowed to OneBridge's control-plane boundary.
- Security-sensitive behavior stays fail closed: missing approval, invalid lineage, bad hashes, or unavailable strong sandbox do not silently pass.
