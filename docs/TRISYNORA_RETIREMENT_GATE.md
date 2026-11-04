# TRISYNORA 退役驗證（Retirement Gate）

審計日期：2026-11-04　審計環境：Linux 6.18、Python 3.13.18、jsonschema 4.26.0、pytest 9.2.0

## 最終判定

**`NOT_SAFE_TO_DELETE`**

`main@debeb2a` 的必要功能，以及 `feat/p1-h3-real-take-gate-20261101` 的「消費端」必要功能（Take 收據綁定＋真實媒體解碼），
已遷入 OneBridge 並以真實檔案與真實 ffmpeg 驗證；minimax-H3 **沒有任何執行期程式碼依賴** TRISYNORA。
但 TRISYNORA 仍有 **10 條未合併分支（其中 H3 分支僅部分遷移）與 9 個 PR** 的內容尚無保存或放棄的決定，
且 minimax-H3 文件仍有指向 TRISYNORA 分支的操作文字（純文件，需在 H3 repo 更新）。
永久刪除 Repository 會一併失去這些內容。詳見第 5、6、9 節。

## 1. 來源與目標 Git SHA

| 項目 | 值 |
|---|---|
| 來源 Repository | `kevin781130-netizen/TRISYNORA` |
| 來源 SHA（`main` 與 `feat/pointflux-temporal-evidence-v1-20261011` 皆指向此 commit） | `debeb2aa590c08ee9395f8cebe914feca952260a` |
| 目標 Repository／分支 | `kevin781130-netizen/OneBridge`／`integrate/trisynora-evidence-20261104` |
| 遷移 Commit | `9208bc972a70c396ba1a87ff42cac1f95209c39f` |
| 遷移基底（遷移前 OneBridge） | `5546ed7` |
| 第一次收斂 Commit | `388dd8c195e353ce9d834e3882514ae73531ff7d` |
| 第二次（H3 分支）來源 | `TRISYNORA` 分支 `feat/p1-h3-real-take-gate-20261101`＠`bb47c04fe9257f7262c7c297f85c31d8664a8f9d` |
| H3 側比對對象 | `minimax-H3`＠`artifacts/h3-novafield-local-gate-20261102`（`f85ac44d14902463c7f7447e68d97cd8ad041742`）與 `feat/h3-native-take-export-20261101`（`302ff5af139e9c133f78d40fba8df994869c6f2f`） |
| 第二次收斂 Commit | 即包含本文件的分支 HEAD（見回報；文件無法自我引用其雜湊） |

OneBridge `main` 未被修改。

## 2. 遷移檔案與功能對照矩陣

來源 `main@debeb2a` 共 22 個檔案，逐檔比對結果：

| 來源檔案 | OneBridge 位置 | 比對結果 |
|---|---|---|
| `docs/contracts/*.schema.json`（7） | `src/onebridge/evidence_contracts/contracts/` | `cmp` 位元組完全相同 |
| `trisynora/validator.py` | `evidence_contracts/validator.py` | 僅 `SCHEMAS` 路徑改為套件內 `contracts/` |
| `trisynora/ledger.py` | `evidence_contracts/ledger.py` | 相同；本次修 1 個缺陷（見第 7） |
| `trisynora/adapters/pointflux.py` | `evidence_contracts/adapters/pointflux.py` | 相同；本次修 1 個缺陷（見第 7） |
| `trisynora/__init__.py`、`adapters/__init__.py` | 對應 `__init__.py` | 僅 docstring 不同 |
| `tests/test_{contracts,ledger,pointflux_adapter}.py` | `tests/test_trisynora_*.py` | 29／10／14 個測試，數量一致；僅 import 與 CLI 模組路徑不同 |
| `make_schemas.py` | `scripts/make_trisynora_schemas.py`（**本次補遷**） | 僅輸出路徑不同；重建後 7 個 schema 與已入庫版本零差異 |
| `examples/demo-bundle.json`、`examples/assets/*` | `docs/examples/trisynora/`（**本次補遷**） | 位元組相同；有測試以真實位元組驗證 |
| `requirements.txt`（`jsonschema==4.26.0`） | `pyproject.toml` 的 `evidence`／`dev` extras | 版本釘選相同 |
| `README.md` | `README.md` 章節＋`docs/TRISYNORA_EVIDENCE_MIGRATION.md` | 信任邊界已保留（濃縮版） |
| `.gitignore` | 不需要 | OneBridge 既有 `.gitignore` 涵蓋；新增 `.gitattributes` 禁止證據檔換行轉換 |

