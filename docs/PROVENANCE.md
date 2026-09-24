# Source provenance

This OneBridge foundation was built by extracting and generalizing patterns already present in repositories owned by the same GitHub account.

## Vera extraction

The OneBridge worker sandbox and approval boundary are derived from the owner's Vera implementation patterns:

- fail-closed human approval before mutation/release authority;
- bounded workspace execution;
- Linux bubblewrap and macOS seatbelt strong-sandbox strategy;
- recovery and audit state kept separate from model output.

The code was reduced and renamed for OneBridge rather than importing Vera as a runtime dependency.

## CutPilot extraction

The OneBridge object-store and durable SQLite worker-queue patterns are derived from the owner's CutPilot implementation:

- atomic queue claim and explicit worker ownership;
- retry/requeue state;
- local versus S3/MinIO object-store abstraction;
- artifact URI handling;
- server API separated from workers.

The implementation is generalized around OneBridge task and adapter identities.

## FlowCraft-AI / MiniMax-H3 patterns

Generalized concepts:

- validate-before-execute;
- revision-aware workflows;
- adapters hiding upstream-specific graph/node identifiers.

## Licensing rule

Third-party services remain separate processes or dependencies. Their source code must not be copied into OneBridge Core unless licensing and provenance are explicitly reviewed and documented.
