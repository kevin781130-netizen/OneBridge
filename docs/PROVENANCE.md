# Source provenance

This OneBridge foundation was built by generalizing patterns already present in repositories owned by the same GitHub account.

## Vera-derived patterns

Generalized concepts:

- fail-closed human approval before mutation/release authority;
- checkpoint/resume and immutable recovery thinking;
- bounded execution and sandbox policy separation;
- audit evidence kept separate from model output.

No upstream third-party project is embedded into OneBridge by this change.

## CutPilot-derived patterns

Generalized concepts:

- queue/worker separation;
- local versus S3/MinIO object-store abstraction;
- artifact URI handling;
- explicit retry/requeue semantics;
- server API separated from workers.

## FlowCraft-AI / MiniMax-H3-derived patterns

Generalized concepts:

- validate-before-execute;
- revision-aware workflows;
- adapters hiding upstream-specific graph/node identifiers.

## Licensing rule

Third-party services remain separate processes or dependencies. Their source code must not be copied into OneBridge Core unless licensing and provenance are explicitly reviewed and documented.