功能對照（以 `main@debeb2a` 為範圍）：

| # | 必要功能 | 判定 | 證據 |
|---|---|---|---|
| 1 | 七套 JSON Schema | **PASS** | 位元組相同；wheel 內含 7 個 json 並在乾淨 venv（`-I`）載入成功；重建零差異 |
| 2 | 契約與 lineage 驗證 | **PASS** | 29 個原始契約測試通過 |
| 3 | 真實檔案 SHA-256 驗證 | **PASS** | CLI 端對端測試：位元組竄改、缺檔、symlink 逃逸、非法路徑皆被拒絕；僅 schema 驗證時明確回報 `evidence_files_verified=false` |
| 4 | SQLite EvidenceLedger | **PASS** | 10 個原始 ledger 測試＋新增忙碌鎖／竄改回滾測試 |
| 5 | 事件去重、重放、完整性檢查 | **PASS** | 重放、衝突事件、hash chain 竄改偵測測試通過 |
| 6 | PointFlux temporal Judge Adapter | **PASS** | 14 個原始 adapter 測試通過（合成媒體，見第 4 節限制） |
| 7 | HumanReview 安全限制 | **PASS** | validator CLI 與 ledger 皆拒絕未驗證身分的 HumanReview |
| 8 | CLI 入口與原始測試 | **PASS** | `onebridge evidence-validate`／`evidence-import-pointflux` 正負向通過；來源 53 個測試皆有對應 |

**main 範圍：8／8 PASS。** 輔助項（make_schemas、示例、依賴釘選）3／3 PASS。

### H3 分支（`feat/p1-h3-real-take-gate-20261101`）功能比對

該分支相對 `main` 新增 11 個 `tools/*.py`、12 個測試檔與 `schemas/v1` 交接包系統。逐項判定：

| 分支功能 | 判定 | 處置 |
|---|---|---|
| `tools/h3_take_source_gate.py`（Take 收據＋媒體位元組綁定、嚴格 JSON、路徑安全） | **PASS（已遷移）** | `adapters/h3_take.py::check_take_receipt`；8 個原測試情境皆有對應測試 |
| `tools/pointflux_media_probe.py` 的通用部分（真實 ffprobe 計幀、ffmpeg 完整解碼、政策限制、NOT VERIFIED 語意） | **PASS（已遷移）** | `media_probe.py`；時基由媒體讀出 |
| `tools/h3_take_exchange_bridge.py` 的消費端流程（前後雜湊防替換、寫入） | **PASS（已遷移）** | `onebridge evidence-import-h3-take`；改寫入既有 `trisynora/v1` ledger |
| H3 專屬「產生」邏輯（從 ProjectStore 匯出） | **保留在 H3** | `minimax-H3 backend/native_take_export.py`，不複製、不修改；已用其真實輸出驗證相容 |
| `tools/p1_video_handoff.py`、`preflight_exchange.py`、`verify_asset_package.py`、`pointflux_receiver_dry_run.py`、`schemas/v1`、`examples/v1`（reference-only 交接包） | **未遷移（有意）** | 以 `handoff-package`、`asset.schema.json`、`minimax-h3/export-snapshot` 搜尋 PointFlux 全部 119 分支與 minimax-H3 全部 55 分支皆**無命中**，唯一「消費者」是 TRISYNORA 自己的 dry-run，且恆回傳 receiver blocked；屬獨立的另一套契約路線（與本遷移的 `trisynora/v1` 並存會形成重複架構）。是否放棄由人決定 |
| `bridge_rehearsal.py`、`isolation_audit.py`、`g3_release_gate.py`、`g4_merge_review.py`、`governance/*.json`、`CONSTITUTION.md`、`AGENTS.md`、`.github/workflows/contract-tests.yml`、`scripts/test-local.ps1` | **不適用／需人決定** | 皆為 TRISYNORA repo 自身分支治理與 CI，repo 退役後無執行對象；文件內容隨 mirror bundle 保存 |

