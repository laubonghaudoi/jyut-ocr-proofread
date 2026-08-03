---
name: jyut-ocr-proofread
description: 逐頁識別、校對同清理歷史中文 PDF／OCR Markdown；支援直排、長書分段、頁面家具、低信心與高信心錯字、缺頁／重複掃描、跨頁註腳、heading/list/table 重建及 deterministic final audit。Use when asked to OCR, transcribe, 校對, proofread, clean historical Chinese PDFs or Markdown, including image-only books and jyut-ocr repos.
---

# Jyut OCR 校對

用呢個 skill 將歷史中文 PDF 忠實轉錄成 Markdown，或者按同名 PDF 校對既有 OCR。目標係保存原文，而唔係現代化、改寫或潤飾。

## 先讀邊份規則

- 先讀目標 repo 嘅 `AGENTS.md` 或其他本地指示；本地規則優先。
- 做全文、final pass、長報告、章程、表格、票據、尾頁或速記錄時，讀 `references/jyut-ocr-detailed-rules.md`。
- 遇到反覆近形錯字、官職、人名、地名、數字符號或舊式標點時，讀 `references/common-ocr-traps.md`。
- 處理單一長書、掃描 spread、旋轉／重複／缺頁、分段並行、Apple Vision 底稿、跨頁註腳或外部同版復原時，讀 `references/long-book-workflow.md`。

## 原文依據

- 以 PDF 圖像為最終依據；OCR、Apple/macOS Vision、Gemini、GJ.cool、OCR JSON 同外部研究都只係底稿或提示。
- 保留原有異體字、舊字形同舊式標點，例如 `粤/粵`、`淸/清`、`决/決`、`吿/告`、`塲/場`、`箚/劄/剳/札`。唔好靜靜正規化。
- 優先核對 `[]` 低信心字；冇括號嘅字亦可能係高信心錯字，要按語境同圖像檢查。
- PDF 模糊而底稿合理時，保留最有根據讀法或報告 uncertainty；唔好憑空補字。
- 未逐頁視覺對照，就唔可以話「已逐頁同 PDF 對照完成」。

## OCR 工具次序

1. 立即 render PDF 並由圖像開始視覺識別，唔為任何服務停低。
2. Apple/macOS Vision 可以批量產生有 page provenance 嘅底稿；直排次序、表格、淡字、註腳同跨頁段落仍要按圖重建。
3. 只有已有 token、API 正常而且 5 秒內即時回應時，先用 GJ.cool 作輔助底稿。timeout、quota、auth、服務錯誤或 `wait for ... seconds` 後，本輪唔再自發重試。
4. Tesseract/Tessdata 係硬性禁用：唔安裝、唔調用、唔等待、唔讀取現成輸出、唔採納結果。

## 安全並行

- 多份獨立 Markdown/PDF pair 可以一檔一 agent 並行；每個 writer 嘅檔案範圍必須互斥。
- 單一長書亦可以按連續頁段分工，但每個 agent 只寫獨立暫存 segment，例如 `segments/p001-080.md`；只由主 agent 組裝最終 Markdown。
- 開工前列明 PDF 頁數、頁段、marker 範圍、表格／尾頁／缺頁風險同輸出檔。唔好畀多人同時改同一份最終檔。
- 主 agent 要 review 每段、驗證 segment 交界、處理衝突，並統一跑 final audit。subagent 回報唔等於全文完成。

## 標準流程

1. 找出 PDF/Markdown pair，確認 PDF scan page count，同時辨認書內 printed page number。
2. 建立或核對 page manifest：記錄 spread、旋轉、空白、缺 OCR、完整／局部重複、缺失印刷頁及刻意略去嘅 marker。
3. 保留檔首 `---` metadata；只有 PDF、既有 OCR 或可靠同版書目清楚提供時先補 metadata。
4. render 足夠解像度嘅頁圖或 crop；確認現有 render 數量同 PDF 頁數一致。
5. 改文前掃 `[]`、page marker、OCR notice、頁面家具、可疑拉丁／符號及已知高信心錯字。
6. 按頁推進：先修低信心字，再修語境唔通嘅高信心字，之後重建段落、heading、list、table、表單同圖說。
7. 校對期間保留 `<!-- page_... -->` 定位。逐個 marker 判斷刪除後應係正文 join、真正 paragraph break、單一換行 list boundary，定同一 list item 續文。
8. 若頁底註腳插斷跨頁正文，先接完整正文，再將註腳放到所屬完整段落後；核對註號順序。
9. 長書 segment 完成後由主 agent 統一 assemble，驗證 marker inventory 同所有已記錄例外，再 finalise。
10. 刪除臨時 marker／OCR notice，跑 deterministic audit，再逐項人工處理所有輸出；最後做獨立 read-only 結構覆核。

