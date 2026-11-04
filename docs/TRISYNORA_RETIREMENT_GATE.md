# TRISYNORA 退役驗證（Retirement Gate）

審計日期：2026-11-04　審計環境：Linux 6.18、Python 3.13.18、jsonschema 4.26.0、pytest 9.2.0

## 最終判定

**`NOT_SAFE_TO_DELETE`**

`main@debeb2a` 的必要功能已完整、可驗證地遷入 OneBridge；但 TRISYNORA 另有 **11 條未合併分支與 9 個 PR**，
含尚未遷移的程式碼與文件，且 minimax-H3 的交接文件指向其中一條分支。永久刪除 Repository 會一併失去這些內容。
詳見第 6、5 節。在這些項目由人決定「保存或放棄」之前，不得刪除。

## 1. 來源與目標 Git SHA

| 項目 | 值 |
|---|---|
| 來源 Repository | `kevin781130-netizen/TRISYNORA` |
| 來源 SHA（`main` 與 `feat/pointflux-temporal-evidence-v1-20261011` 皆指向此 commit） | `debeb2aa590c08ee9395f8cebe914feca952260a` |
| 目標 Repository／分支 | `kevin781130-netizen/OneBridge`／`integrate/trisynora-evidence-20261104` |
| 遷移 Commit | `9208bc972a70c396ba1a87ff42cac1f95209c39f` |
| 遷移基底（遷移前 OneBridge） | `5546ed7` |
| 本次收斂 Commit | 即包含本文件的分支 HEAD（見回報；文件無法自我引用其雜湊） |

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

未遷移範圍（**BLOCKED**，見第 6 節）：TRISYNORA 其他分支上的內容。

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
python -m pytest                                    → 231 passed, 0 failed, 0 skipped
```

- 來源 TRISYNORA `pytest tests`：53 passed（基準）。
- 打包驗證：`pip wheel` 後安裝到乾淨 venv，以 `python -I` 於 repo 外載入 `onebridge.evidence_contracts.validator`，7 個 schema 皆可讀。
- CLI（真實位元組）：示例 bundle 搭配 `--evidence-root` 回傳 `artifacts_verified=1, reports_verified=1`；
  位元組竄改、缺檔、symlink 逃逸、`../`、絕對路徑、`C:\`、`file://`、不存在的 root、HumanReview 皆回傳 `ok=false`、exit code 1。

## 4. 尚未驗證的環境與平台

| 項目 | 狀態 |
|---|---|
| Windows 路徑／換行行為 | **NOT_RUN** |
| macOS | **NOT_RUN** |
| Python 3.11／3.12（`requires-python>=3.11`；僅跑 3.13.18） | **NOT_RUN** |
| Windows symlink 測試（測試於 `nt` 自動 skip） | **NOT_RUN** |
| 真實 PointFlux／Chromium／FFmpeg 產出之 MP4 與 judgement | **NOT_RUN**（測試媒體為合成資料，僅驗證容器標頭） |
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
| minimax-H3 | 55 分支 | 5 條分支含 `backend/native_take_export.py`（docstring：「…for the TRISYNORA staging bridge」）、`docs/H3_NATIVE_TAKE_EXPORT*.md`、`tools/h3_acceptance_closeout.py`（P1 gate 標為 `NOT_RUN`／`NOT_VERIFIED`）；**無** `import trisynora`、無 pip／URL 依賴 | **工作流交接依賴（見下）** |
| OneBridge | 本分支 | 僅本次遷移自身 | — |
| Submodule／CI 引用 | 上述 4 個 Repo | `.gitmodules` 與 workflow 無命中 | 無 |

minimax-H3 說明：其匯出器只輸出檔案，不 import TRISYNORA；但文件指示將匯出結果交給
TRISYNORA **`feat/p1-h3-real-take-gate-20261101`** 分支的 bridge（`--source-root <out> --project-file …`）。
該 bridge 不在 `main`，亦未遷入 OneBridge，**目前沒有替代路徑** → 此交接依賴判定為 **BLOCKED**
（且文件自身標示該 P1 路徑為 NOT_RUN，從未實測）。

**UNKNOWN（未檢查，不得視為無依賴）**：帳號下其餘 Repository（如 tickets_hunter、LyricGuard、ScoreWeaver 等）；
各 Repository 的 Issue／PR 內文、GitHub Actions secrets／variables、Webhook、Release、Wiki、Pages；
本機端（使用者電腦）的 clone、虛擬環境或排程對 `trisynora` 套件的使用；PyPI 發佈情形。