說明與操作方式：[H3_TAKE_EVIDENCE_IMPORT.md](H3_TAKE_EVIDENCE_IMPORT.md)。

### 與既有 OneBridge 模組的重複性檢查（補充）

- 路徑安全讀檔原本私有於 `adapters/pointflux.py`，已抽成 `evidence_contracts/_files.py`，PointFlux 與 H3 adapter 共用同一份（行為不變，原測試全數通過）。
- H3 快照解析雖與 H3 exporter 內的 `_check_native` 檢查同類，但屬「跨信任邊界的消費端複查」（匯出副本離開 H3 後可被替換），只在 OneBridge 保留一份；H3 內的是產生端檢查，兩者角色不同。

### 與既有 OneBridge 模組的重複性檢查

- `integrity.py`／`artifacts.py`／`audit.py` 僅有各自的 SHA-256 計算迴圈重疊（約 10 行）；
  證據模組要求同時核對位元組數、拒絕 symlink 逃逸、且為選用依賴，**刻意不改為共用**，以免動到既有資料格式與 API。
- 證據 ledger 是獨立 SQLite 檔，**不寫入**既有 task／audit 資料庫，資料格式互不影響。
- 不存在第二套最終驗收機制：validator／ledger 輸出恆為 `release_approved=false`，拒絕 HumanReview，
  `release_gate.py`、`approval.py` 保持唯一的放行邏輯；新增測試
  `test_evidence_contracts_not_wired_into_release_or_approval_authority` 保證除 `cli.py` 外，
  OneBridge 其他模組不得引用 `evidence_contracts`。Vera 的最終決策與 VeraGate 權威未被觸及。

## 3. 完整測試命令與實際結果（Linux／Python 3.13.18，本次實際執行）

```
python -m pip install -e '.[dev,evidence]'          → 成功
python -m pytest tests/test_trisynora_contracts.py tests/test_trisynora_ledger.py \
       tests/test_trisynora_pointflux_adapter.py tests/test_trisynora_integration.py
                                                    → 57 passed
python -m pytest                                    → 275 passed, 0 failed, 1 skipped
H3_NATIVE_TAKE_EXPORT=<H3 exporter 路徑> python -m pytest
                                                    → 276 passed, 0 failed, 0 skipped
```

唯一 skip 為「以 minimax-H3 真實 exporter 產生副本再匯入」的跨專案相容測試（需指定 exporter 路徑才執行，預設 NOT_RUN）；
上列第二行為指向 H3 `artifacts/h3-novafield-local-gate-20261102` 分支真實 `backend/native_take_export.py` 的實跑結果。

- 來源 TRISYNORA `pytest tests`：53 passed（基準）。
- H3 Take 匯入（`tests/test_trisynora_h3_take.py`，45 個）：以 ffmpeg 6.1.1 實際編碼的 H.264 MP4 驗證正向、重放冪等、
  非 MP4／帶音訊／mpeg4／yuv444p／過小尺寸／截斷檔／合法但被替換的影片、收據竄改、重複 take／run、未完成歷史、
  重複 JSON key、NaN、`../`、絕對路徑、反斜線、symlink、PATH 無 ffmpeg（exit 4 且不寫入）。
- 跨專案實跑（scratchpad，非入庫）：真實 `native_take_export.py` 輸出 → `onebridge evidence-import-h3-take`：
  exit 0、`inserted=3`；重放 `inserted=0`。
