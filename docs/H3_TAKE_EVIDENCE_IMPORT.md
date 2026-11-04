# H3 Take 匯出副本 → OneBridge 證據 ledger

取代 TRISYNORA `feat/p1-h3-real-take-gate-20261101` 的「消費端」必要功能，使 TRISYNORA 退役後
minimax-H3 的 Take 匯出仍有可驗證的下一步。

## 職責分工（不得重複實作）

| 步驟 | 負責方 | 位置 |
|---|---|---|
| 從 H3 ProjectStore 產生匯出副本（`project/snapshot.json` ＋ `render/<take>.mp4`） | **minimax-H3** | `backend/native_take_export.py`（H3 專屬產生邏輯，保留在 H3，不寫入 OneBridge） |
| 核對 Take 收據與 MP4 位元組、真實 ffprobe／ffmpeg 解碼、寫入證據 ledger | **OneBridge** | `onebridge evidence-import-h3-take` |
| 最終決策／放行 | **Vera**（VeraGate） | OneBridge 與本流程皆不核准 |

H3 exporter 為標準庫實作，對 TRISYNORA **沒有任何程式碼依賴**；與 TRISYNORA 的關聯僅是文件中的操作指引。

## 命令

需要 `pip install -e '.[evidence]'` 與 PATH 上的 `ffmpeg`、`ffprobe`。

```bash
onebridge evidence-import-h3-take \
  --evidence-root /handoff-staging/h3-take1 \
  --project-file project/snapshot.json \
  --video render/take001.mp4 \
  --h3-project-id proj1 --take-id take1 \
  --tenant-id organization1 --project-id campaign1 \
  --produced-at 2026-11-04T00:00:00Z \
  --ledger /approved/onebridge/temporal-evidence.sqlite
```

Windows 範例：

```powershell
py -3 -m onebridge.cli evidence-import-h3-take --evidence-root C:\handoff-staging\h3-take1 --project-file project/snapshot.json --video render/take001.mp4 --h3-project-id proj1 --take-id take1 --tenant-id organization1 --project-id campaign1 --produced-at 2026-11-04T00:00:00Z --ledger C:\approved\onebridge\temporal-evidence.sqlite
```

`--project-file` 與 `--video` 為 `--evidence-root` 底下的相對路徑（使用 `/`）。

| Exit code | 意義 |
|---|---|
| 0 | 收據與位元組吻合、真實解碼通過、3 筆記錄已寫入（重放為冪等，`inserted=0`） |
| 1 | `blocked`：收據、路徑、位元組、媒體政策或 ledger 任一檢查失敗；**不寫入任何證據** |
| 4 | `not_verified`：找不到 ffmpeg／ffprobe 或逾時；**不寫入任何證據，不得視為通過** |

## 驗證內容與邊界

- 快照內 Take 必須唯一，且具備 `h3_prompt_id`、唯一對應的 H3 workflow run、唯一 `status=complete` 的生成歷史、
  綁定到該 MP4 檔名的 `asset`，以及與**實際位元組**相符的 `source_media_sha256`。
- 拒絕重複 JSON key、NaN／Infinity、`..`／絕對路徑／反斜線、symlink、快照與影片相同檔案。
- 媒體時基（fps、影格數、尺寸）由真實 ffprobe 讀出，不接受任何宣告值；並以 ffmpeg 完整解碼。
  政策：單一 H.264／yuv420p 影像串流、無音訊、64–1920×1080、≤60 秒、≤128 MiB。
- 檢查期間來源若被替換（前後 SHA-256 不同）一律拒絕。
- 寫入 `ProjectSpec`／`ShotSpec`／`ArtifactRecord`；`rights.*` 一律 `false`；**不**產生 EvaluationRecord、
  HumanReview 或任何核准，輸出恆為 `release_approved=false`、`receiver_authorized=false`、`pointflux_imported=false`。
- 快照是操作員提供的未簽章資料：本流程不證明 H3／ComfyUI 真實渲染，也不證明匯出者身分；
  通過解碼不代表畫面品質。PointFlux 原生匯入不在本流程範圍。

## 與 TRISYNORA 舊指令的對照

| 舊（TRISYNORA 分支） | 新（OneBridge） |
|---|---|
| `tools/h3_take_source_gate.py --source-root … --project-file … --video …` | 包含於 `evidence-import-h3-take`（`adapters/h3_take.check_take_receipt`） |
| `tools/h3_take_exchange_bridge.py … --out <new dir> --target-project-id …` | `evidence-import-h3-take`；不再產生「reference-only 交接包」（見退役閘門文件第 6 節） |
| `tools/pointflux_media_probe.py`（通用部分） | `onebridge.evidence_contracts.media_probe.probe_mp4` |

## minimax-H3 文件中待更新的文字（H3 側，非執行依賴）

下列文件仍指示操作員使用 TRISYNORA 分支，需在 minimax-H3 repo 內改為上表的 OneBridge 指令。
此處未修改 minimax-H3（不在本次授權的寫入範圍內）：

- `docs/H3_NATIVE_TAKE_EXPORT_P1.md`：「Run the exporter below, then TRISYNORA's separate media/codec probe」與
  「On TRISYNORA's isolated `feat/p1-h3-real-take-gate-20261101` branch, the next bridge consumes…」
- `docs/H3_NATIVE_TAKE_EXPORT.md`（`feat/h3-native-take-export-20261101`）的「TRISYNORA handoff」段落
- `docs/H3_OFFLINE_GATE_SLICE14.md`、`docs/H3_P0P1_EVIDENCE_CLOSEOUT_SLICE21.md`、`docs/H3_NOVAFIELD_RC_FREEZE_20261102.md`
  與 `tools/h3_acceptance_closeout.py`：僅有「P1 export to TRISYNORA／PointFlux = NOT_RUN」的狀態標籤，不執行任何 TRISYNORA 程式。

## 驗證紀錄

- 使用 minimax-H3 `artifacts/h3-novafield-local-gate-20261102` 分支上的**真實** `backend/native_take_export.py`
  產生匯出副本（真實 libx264 MP4），再由 `onebridge evidence-import-h3-take` 匯入：exit 0、`inserted=3`，重放 `inserted=0`。
- 該相容性測試以 `H3_NATIVE_TAKE_EXPORT=<exporter 路徑>` 啟用（`tests/test_trisynora_h3_take.py`），未設定時為 skip（NOT_RUN）。
