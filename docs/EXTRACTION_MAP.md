# OneBridge extraction map

This repository intentionally reuses patterns already developed in sibling repositories owned by the same account. The goal is to avoid rebuilding proven infrastructure while keeping OneBridge's public contract independent.

| OneBridge area | Source project | Reused/generalized pattern | OneBridge module |
|---|---|---|---|
| Approval boundary | Vera | explicit operator approval; fail closed | `approval.py` |
| Strong sandbox | Vera | bubblewrap / seatbelt bounded workspace execution | `workers/sandbox.py` |
| Checkpoint lineage | Vera | digest-bound dependency checkpoint reuse | `checkpoints.py`, `workflow_graph.py` |
| Audit/recovery | Vera | immutable/digest-validated run evidence + secret redaction | `audit.py`, `redaction.py` |
| Worker queue | CutPilot | durable SQLite + PostgreSQL SKIP LOCKED claim/finish/fail/requeue | `workers/queue.py`, `workers/postgres_queue.py`, `workers/factory.py` |
| Object storage | CutPilot | local + S3/MinIO abstraction | `artifacts.py` |
| Workspace identity | CutPilot | salted API-key digests, revocation, bearer auth and tenant scope | `identity.py`, `auth.py`, `api.py` |
| Revision/rollback | FlowCraft-AI | snapshots, revision IDs, rollback | `revisions.py` |
| Adapter capability contract | LyricGuard | explicit provider capabilities and health metadata | `adapters/capabilities.py` |
| Compatibility / blue-green | OneBridge plan + provider patterns | active/candidate promotion after health validation | `compatibility.py` |
| Release integrity | SONICRAFT AI Strings | SHA-256 manifest verification and fail-closed release evidence | `integrity.py`, `release.py` |
| Typed workflow validation | MiniMax-H3 / FlowCraft-AI | deterministic validation before execution and adapter output acceptance | `workflow_graph.py`, `adapters/validation.py` |
| External CLI adapter execution | FlowSonic | bounded subprocess invocation, persisted/redacted logs, version probe | `adapters/process.py` |
| Process supervision | Vera | timeout/output/workspace budgets and process-tree cleanup | `workers/supervisor.py` |
| Network egress | Vera | explicit default-deny host/path/method policy | `network_policy.py`, `endpoint_policy.py` |
| Supply-chain CI | Vera | SAST, secret scan, reproducible CycloneDX SBOM | `.github/workflows/security-supply-chain.yml` |
| Flowise integration | OneBridge + official Flowise API | Prediction API adapter with allowlisted overrideConfig and mock fallback | `adapters/flowise.py`, `adapters/factory.py` |

## Extraction rules

- Upstream-specific payloads stay inside adapters.
- OneBridge Core depends on stable contracts, not internal classes from sibling projects.
- Third-party source is not vendored by default.
- Every extracted pattern is renamed and narrowed to OneBridge's control-plane boundary.
- Security-sensitive behavior stays fail closed: missing approval, invalid lineage, bad hashes, or unavailable strong sandbox do not silently pass.
