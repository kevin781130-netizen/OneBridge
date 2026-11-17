# OneBridge Licensing Provenance and Rights-Holder Declaration

**Record date:** 2026-11-17  
**Repository:** `kevin781130-netizen/OneBridge`  
**Branch:** `docs/onebridge-license-provenance-20261117`  
**Rights-holder identifier used in this repository:** `kevin781130-netizen`

## 1. Rights-holder declaration

The individual operating the GitHub account `kevin781130-netizen` has confirmed that they personally own or control the copyright in the original VERA-authored source material identified in this document.

The GitHub account name is used here as the requested repository attribution identifier. It is not represented as a verified legal name or corporate entity.

## 2. Limited authorization for VERA-derived material in OneBridge

The rights holder authorizes the inclusion, modification, distribution, sublicensing, and commercial use of the rights holder's own original VERA-authored material that has been extracted, adapted, or generalized into the following OneBridge files, **to the extent that the relevant material is original work owned or controlled by that rights holder**:

| OneBridge file or area | VERA source / provenance identified in the extraction map |
|---|---|
| `src/onebridge/approval.py` | `agent_core/approval_gate.py` — explicit, fail-closed operator approval |
| `src/onebridge/workers/sandbox.py` | `vera/agent_sandbox.py` — bounded workspace and strong-sandbox execution boundary |
| `src/onebridge/redaction.py` | `vera/redaction.py` — secret redaction |
| `src/onebridge/checkpoints.py` | VERA task-graph checkpoint and lineage mechanisms |
| `src/onebridge/workflow_graph.py` | VERA task-graph dependency and execution-wave mechanisms |
| `src/onebridge/workers/supervisor.py` | VERA process supervision, timeout, output and workspace budgets |
| `src/onebridge/network_policy.py`, `src/onebridge/endpoint_policy.py` | VERA default-deny network-egress policy patterns |
| `src/onebridge/audit.py` | VERA audit and recovery evidence patterns |
| `src/onebridge/integrity.py`, `src/onebridge/release.py`, `src/onebridge/release_gate.py` | VERA release-integrity and fail-closed release-gate patterns |
| `src/onebridge/contextforge.py` | VERA context-selection and bounded-context patterns |
| `.github/workflows/security-supply-chain.yml` | VERA supply-chain CI control patterns |

For the rights holder's original material in those OneBridge files, the rights holder authorizes distribution as part of the OneBridge Work under the **Apache License, Version 2.0**, as provided in the repository-root `LICENSE`.

This is a limited authorization for the identified OneBridge material. It is not a relicensing of the VERA repository.

## 3. VERA remains proprietary

The VERA repository remains separately owned and licensed under its existing proprietary license.

This record does not:

- grant the public a license to the VERA repository;
- authorize redistribution of VERA as a product;
- transfer ownership of VERA to OneBridge or to another person or entity;
- change VERA's existing proprietary license; or
- grant rights to VERA code that is not included in OneBridge.

The rights holder may continue to develop, license, sell, and distribute VERA independently.

## 4. Third-party material is excluded from this authorization

This authorization covers only material the rights holder owns or controls. It does not relicense third-party code, dependencies, assets, trademarks, or other upstream material.

In particular:

1. Any third-party-derived source included in a OneBridge file remains subject to its applicable upstream license and required notices.
2. MIT, Apache-2.0, BSD, LGPL, and other third-party notices must be preserved where required by the applicable license.
3. The VERA repository contains its own third-party provenance records, including `THIRD_PARTY_NOTICES.md` and `third_party/*/PORTING.md`. Those records must be checked at file/function level before any VERA-derived material with third-party provenance is redistributed.
4. OneBridge's other extracted or adapted sources identified in `docs/EXTRACTION_MAP.md` (including CutPilot, FlowCraft-AI, LyricGuard, MiniMax-H3, SONICRAFT AI Strings, and FlowSonic) are **not** covered by this VERA authorization. Their rights and notices must be cleared independently.
5. Runtime dependencies, optional dependencies, external engines, MCP servers, and separately installed tools retain their own licenses and terms. This document does not grant a right to bundle them.

If a component's provenance or license is unknown, do not treat it as cleared for commercial redistribution until it has been reviewed.

## 5. No trademark grant

No trademark, service-mark, product-name, or branding rights are granted by this document, except for nominative references reasonably needed to identify provenance.

## 6. Relationship to the repository license

This document supplements the repository-root `LICENSE` and records the rights holder's authorization for the identified VERA-derived original material to be included in the OneBridge Work under Apache-2.0.

It does not replace the Apache-2.0 license text, third-party notices, dependency inventory, release SBOM, or any other license-specific obligations. If there is a conflict between this summary and the applicable third-party license for third-party material, the applicable third-party license controls that material.

## 7. Commercial-release condition

This record establishes the stated provenance and authorization for the rights holder's own VERA-derived material. It does **not** by itself establish that every component in OneBridge is cleared for every distribution model.

Before a commercial release, maintain and review:

- a complete source-provenance inventory;
- exact release-artifact SBOMs for Python and TypeScript distributions;
- third-party license and NOTICE files for all shipped components;
- the LGPL compliance assessment for any distributed Psycopg components;
- the external-engine and MCP-server deployment terms; and
- the final package contents to ensure unapproved source or dependencies are not bundled.

---

**Rights-holder confirmation recorded:** The account operator `kevin781130-netizen` confirmed in the project-maintenance conversation on 2026-11-17 that they personally own or control the VERA copyright.

**Attribution:** Copyright (c) 2026 kevin781130-netizen, for original OneBridge contributions and the specifically authorized VERA-derived material described above.

This file is a project provenance and licensing record. It is not a substitute for jurisdiction-specific legal advice or a separately executed agreement where one is required.
