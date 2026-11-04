# PointFlux reference-to-evidence adapter（自 TRISYNORA 遷入）

來源：TRISYNORA `feature/pointflux-reference-evidence-adapter-20261018`@`01ba38c08cfe`。已整合至 `onebridge.evidence_contracts.adapters.pointflux_reference`，沿用 OneBridge 既有 schema、`validate_bundle` 與 `_files.read_local`／`strict_json`。

**Scope:** Convert an existing `pointflux-h3-shot-exchange/v1` JSON into the **existing TRISYNORA main-line** `ProjectSpec` + `ShotSpec` evidence *metadata* records. It does not replace PointFlux's native exchange schema, H3's own import/bind API, or the separate draft `trisynora.shot/v1` exchange-envelope family. Neither external product is invoked, installed, edited or bound.

This is not a 3D Camera/Rig/Motion converter, generated-media importer, H3 render controller, authenticated producer attestation or release gate. In particular **no ArtifactRecord, EvaluationRecord, HumanReview or DecisionRecord is fabricated**. Camera, timelines and asset references remain owned by native PointFlux/H3, not embedded in TRISYNORA's `ShotSpec` v1.

## Inputs and example

1. Export an exchange JSON with the existing isolated PointFlux `scripts/export-shot-exchange.mjs` branch.
2. Independently place the *original native Scene IR UTF-8 files* under an operator-approved local directory. Supply a small JSON `scene-map.json` mapping every exchanged `shotId` to its project-relative Scene IR path. Do not submit private native Scene IR to this repository.
3. 在 OneBridge（`pip install -e '.[evidence]'`）執行（唯讀轉換，exit 0 成功、1 拒絕）：

```sh
onebridge evidence-pointflux-reference \
  --exchange /operator/staging/pointflux-shot-handoff-v1.json \
  --scene-root /operator/approved/pointflux-scenes \
  --scene-map /operator/staging/scene-map.json \
  --tenant-id tenant_demo --project-name "Demo commercial" \
  --produced-at 2026-10-18T12:00:00+08:00 \
  --out /operator/staging/pointflux-reference-records.json
```

Example scene map: `{"shot_A":"shots/shot_A.pf.json"}`. The adapter **rejects** missing/mismatched mappings and mismatched scene SHA-256; `sourceSha256` is the digest of original UTF-8 Scene IR bytes, *not* the MP4, thumbnail or normalized JSON. The exchange file itself is independently hashed into ProjectSpec provenance. The output is **exclusive-create** and cannot be written under the scene root.

After conversion, `onebridge.evidence_contracts.validator.validate_bundle` validates records against published v1 schemas. **本 CLI 不寫入 EvidenceLedger，也不放行任何東西**；這些中繼資料本身不構成證據核准（輸出與既有 ledger 相容，已有測試）。 Any future authorized ledger import should perform independent file-byte verification and producer authentication at its trusted boundary.

## Deliberate compatibility constraints

- Only PointFlux-origin Scene IR `1.4`, `reference-only`, and `requiresExplicitBind=true`.
- Integer 1–120 FPS, <=60 seconds per shot, 1–30 shots. A mixed-FPS or mixed-resolution project is rejected rather than silently assigning a wrong `ProjectSpec.delivery`.
- Both `projectId` and `shotId` must be at least two characters; the TRISYNORA v1 record schema is stricter than the PointFlux ID pattern for one-character IDs. No automatic ID rewriting.
- Only project-relative local scene paths, no symlinks, URL fetches, shell, model calls, browser, GPU, media decoding, or writes to source repositories.
- Source hashes are byte-verified **against operator-supplied local files**. The adapter independently re-derives the **static Camera, output/FPS, timeline event frames, and source/target asset references** from those Scene IR bytes and rejects mismatches. This is not a replacement for PointFlux's native Scene IR schema/semantic validator, and it does not prove the producer's identity, H3/PF 3D coordinate equivalence, asset file existence, license, artistic quality, or successful rendering.
- `rights.*` are always false; `producer` is `operator-import`; evidence is not signed. Explicit user H3 bindings and native review authority remain unchanged.
- Repeating the identical exchange, tenant and timestamp makes deterministic record IDs. Changing the timestamp for an identical exchange is not a valid ledger replay; callers must preserve recording time.

## Tests

```sh
python -m pytest tests/test_trisynora_pointflux_reference.py
```

24 個測試：10 個自 TRISYNORA 遷入的原始離線測試（metadata 對映、Scene IR SHA-256 位元組完整性、Camera／Timeline／output／asset 偽造、偽造摘要、路徑穿越、未授權 handoff、未知欄位、非有限數值、缺少 map、單字元 ID、混合交付規格），
加上 OneBridge 新增 14 個：CLI 正向、與 ledger 相容、拒絕覆寫既有輸出、輸出不得位於來源專案內、竄改位元組、不安全／缺失路徑、symlink 場景檔與 symlink 根目錄、畸形輸入、不接入放行／核准模組。

未驗證（NOT_RUN）：以 PointFlux 真實 `scripts/export-shot-exchange.mjs` 產生 exchange 再轉換（需 npm 依賴安裝，本次環境未執行）；
已用 PointFlux 入庫 fixture `tests/exchange/fixtures/shot-exchange-v1.json` 通過本 adapter 的結構驗證（僅結構，其 sourceSha256 為合成值，無法做 Scene IR 位元組比對）。
真實 H3 service 整合、Windows／macOS 亦為 NOT_RUN。
