# 共用人工標註工具

需要畀人對圖裁定、修改轉錄或覆核 golden 時，統一用 `jyut-ocr-bench` 嘅 `jyut_ocr_review`。本技能嘅 `scripts/review.py` 只負責啟動共用工具，唔保存另一套 server、介面或儲存邏輯。正常 OCR 流程繼續產生底稿及疑問清單；人工標註由用戶另行啟動。

## 啟動

需要 Python 3.10+ 同支援 `REVIEW_API_VERSION = 1` 嘅 [jyut-ocr-bench](https://github.com/laubonghaudoi/jyut-ocr-bench) checkout。喺本技能目錄執行：

```bash
export JYUT_OCR_BENCH_ROOT=/path/to/jyut-ocr-bench
python3 scripts/review.py --project-root /path/to/book-project --port 8765
```

打開命令印出嘅 localhost 網址。`--tool-root /path/to/jyut-ocr-bench` 優先於環境變數；兩者都冇先由 launcher 原檔嘅父目錄逐級搵 sibling `jyut-ocr-bench`。Symlink 安裝按 source 位置尋找；複製安裝嘅 skill 建議明確設環境變數。指定位置無效或 API 版本唔相容會停止，唔會改用其他安裝。所有其餘參數原樣交畀共用工具；相對資料路徑以執行命令嘅目錄為準。

其他入口：

```bash
python3 scripts/review.py validate-project --project-root /path/to/book-project
python3 scripts/review.py --audit-root /path/to/legacy-audit --port 8765
python3 scripts/review.py --dataset-root "$JYUT_OCR_BENCH_ROOT" --port 8765
python3 scripts/review.py import-goldgate --help
```

`--project-root` 用於一般 project-local queue；`--audit-root` 用於舊人工覆核 audit；`--dataset-root` 用於正式 benchmark 案例。新 queue schema、建立方式及舊資料匯入參數以共用工具嘅文件及 `--help` 為準，唔喺技能再維護一套 schema。

## 資料同人工答案

圖片、題目、原有人工紀錄、新裁定同 append-only 歷史留喺相應 project，工具 repo 只提供程式。一般書籍疑點唔會自動變成 benchmark sample。已有記錄按來源身份載入；唔複製另一份會分叉嘅答案庫，亦唔將舊接受日期當成新一輪覆核已完成。

用戶可以確認、修訂或標記仍存疑。保存裁定唔會自動套用到正文、golden 或相容快照；套用時按 exact occurrence 核對來源及目前版本，另行驗證改動。圖片、題目或定位有改，受影響嘅舊裁定保留作歷史，再對新證據覆核。完成數只代表已記錄嘅人工覆核範圍，唔保證全部文字零錯誤。

已遷移嘅舊 HTML／server 只作歷史追溯；唔再由舊實作儲存新答案。若共用工具未配置好，先修正入口，唔另寫臨時標註頁。

## 同步、標註、採納分開進行

1. **同步資料快照**：需要跑舊技能評測時，先用 Qwen 既有同步命令取指定 benchmark commit 嘅人工資料快照；呢一步唔開人工覆核，亦唔表示今輪已確認。其他項目沿用各自資料準備流程。
2. **人工標註**：經本入口啟動共用畫布，按原圖保存 project-local 裁定同歷史。
3. **採納答案**：另外核對適用版本、來源同範圍，再按項目流程更新正文或建立新 benchmark 資料版本。畫布儲存本身唔執行採納。