## 6. 未遷移功能及原因

僅 `main@debeb2a` 在授權範圍內遷移。TRISYNORA 其他 ref（淺層抓取，tip 相對 `main` 的檔案差異）：

| 分支（PR） | 相對 main 的內容 | 遷移？ |
|---|---|---|
| `feature/pointflux-reference-evidence-adapter-20261018`（#9） | 新增 `trisynora/adapters/pointflux_reference.py`、`tests/test_pointflux_reference_adapter.py`、`docs/POINTFLUX_REFERENCE_ADAPTER_V1.md`——**與已遷移功能同類的 PointFlux 程式碼** | 未遷移，**BLOCKED** |
| `feat/p1-h3-real-take-gate-20261101` | H3 Take 交換 bridge、G3／G4 治理檔、`schemas/v1`（asset／capability／evidence／job／shot） | 未遷移，**BLOCKED**（minimax-H3 依此） |
| `feat/p1-h3-video-handoff-dryrun`（#2）、`feat/p1-pointflux-receiver-contract`（#3）、`feat/p1-real-mp4-probe-gate`（#4）、`feat/p1-g1-isolation-audit`（#5）、`feat/p1-g2-bridge-rehearsal`（#6）、`feat/g3-release-freeze-v01rc`（#7） | P1 交接／MP4 探測／隔離稽核／橋接演練／凍結審查之程式、文件與測試 | 未遷移，**BLOCKED** |
| `docs/constitution-v1`（#1） | CONSTITUTION、ADR、治理與測試；**刪除**了 `docs/contracts`（與本次遷移的 v1 契約路線相衝突） | 未遷移，需人決定 |
| `experiment/deepseek-harness-readonly-mcp`（#8） | 實驗性 DeepSeek harness（唯讀 MCP、receipts） | 未遷移，需人決定 |

不遷移原因：任務明定範圍為指定來源 SHA、禁止新增功能與擴充產品，且多數分支與 `main` 的契約路線互相衝突，
是否採用屬產品／治理決策，不應由本次整合代為決定。**是否放棄或保存須由擁有者決定。**

## 7. OneBridge 原有功能回歸結果

- 遷移前基底 `5546ed7`：`156 passed`（於 detached worktree 實測）。
- 目前分支：`231 passed, 0 failed, 0 skipped` ＝ 原有 156 ＋ 遷移後 75（原始 53、整合 4、本次新增 CLI 與邊界 18）。
- 未刪除或修改任何既有 OneBridge 測試與模組；既有 `onebridge.evidence.classify_evidence` 未被覆蓋（有測試）。

本次修復（皆維持 fail-closed，未放寬任何驗證）：

1. `ledger.py`：資料庫被鎖定導致 `BEGIN IMMEDIATE` 失敗時，`ROLLBACK` 會再拋 `no transaction is active`，遮蔽真正原因；
   改為僅在 `in_transaction` 時回滾。
2. `adapters/pointflux.py`：CLI 未捕捉 `sqlite3.Error`（如 ledger 目錄不存在）而輸出 traceback；
   改為與其他錯誤一致的 `{"ok": false}`＋exit code 1。
3. 打包與換行：新增 `.gitattributes`，避免證據範例檔與 schema 因換行轉換導致 SHA-256 失效。

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

阻擋項目：

1. **BLOCKED**：11 條未合併分支（含 `pointflux_reference` adapter 與 P1 H3 bridge）尚未決定保存或放棄。
2. **BLOCKED**：minimax-H3 的 Take 匯出交接指向未遷移的 TRISYNORA 分支，無替代路徑。
3. **UNKNOWN**：其餘 Repository、GitHub 平台資料、使用者本機環境之依賴。
4. **NOT_RUN**：Windows／macOS／Python 3.11–3.12 與真實 PointFlux 產出驗證。

解除條件（由擁有者決定後再重審）：(a) 以 mirror bundle 保存全部 ref，或明確宣告分支工作放棄；
(b) 決定 minimax-H3 的交接路徑（遷移 bridge 或停用該路徑）；(c) 確認 UNKNOWN 項目；(d) 在 Windows／macOS 補跑測試。