- 打包驗證：`pip wheel` 後安裝到乾淨 venv，以 `python -I` 於 repo 外載入 `onebridge.evidence_contracts.validator`、`adapters.h3_take`、`media_probe`、`_files`，7 個 schema 皆可讀。
- CLI（真實位元組）：示例 bundle 搭配 `--evidence-root` 回傳 `artifacts_verified=1, reports_verified=1`；
  位元組竄改、缺檔、symlink 逃逸、`../`、絕對路徑、`C:\`、`file://`、不存在的 root、HumanReview 皆回傳 `ok=false`、exit code 1。

## 4. 尚未驗證的環境與平台

| 項目 | 狀態 |
|---|---|
| Windows 路徑／換行行為 | **NOT_RUN** |
| macOS | **NOT_RUN** |
| Python 3.11／3.12（`requires-python>=3.11`；僅跑 3.13.18） | **NOT_RUN** |
| Windows symlink 測試（測試於 `nt` 自動 skip） | **NOT_RUN** |
| 真實 PointFlux／Chromium 產出之 MP4 與 judgement | **NOT_RUN**（PointFlux adapter 測試媒體為合成資料，僅驗證容器標頭） |
| 真實 H3／ComfyUI 渲染產生的 Take（真實 ProjectStore 與 GPU 輸出） | **NOT_RUN**（H3 exporter 以測試快照與 ffmpeg testsrc 影片實跑，非真實 H3 渲染） |
| Windows 上的 ffmpeg／H3 exporter 實跑 | **NOT_RUN** |
| H3 exporter 所在分支尚未合併至 minimax-H3 `main`（`main` 最後更新 2026-09-14） | 事實陳述；完整「H3 → OneBridge」鏈需該分支合併後才能在 H3 主線使用 |
| GitHub Actions／Docker | 依指示不使用 |
| 外部服務 | 無（證據模組不呼叫任何外部服務） |

## 5. 跨專案依賴檢查

方法：對每個 Repository 的 **所有遠端分支**（淺層 depth=1 抓取）做不分大小寫的 `git grep trisynora`，
涵蓋 import、CLI、Schema URL（`schemas.trisynora.dev`）、GitHub URL、Submodule、安裝依賴與 CI 設定。
GitHub code search 回傳 `incomplete_results`，未採信。

| Repository | 掃描範圍 | 結果 | 分類 |
|---|---|---|---|
| Vera | 242 分支＋預設分支 | 無任何命中 | 無依賴 |
| CutPilot | 27 分支 | 無任何命中 | 無依賴 |
| PointFlux | 119 分支 | 僅 3 份 docs（`COMMERCIAL_MVP_*`）敘述「不修改 TRISYNORA」，4 條分支 | 僅文件提及 |
| minimax-H3 | 55 分支 | 5 條分支含 `backend/native_take_export.py`（docstring：「…for the TRISYNORA staging bridge」）、`docs/H3_NATIVE_TAKE_EXPORT*.md`、`tools/h3_acceptance_closeout.py`（P1 gate 標為 `NOT_RUN`／`NOT_VERIFIED`）；**無** `import trisynora`、無 pip／URL／subprocess／工具路徑呼叫（已再次以 `trisynora`、`h3_take_source_gate`、`h3_take_exchange_bridge`、`pointflux_media_probe`、`p1_video_handoff` 全分支搜尋） | **執行依賴：無。操作文件引用：有（見下）** |
| OneBridge | 本分支 | 僅本次遷移自身 | — |
| Submodule／CI 引用 | 上述 4 個 Repo | `.gitmodules` 與 workflow 無命中 | 無 |

minimax-H3 說明（第二次收斂後）：

- **執行期依賴：無。** H3 exporter 只用標準庫、只輸出 `project/snapshot.json` 與 `render/<mp4>`；刪除 TRISYNORA 不會使任何 H3 程式失敗。
- **替代路徑：已建立並驗證。** 原先文件指示交給 TRISYNORA `feat/p1-h3-real-take-gate-20261101` 的 bridge；
  現在由 `onebridge evidence-import-h3-take` 取代（真實 exporter 輸出＋真實 ffmpeg 實跑通過）。
