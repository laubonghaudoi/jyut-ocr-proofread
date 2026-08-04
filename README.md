# 粵文 OCR 校對

呢個係 Codex plugin，用嚟按同名 PDF 校對歷史中文 OCR Markdown，流程基於 `jyut-ocr` repo 嘅校對守則。

plugin 會安裝一個 skill：`jyut-ocr-proofread`。skill body 同 bundled OCR rules 都用粵文寫，令 agent 處理 OCR 校對時直接讀到同 repo 指示一致嘅工作語言。

## 佢會做咩

- 以 PDF 圖像作最終依據；
- 保留 PDF 原有字形、異體字、舊字形同舊式標點；
- 查 PDF 後先刪頁眉、頁腳、書脊、承印資訊、OCR notice 等頁面家具；
- 優先處理低信心括號同語境唔通嘅高信心 OCR 錯字；
- 先讀本書凡例，分清 OCR confidence bracket 同原書訂正／增補／缺損符號；
- 按 PDF 版面重整 heading、段落、bullet list、table；
- 新 OCR 或補漏時先用視覺識別，GJ.cool 只作背景並行或極短時間輔助；
- 單一長書可以按頁段寫入獨立 segment，再由主 agent 驗證交界同統一合併；
- 分辨 PDF scan page 同書內 printed page，記錄旋轉、spread、重複掃描同真正缺頁；
- 用 bundled scripts 檢查 page marker、跨頁黐行、tight list、table 欄數、成對標點、Markdown 結構、final residue 同 stale assembly；
- 長文或 final pass 時載入更完整嘅粵文 `jyut-ocr` 校對 reference。

## 0.2.2 更新重點

- 防止跨頁 list 續文／nested item 因預先 `.strip()` 而被誤標 `join`；audit／finalizer 會要求 `list-cont`、`list-new` 或經視覺確認並由 receipt 保存嘅真正結構 break。
- Receipt 必須封存 final audit 實際使用嘅 notation policy、source exceptions 同 page manifest，避免定稿後新增白名單改變結果。
- Final audit 可用目前 PDF 路徑重驗來源 SHA、scan sequence、sealed annotated marker coverage 同每張 render hash；刻意略去 marker 嘅 scan 要喺 manifest 明寫 `marker_expected: false`。
- Review sidecar 狀態禁止降級，獨立覆核要記 writer 以外嘅 reviewer、頁段同 evidence hash；長目錄每個差異亦要有已核圖 disposition。

## 0.2.1 更新重點

- 新增本書 notation policy：凡例證明 ASCII `[ ]` 係編者訂正時，可以同 OCR 低信心 markup 分開處理，避免機械刪除來源符號。
- 新增 exact line／column／symbol source exception ledger；原書本身未閉或錯序引號／括號要先核圖，位置、line text 或 issue 改變就會報 stale exception。
- Finalizer 可以輸出 SHA-256 assembly receipt，hash ordered upstream segments、combined annotated、boundary manifest 同 final；late patch 或 receipt 後修改會令 final audit 失敗。
- 結構 audit 補 tight-list continuation、table 欄數、delimiter 配對、space-before-tab、missing final newline 同 EOF 多餘空行。
- 將高信心 correction propagation、平行篇章互校、multi-pass OCR disagreement 同「收齊所有 auditor 後重新 assemble」寫成 release gate；所有差異只產生核圖候選。

## 0.2.0 更新重點

- 長單檔可安全並行：每個 agent 只寫獨立頁段 segment，主 agent 獨佔最終檔、assemble 同跨段覆核。
- 新增 long-book page manifest 流程，同時追蹤 scan page／printed page、spread、旋轉、空白、完整或局部重複、缺失印刷頁。
- 原 PDF 真缺頁時，先證實缺頁，再以可確認嘅同版來源交叉復原，保存 evidence manifest，並披露非視覺核實部分。
- 明確分類 page marker 移除後應係正文 join、段落 break、list boundary 定 item continuation；頁底註腳插斷正文時先重建完整正文。
- 新增純 Python stdlib audit/finalize scripts，檢查 marker inventory、顯式 boundary manifest、soft break、heading/list spacing、OCR residue 同重複長段；無完整 boundary contract 時拒絕寫入。
- Final pass 加入結構 invariants，例如條號、人名數、A–J 項目同表格總數；亦補充 untracked 新檔令 `git diff --check` 失效嘅盲點。
- Apple/macOS Vision 可以加速建立有 page provenance 嘅底稿，但直排次序、註腳、表格同跨頁內容仍以 PDF 圖像為準。

