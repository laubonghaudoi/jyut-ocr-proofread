# 共用人工標註工具

需要畀人對圖裁定、修改轉錄或覆核 golden 時，統一用 `jyut-ocr-bench` 嘅 `jyut_ocr_review`。本技能嘅 `scripts/review.py` 只負責啟動共用工具，唔保存另一套 server、介面或儲存邏輯。呢個係可選整合，一般 OCR／校對唔需要另一份 repo。正常流程繼續產生底稿及疑問清單；人工標註由用戶另行啟動。未配置介面時保留精確來源、版本同疑點，仍可繼續其他校對工作。

## 啟動

需要 Python 3.10+ 同 [jyut-ocr-bench](https://github.com/CanCLID/jyut-ocr-bench) checkout，佢嘅 `REVIEW_API_VERSION` 要係 `1` 或 `2`（`scripts/review.py --api-version` 會印出嚟）。現行 bench 係 API 2。喺本技能目錄執行：

```bash
. "${XDG_CONFIG_HOME:-$HOME/.config}/jyut-ocr/env.sh"
export JYUT_OCR_BENCH_ROOT=/path/to/jyut-ocr-bench
"$JYUT_OCR_PYTHON" scripts/review.py --port 8765
```

打開命令印出嘅 localhost 網址。`--tool-root /path/to/jyut-ocr-bench` 優先於環境變數；兩者都冇先由 launcher 原檔嘅父目錄逐級搵 sibling `jyut-ocr-bench`。Symlink 安裝按 source 位置尋找；複製安裝嘅 skill 建議明確設環境變數。指定位置無效或 API 版本唔相容會停止，唔會改用其他安裝：API 比 launcher 新就更新本技能，舊過或者讀唔到就更新 bench。其餘參數原樣交畀共用工具；相對資料路徑以執行命令嘅目錄為準。

**API 2**（現行 bench）只有一個答案庫：bench checkout 入面 git 跟蹤嘅 `sample-catalogue/v1.json`。提交即係確認，覆寫嗰個案例嘅現行答案；改動歷史靠 git，每標好一批就喺 bench repo commit 一次。

- 唔加 `--dataset-root`；確認啟動時 `Answers:` 指向該 repo 嘅 `sample-catalogue/v1.json`。指去其他副本（例如臨時 worktree）會印 `WARNING: answers are NOT written to this repo`，要停咗佢重開。
- 測試另用臨時副本同其他 port（例如 `--port 0`），唔將臨時答案庫交畀用戶標註。
- 第二套執行紀錄或結果瀏覽可以加 `--state-dir /path/to/second-review`。其他參數以共用工具嘅 `--help` 為準，唔喺技能再維護一套 schema。
- API 2 冇 project-local queue、舊 audit 同 Goldgate 匯入。`--project-root`、`--audit-root`（`--root`）、`validate-project`、`import-goldgate` 係 API 1 嘅，launcher 見到就停（exit 2），講明原因，唔交畀工具。

**API 1**（舊 bench checkout）仍然照舊啟動：

```bash
"$JYUT_OCR_PYTHON" scripts/review.py --project-root /path/to/book-project --port 8765
"$JYUT_OCR_PYTHON" scripts/review.py validate-project --project-root /path/to/book-project
"$JYUT_OCR_PYTHON" scripts/review.py --audit-root /path/to/legacy-audit --port 8765
"$JYUT_OCR_PYTHON" scripts/review.py import-goldgate --help
```

`--project-root` 用於一般 project-local queue；`--audit-root` 用於舊人工覆核 audit。

## 資料同人工答案

API 2：圖片同題目係 bench 封存嘅證據，答案只寫入 bench 嘅 `sample-catalogue/v1.json`，每案只留現行答案。API 1：圖片、題目、原有人工紀錄、新裁定同 append-only 歷史留喺相應 project。兩者嘅工具 repo 都只提供程式，本技能唔保存答案。一般書籍疑點唔會自動變成 benchmark sample；書嘅疑點照舊留喺疑問清單，由用戶對原圖處理。唔複製另一份會分叉嘅答案庫，亦唔將舊接受日期當成新一輪覆核已完成。

用戶可以確認或修訂讀法；API 1 仲可以標記仍存疑。保存答案唔會自動套用到正文、golden 或相容快照；套用時按 exact occurrence 核對來源及目前版本，另行驗證改動。圖片、題目或定位有改，受影響嘅舊答案要對新證據再覆核。完成數只代表已記錄嘅人工覆核範圍，唔保證全部文字零錯誤。

已遷移嘅舊 HTML／server 只作歷史追溯；唔再由舊實作儲存新答案。若共用工具未配置好，保存待覆核清單，使用介面前先修正入口；唔另寫會分叉答案歷史嘅臨時標註頁。

## 同步、標註、採納分開進行

1. **同步資料快照**：需要跑回歸評測時，先用 `scripts/sync_benchmark.py` 取指定 benchmark commit 嘅人工資料快照（見 `developer-notes.md`〈獨立 benchmark 同相容快照〉）；呢一步唔開人工覆核，亦唔表示今輪已確認。其他項目沿用各自資料準備流程。
2. **人工標註**：經本入口啟動共用畫布，按原圖保存答案（API 2 寫入 bench 嘅 `sample-catalogue/v1.json`；API 1 寫入 project-local 裁定同歷史）。
3. **採納答案**：另外核對適用版本、來源同範圍，再按項目流程更新正文或建立新 benchmark 資料版本。畫布儲存本身唔執行採納。