- **殘餘（非執行期）：** minimax-H3 內仍有文字指示操作員使用 TRISYNORA 分支（`docs/H3_NATIVE_TAKE_EXPORT_P1.md`、
  `docs/H3_NATIVE_TAKE_EXPORT.md` 及數份僅含 NOT_RUN 狀態標籤的文件；逐項清單見 H3_TAKE_EVIDENCE_IMPORT.md 末段）。
  這些文字在 TRISYNORA 刪除後會指向不存在的分支。修改需寫入 minimax-H3 的分支，不在本次授權範圍，故**未修改**，列為阻擋項。
  `docs/evidence/*`、`evidence/*` 下的歷史證據檔屬不可變紀錄，不應修改。

**UNKNOWN（未檢查，不得視為無依賴）**：帳號下其餘 Repository（如 tickets_hunter、LyricGuard、ScoreWeaver 等）；
各 Repository 的 Issue／PR 內文、GitHub Actions secrets／variables、Webhook、Release、Wiki、Pages；
本機端（使用者電腦）的 clone、虛擬環境或排程對 `trisynora` 套件的使用；PyPI 發佈情形。

## 6. 未遷移功能及原因

僅 `main@debeb2a` 在授權範圍內遷移。TRISYNORA 其他 ref（淺層抓取，tip 相對 `main` 的檔案差異）：

| 分支（PR） | 相對 main 的內容 | 遷移？ |
|---|---|---|
| `feature/pointflux-reference-evidence-adapter-20261018`（#9） | 新增 `trisynora/adapters/pointflux_reference.py`、`tests/test_pointflux_reference_adapter.py`、`docs/POINTFLUX_REFERENCE_ADAPTER_V1.md`——**與已遷移功能同類的 PointFlux 程式碼** | 未遷移，**BLOCKED** |
| `feat/p1-h3-real-take-gate-20261101` | H3 Take 交換 bridge、G3／G4 治理檔、`schemas/v1`（asset／capability／evidence／job／shot） | **部分遷移**：消費端必要功能已遷（見第 2 節）；`schemas/v1` 交接包系統與 G1–G4 治理未遷（無執行消費者／屬 repo 自身治理），需人決定放棄與否 |
| `feat/p1-h3-video-handoff-dryrun`（#2）、`feat/p1-pointflux-receiver-contract`（#3）、`feat/p1-real-mp4-probe-gate`（#4）、`feat/p1-g1-isolation-audit`（#5）、`feat/p1-g2-bridge-rehearsal`（#6）、`feat/g3-release-freeze-v01rc`（#7） | P1 交接／MP4 探測／隔離稽核／橋接演練／凍結審查之程式、文件與測試（H3 分支為這些分支的超集） | 通用媒體探測已隨 H3 分支遷移；其餘為 `schemas/v1` 交接包與 repo 治理，未遷移，需人決定 |
| `docs/constitution-v1`（#1） | CONSTITUTION、ADR、治理與測試；**刪除**了 `docs/contracts`（與本次遷移的 v1 契約路線相衝突） | 未遷移，需人決定 |
| `experiment/deepseek-harness-readonly-mcp`（#8） | 實驗性 DeepSeek harness（唯讀 MCP、receipts） | 未遷移，需人決定 |

不遷移原因：任務明定範圍為指定來源 SHA、禁止新增功能與擴充產品，且多數分支與 `main` 的契約路線互相衝突，
是否採用屬產品／治理決策，不應由本次整合代為決定。**是否放棄或保存須由擁有者決定。**

## 7. OneBridge 原有功能回歸結果

- 遷移前基底 `5546ed7`：`156 passed`（於 detached worktree 實測）。
- 第一次收斂：`231 passed`（原有 156 ＋ 75：原始 53、整合 4、CLI 與邊界 18）。
- 第二次（H3）收斂後：`275 passed, 0 failed, 1 skipped`（＋H3 Take 匯入 44 個通過、1 個跨專案相容測試預設 skip）；
  設定 `H3_NATIVE_TAKE_EXPORT` 後 `276 passed, 0 skipped`。既有 156 個測試全數仍在且通過。
- 未刪除或修改任何既有 OneBridge 測試與模組；既有 `onebridge.evidence.classify_evidence` 未被覆蓋（有測試）。