詳細版本摘要見 [CHANGELOG.md](CHANGELOG.md)。

## Bundled audit scripts

喺 skill 目錄執行。先 audit annotated 稿，再用同一份 JSON boundary manifest dry-run；逐個 marker 核對輸出後先加 `--write`，並寫到另一個 final 檔：

```bash
python3 scripts/audit_ocr_markdown.py TARGET-annotated.md --mode annotated --expected-count 240 --skip-pages 17 --boundary-manifest boundaries.json
python3 scripts/finalize_page_markers.py TARGET-annotated.md --expected-count 240 --skip-pages 17 --boundary-manifest boundaries.json
python3 scripts/finalize_page_markers.py TARGET-annotated.md --expected-count 240 --skip-pages 17 --boundary-manifest boundaries.json --output TARGET.md --receipt assembly-receipt.json --upstream ordered-segments.json --upstream segments/p001-120.md --upstream segments/p121-240.md --upstream notation-policy.json --upstream source-exceptions.json --upstream page-manifest.json --write
python3 scripts/audit_ocr_markdown.py TARGET.md --mode final --notation-policy notation-policy.json --source-exceptions source-exceptions.json --page-manifest page-manifest.json --source-pdf /current/path/source.pdf --assembly-receipt assembly-receipt.json
```

`boundaries.json` 用 marker page 作 key，label 係 `start`、`join`、`paragraph`、`structural`、`list-cont` 或 `list-new`。每個實際 marker 必須恰好有一條記錄；無 manifest 時 dry-run 只會列 context，任何 `--write` 都會拒絕。寫入亦必須指定另一個 `--output`，腳本永不覆寫 annotated 底稿。完整 schema 同跨頁例子見 `references/long-book-workflow.md`。

長書用 `--receipt` 同按次序重複嘅 `--upstream` 建立 SHA-256 hash receipt；任何 segment、annotated、boundary 或 final 後續修改都要重新 finalize。Final audit 會讀嘅 notation policy、source exception 同 page manifest 亦要列為 upstream；如果本書冇相應來源符號／異常，先省略嗰啲 JSON 選項。完整 completion gate schema 見 `references/release-gate.md`。

## 由 GitHub 安裝

發佈後，可以直接將呢個 repo 加入 Codex plugin marketplace：

```bash
codex plugin marketplace add laubonghaudoi/jyut-ocr-proofread --ref main
codex plugin add jyut-ocr-proofread@jyut-ocr-proofread
```

安裝後開新 Codex thread，先會載入新 plugin/skill metadata。

## 由本地 clone 安裝

如果你已經 clone 咗 repo，亦可以用本地路徑加入 marketplace：

```bash
codex plugin marketplace add /path/to/jyut-ocr-proofread
codex plugin add jyut-ocr-proofread@jyut-ocr-proofread
```

## GJ.cool token

GJ.cool 唔係必需。若你有 token，可以喺自己本機 shell 或本地環境設定其中一個變數：

- `GJCOOL_ACCESS_TOKEN`
- `GJCOOL_AUTH`

唔好 commit 真 token。`.env.example` 只列出支援嘅變數名。skill 會先由 PDF render 圖像做視覺轉寫；只有 GJ.cool 即時有結果時，先會用佢做底稿再核 PDF。

## Repo 結構

```text
.agents/plugins/marketplace.json
plugins/jyut-ocr-proofread/
  .codex-plugin/plugin.json
  skills/jyut-ocr-proofread/
    SKILL.md
    agents/openai.yaml
    references/common-ocr-traps.md
    references/jyut-ocr-detailed-rules.md
    references/long-book-workflow.md
    references/release-gate.md
    scripts/_ocr_markdown.py
    scripts/audit_ocr_markdown.py
    scripts/finalize_page_markers.py
tests/
  test_ocr_markdown_scripts.py
```

## License

MIT