## 真正缺失嘅印刷頁

- 先證明頁面真係唔存在於 PDF，而唔係 OCR 漏頁、spread 次序錯、旋轉頁、重複頁或 render 漏檔。
- 先找本地舊版、raw OCR、完整 render 或同 repo 底稿。只有原 PDF 真缺頁時，先用外部同版資料復原正文。
- 外部資料必須可以確認為同一版本；優先完整同版頁圖。無完整頁圖時，以兩個獨立同版來源交叉核實字詞。
- 保存 evidence manifest，記錄 PDF hash、缺頁位置、來源、復原文字、證據強度同未能視覺核實嘅標點／行款。
- 在 metadata 同最終回覆透明披露；唔可以將非視覺復原講成逐頁圖像校對。

## Markdown 結構

- 全書題名用 `#`；大篇／主要文件用 `##`；章節、呈文、批答、箚覆等按父子關係使用 `###` 以下層級。
- heading 層級除咗按語義，亦要睇 PDF 字級、位置同副題關係。完成後檢查 heading 前後空行同有冇意外跳級。
- 同一語義段落寫成一個 Markdown 段落；唔保留 OCR 原始行寬。
- 目錄、條文、問題、辦法、證據同明顯枚舉可用 tight bullet list；保留原項號，項目之間唔加空行。
- 跨頁同一 list item 嘅續文縮入該 item；新項目另起 bullet。唔好因數字開頭就自動當新項。
- PDF 明顯係表格就重建 Markdown table；直排表格按版面理解欄列，唔照 OCR 行序硬排。
- 表格總數、條號、人名數、A–J 等結構 invariants 要另行計算驗證，唔好只靠閱讀語意。

## 頁面家具

- 刪除重複頁眉、頁腳、書脊、頁碼、承印資訊、卷冊／叢書邊欄、OCR 工具標題同空白頁 notice。
- 只刪明顯非正文。似家具但同正文黐住，或者課名／書名可能屬正文時，先對 PDF。
- OCR 話空白嘅頁仍要 render 檢查淡字、印章、表格、圖、尾頁小字同另一篇題名。

## Deterministic 驗證

技能附帶：

- `scripts/audit_ocr_markdown.py`：審核 annotated/final Markdown、marker inventory、顯式 boundary contract、OCR residue、soft break、heading/list spacing 同重複長段。
- `scripts/finalize_page_markers.py`：先 dry-run，逐個核對 marker 左右 context、boundary manifest 同預期移除數；`--write` 必須同時提供完整 `--boundary-manifest` 同另一個 `--output`，腳本唔會靠空行猜 join/break，亦唔會覆寫 annotated 底稿。

最少再人工檢查：

```bash
rg -n "\[|\]|<!--|gjcool|此頁OCR|[A-Za-z]" TARGET.md || true
rg -n "承印|廣州大典|史部|政書|經濟學K|縱濟學|澄 濟" TARGET.md || true
rg -n "[[:blank:]]+$" TARGET.md || true
git status --short -- TARGET.md
git diff --check -- TARGET.md
```

`git diff --check -- TARGET.md` 對 untracked 新檔可能實際無檢查內容；遇到新檔要另跑 trailing-whitespace audit，或者用 `git diff --no-index --check /dev/null TARGET.md`，並留意 exit 1 可以只代表檔案有差異，判斷以有冇 whitespace error 輸出為準。

完成回覆要交代：PDF scan 頁數、printed-page continuity、是否逐頁視覺核對、重複／缺頁、補 OCR／外部復原、剩餘疑問、殘留 markers／brackets／OCR notices、合法拉丁詞同舊標點、段落／heading／list／table audit，以及所有驗證結果。
