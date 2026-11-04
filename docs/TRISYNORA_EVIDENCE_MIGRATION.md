# TRISYNORA v1 → OneBridge evidence-contract migration

Source: https://github.com/kevin781130-netizen/TRISYNORA
Pinned source commit: `debeb2aa590c08ee9395f8cebe914feca952260a`
Target: `onebridge.evidence_contracts` on an isolated OneBridge integration branch.

## Ported without changing published contract identities
- 7 JSON Schema 2020-12 documents in `src/onebridge/evidence_contracts/contracts/`.
- `validator.py`: strict contract/lineage validation; optional verified source bytes.
- `ledger.py`: append-only SQLite journal with per-project replay and hash-chain checks.
- `adapters/pointflux.py`: read-only PointFlux temporal-Judge import.
- 3 original adversarial test modules, adapted to OneBridge imports and CLI module path.
- Existing OneBridge commands extended with `evidence-validate` and
  `evidence-import-pointflux`; original CLI commands unchanged.
- `jsonschema==4.26.0` is an optional `evidence` extra; included in `dev`.

## Deliberately NOT changed
- Existing `onebridge.evidence` classifier, task authorization, approval,
  task/event audit, adapters, database migrations, worker execution, HTTP APIs.
- VeraGate and Vera final authority; PointFlux never executed by evidence importer.
- The published `trisynora/v1` schema identifier (preserves compatibility).

## Security / quality boundaries
- CLI validator read is unauthenticated and does not approve.
- OneBridge API has **no new external evidence-write route** in this migration.
- `HumanReview` ledger writes remain blocked absent a separately authenticated
  trusted review service.
- SHA-256 validation proves bytes against a claimed digest, not authentic source
  identity, good artistic quality, or consent.
- SQLite evidence journal is **not** merged with OneBridge's existing task
  database or its audit log; do not double-record or infer release success.

## Verification before deleting the source repository
```bash
python -m pip install -e '.[dev,evidence]'
python -m pytest tests/test_trisynora_contracts.py tests/test_trisynora_ledger.py tests/test_trisynora_pointflux_adapter.py tests/test_trisynora_integration.py
python -m pytest
onebridge evidence-validate examples/your-bundle.json --evidence-root /approved/evidence
```
The last command needs a real operator-supplied evidence bundle. Never claim
local tests or real-machine verification passed without an observed log.

Before retiring the source: verify current downstream consumers and imports,
preserve tags and history, confirm the new branch is integrated and tested,
then explicitly delete or archive the old repository. Do not delete the
source as part of this branch migration.

## 收斂與退役驗證
最終審計、功能對照矩陣、跨專案依賴掃描與刪除判定見
[TRISYNORA_RETIREMENT_GATE.md](TRISYNORA_RETIREMENT_GATE.md)。
本次收斂另補遷 `scripts/make_trisynora_schemas.py` 與 `docs/examples/trisynora/`，
並修復 ledger 鎖定時的錯誤遮蔽與 PointFlux CLI 的 sqlite 錯誤處理。

## H3 Take 匯入（第二次收斂）
自 TRISYNORA `feat/p1-h3-real-take-gate-20261101` 僅遷移「消費端」必要功能：
`onebridge evidence-import-h3-take`（Take 收據與媒體位元組綁定＋真實 ffprobe／ffmpeg 解碼＋寫入既有 ledger）。
H3 專屬產生邏輯留在 minimax-H3；不遷移 `schemas/v1` 交接包與 G1–G4 治理。
詳見 [H3_TAKE_EVIDENCE_IMPORT.md](H3_TAKE_EVIDENCE_IMPORT.md)。