本次修復（皆維持 fail-closed，未放寬任何驗證）：

1. `ledger.py`：資料庫被鎖定導致 `BEGIN IMMEDIATE` 失敗時，`ROLLBACK` 會再拋 `no transaction is active`，遮蔽真正原因；
   改為僅在 `in_transaction` 時回滾。
2. `adapters/pointflux.py`：CLI 未捕捉 `sqlite3.Error`（如 ledger 目錄不存在）而輸出 traceback；
   改為與其他錯誤一致的 `{"ok": false}`＋exit code 1。
3. 打包與換行：新增 `.gitattributes`，避免證據範例檔與 schema 因換行轉換導致 SHA-256 失效。

第二次收斂新增（皆為 fail-closed）：`media_probe.py`、`adapters/h3_take.py`、`_files.py`（自 pointflux adapter 抽出共用）、
`onebridge evidence-import-h3-take`；工具缺失回傳 exit 4／`not_verified` 且不寫入任何證據。
OneBridge 核心模組仍不引用 `evidence_contracts`（既有邊界測試通過）；不新增任何依賴（ffmpeg 為執行期外部工具，非 Python 依賴）。

## 8. 刪除來源 Repository 後的復原方式

刪除前務必先備份**所有 ref（含 PR head）**，否則第 6 節的分支無法重建：

```bash
git clone --mirror https://github.com/kevin781130-netizen/TRISYNORA.git
git -C TRISYNORA.git bundle create ../TRISYNORA-all-refs.bundle --all
git bundle verify ../TRISYNORA-all-refs.bundle
```

- 以 bundle 還原：`git clone TRISYNORA-all-refs.bundle TRISYNORA-restored`，再建立新 Repository 推回各分支。
- `main` 的功能已在 OneBridge `integrate/trisynora-evidence-20261104`，來源 SHA 已記錄於本文件，可用上述 bundle 對照。
- GitHub 對已刪除 Repository 的還原期限與條件以 GitHub 官方政策為準，**本次未驗證**，不可作為唯一備援。
- Issue／PR 留言、Actions 設定等 GitHub 平台資料不在 git ref 內，bundle 不包含（UNKNOWN，見第 5 節）。

## 9. 最終判定與解除條件

**`NOT_SAFE_TO_DELETE`**

已解除：

- minimax-H3 → TRISYNORA 的**執行期**交接依賴（原 BLOCKED）：已有 OneBridge 替代路徑，且以 H3 真實 exporter 輸出、真實 ffmpeg 解碼驗證通過。

尚存阻擋：

1. **BLOCKED**：`feature/pointflux-reference-evidence-adapter-20261018`（#9）的 `pointflux_reference.py` adapter 與測試未遷移（與已遷移功能同類的 PointFlux 程式碼）。
2. **BLOCKED**：其餘未合併分支內容（`schemas/v1` 交接包系統、G1–G4 治理、`docs/constitution-v1`、deepseek harness）尚未決定保存或放棄。
3. **BLOCKED（文件）**：minimax-H3 內仍有指向 TRISYNORA 分支的操作文字（第 5 節），需在 H3 repo 更新；本次未獲寫入授權。
4. **UNKNOWN**：其餘 Repository、GitHub 平台資料（Issue／PR 內文、secrets、Webhook 等）、使用者本機環境之依賴。
5. **NOT_RUN**：Windows／macOS／Python 3.11–3.12；真實 PointFlux 產出；真實 H3／ComfyUI 渲染產生的 Take；Windows 上的 ffmpeg 實跑。

解除條件（由擁有者決定後再重審）：(a) 決定 `pointflux_reference` adapter 是否遷移（遷移則另開一刀）或宣告放棄；
(b) 以 mirror bundle 保存全部 ref，或明確宣告其餘分支工作放棄；(c) 在 minimax-H3 更新第 5 節所列文件，或宣告該文字可過時；
(d) 確認 UNKNOWN 項目；(e) 在 Windows／macOS 補跑測試，並以真實 H3 Take 做一次端到端。
