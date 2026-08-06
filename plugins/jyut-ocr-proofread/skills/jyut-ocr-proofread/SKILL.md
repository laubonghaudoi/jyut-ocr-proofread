---
name: jyut-ocr-proofread
description: 逐頁識別、校對同清理歷史中文 PDF／OCR Markdown；支援直排、長書分段、本書校勘符號、頁面家具、低信心與高信心錯字、缺頁／重複掃描、跨頁註腳、heading/list/table 重建、來源例外及 deterministic release audit。Use when asked to OCR, transcribe, 校對, proofread, clean historical Chinese PDFs or Markdown, including image-only books and jyut-ocr repos.
---

# Jyut OCR 校對

用呢個 skill 將歷史中文 PDF 忠實轉錄成 Markdown，或者按同名 PDF 校對既有 OCR。目標係保存原文，而唔係現代化、改寫或潤飾。

## 先讀邊份規則

- 先讀目標 repo 嘅 `AGENTS.md` 或其他本地指示；本地規則優先。
- 做全文、final pass、長報告、章程、表格、票據、尾頁或速記錄時，讀 `references/jyut-ocr-detailed-rules.md`。
- 遇到反覆近形錯字、官職、人名、地名、數字符號或舊式標點時，讀 `references/common-ocr-traps.md`。
- 處理單一長書、掃描 spread、旋轉／重複／缺頁、分段並行、Apple Vision 底稿、跨頁註腳或外部同版復原時，讀 `references/long-book-workflow.md`。
- 書內有凡例、編者校勘符號、原書不配對引號，或者長書準備定稿／交付時，讀 `references/release-gate.md`。

## 原文依據

- 以 PDF 圖像為最終依據；OCR、Apple/macOS Vision、Gemini、GJ.cool、OCR JSON 同外部研究都只係底稿或提示。
- 保留原有異體字、舊字形同舊式標點，例如 `粤/粵`、`淸/清`、`决/決`、`吿/告`、`塲/場`、`箚/劄/剳/札`。唔好靜靜正規化。
- 優先核對 `[]` 低信心字；冇括號嘅字亦可能係高信心錯字，要按語境同圖像檢查。
- 開始正文前先讀凡例／編輯說明，建立本書符號政策。`[]`、`［］`、`〈〉`、`□`、`（？）` 同註號可能係原書校勘內容，唔可以當 residual 一律刪；同一系列各冊亦要分開判斷。
- Annotated audit 唔接受 notation policy 豁免：已核實嘅來源符號可以原樣保留並記成 known annotated-stage issue，但呢一關唔可以聲稱 clean；移除 OCR confidence markup 後，只喺 final mode 先按 exact policy 接受來源 notation。
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
- 交付前收齊所有 writer 同 read-only auditor 嘅 final report，確認冇 active、未合併或可能再寫入嘅頁段 task。獨立覆核後再 patch，受影響 scan 要重新獨立覆核，或者建立記有 before/after、scan/render hash、獨立 verifier 同 evidence hash 嘅 sealed amendment ledger；之後重新 assemble、finalize 同跑完整 audit，舊 QA 結果即時失效。
- Release evidence 要分清目前生效嘅 `active`，同被排除嘅 `historical`／`superseded`；後者用 classification、reason 同必要嘅 `superseded_by` 交代。任何 provisional、未驗證或 failing artifact 都唔可以進入 active release closure。

## 標準流程

1. 找出 PDF/Markdown pair，確認 PDF scan page count，同時辨認書內 printed page number；先讀凡例、編輯說明同交付範圍。
2. 建立本書 notation policy，再建立或核對 page manifest：記錄 source/render、scan facts、重複／缺頁同 `marker_expected`。Boundary contract 同 writer／independent review evidence 另存 sealed sidecars，唔好靠 page manifest 一個 status 欄代替。
3. 保留檔首 `---` metadata；只有 PDF、既有 OCR 或可靠同版書目清楚提供時先補 metadata。
4. render 足夠解像度嘅頁圖或 crop；確認現有 render 數量同 PDF 頁數一致。
5. 改文前掃 `[]`、page marker、OCR notice、頁面家具、可疑拉丁／符號及已知高信心錯字；將命中分類成 OCR、來源內容或合法格式。
6. 按頁推進：先修低信心字，再修語境唔通嘅高信心字，之後重建段落、heading、list、table、表單同圖說。
7. 每次視覺確認一個無括號錯字後，搜尋全書同形／一字之差嘅人名、官名同固定語；逐處核圖，唔做全域替換。
8. 校對期間保留 `<!-- page_... -->` 定位。逐個 marker 判斷刪除後應係正文 join、真正 paragraph break、新 list item，定同一 list item 續文。產生 boundary manifest 時保留下一個非空行嘅原始 leading whitespace；唔可以先 `.strip()` 再判斷 `list-cont`／nested `list-new`，亦要保留經 PDF 確認嘅兩個獨立 lists 之間嘅真正 break。
9. 若頁底註腳插斷跨頁正文，先接完整正文，再將註腳放到所屬完整段落後；核對註號順序。
10. 收齊所有 review，凍結 segments 後用 `assemble_ocr_segments.py` 按 sealed ordered manifest 重播；separator 只可係空字串或全由 LF newline characters 組成，segment ranges、每段同組裝後嘅實際 markers、sealed page manifest 嘅 `marker_expected` 必須 exact，combined annotated 要 byte-for-byte 相同。
11. 建立並用 `validate_release_evidence.py` 驗證 pre-final `release-plan.json` 嘅 discovery inventory、classification、full/subset dependency hash、active closure、DAG 同必要 reconciliation evidence binding；任何 active TOC evidence type 都強制要 `reconciliation_scope`，plan 唔包含待生成 final／receipt／audit。Finalizer 必須帶 `--release-plan` 寫 schema v2 receipt；output／receipt 唔可 alias plan／classified artifact 或新命中 pre-final discovery globs。再跑 final audit，重驗 plan 同 receipt exact active closure，之後做獨立高信心 visual sweep；legacy v1 receipt 必須重新 finalize。要完整成品 inventory 就另建 post-final bundle plan，唔回餵 finalizer；任何後續 patch 都返回第 10 步。

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
- 長目錄先逐 scan 建立每項末端印刷參照 inventory，同 recovery ledger 記錄漏抽／誤併；再由獨立 reviewer 對全部參照做 100% 圖像覆核，唔只抽查差異。
- 目錄 item、分組 heading 同正文 TOC-eligible heading 要分開建 ledger／inventory；非 TOC-eligible heading 要逐項記 exclusion reason。正文同目錄係兩套來源文字，保留核圖確認嘅原參照、註號、題名同次序。
- 目錄跨卷、跨冊或跨交付範圍時，先定義 `reconciliation_scope` 同互斥 partitions；只喺同一 scope 內比較 count／mapping，scope 外每項亦要有明確 disposition，唔可以用全目錄同局部正文硬對數。
- Scope validator 只驗 partition arithmetic 同五項 sealed evidence binding；ledger row/status、reviewer coverage 同 challenge 語義仍要由專用 invariant／challenge 工具或人工逐項審核，否則唔可以聲稱對賬完成。
- 例行裝置省略可以獨立分類；涉及語義、人名、官職、日期、數字嘅高風險異文，以及每個頁碼差額或重排，都要經第二位 reviewer challenge。頁碼 mapping 另設細分類 disposition，唔可以改寫目錄印刷參照去迎合正文。
- PDF 明顯係表格就重建 Markdown table；直排表格按版面理解欄列，唔照 OCR 行序硬排。
- 表格總數、條號、人名數、A–J 等結構 invariants 要另行計算驗證，唔好只靠閱讀語意。

## 頁面家具

- 刪除重複頁眉、頁腳、書脊、頁碼、承印資訊、卷冊／叢書邊欄、OCR 工具標題同空白頁 notice。
- 只刪明顯非正文。似家具但同正文黐住，或者課名／書名可能屬正文時，先對 PDF。
- OCR 話空白嘅頁仍要 render 檢查淡字、印章、表格、圖、尾頁小字同另一篇題名。
- 全書識別通常保留只出現一次嘅扉頁、英文題名、版權／CIP、凡例、照片圖說、贊助題記同來源說明；呢啲唔係重複頁面家具。先界定係全掃描本定正文-only，唔好見到出版資訊就機械刪。

## Deterministic 驗證

技能附帶：

- `scripts/assemble_ocr_segments.py`：按 strict ordered manifest 驗 segment hashes／連續 scan ranges，並按 sealed page manifest 驗 marker coverage；重播 bytes 同現有 combined annotated 不一致就失敗。
- `scripts/validate_release_evidence.py`：按 canonical discovery globs 驗全部 artifact classification、SHA-256、typed dependency contracts、active closure、dependency／supersession DAG，同 optional reconciliation arithmetic／sealed evidence binding。
- `scripts/audit_ocr_markdown.py`：審核 annotated/final Markdown、marker inventory、顯式 boundary contract、OCR residue、soft break、tight-list continuation、table 欄數、成對標點、完整 EOF whitespace、來源 notation／exception、PDF／render provenance、sealed marker coverage 同 assembly receipt。Notation policy 只喺 final mode exact accept；annotated mode 唔可以用 policy 將來源符號命中豁免成 clean。Final receipt gate 只接受 schema v2，會重驗唯一 `release-plan` input、目前 plan 同 ordered active closure；legacy v1 只可診斷，唔會放行。
- `scripts/finalize_page_markers.py`：先 dry-run，逐個核對 marker 左右 context、boundary manifest 同預期移除數；`--write` 必須提供完整 `--boundary-manifest` 同另一個 `--output`。長書寫 `--receipt` 時亦必須提供已驗證嘅 `--release-plan`；腳本唔覆寫 annotated 底稿，並自動用 schema v2 唯一 `release-plan` role 封存 plan 嘅 active closure，唔再靠手寫重複 `--upstream` 清單。Output／receipt 唔可以 alias plan 或任何 classified artifact，亦唔可以變成 pre-final discovery 新命中。
- 第一次 boundary／issue review 保留預設 verbose 輸出；全數位置已核實後，長書重跑可加 `--summary-only` 壓縮終端輸出。呢個選項只隱藏逐項 context，唔改檢查、deterministic issue counts 或 exit code；任何非零 issue 要除去選項重跑定位。

最少再人工檢查：

```bash
rg -n "\[|\]|<!--|gjcool|此頁OCR|[A-Za-z]" TARGET.md || true
rg -n "承印|廣州大典|史部|政書|經濟學K|縱濟學|澄 濟" TARGET.md || true
rg -n "[[:blank:]]+$" TARGET.md || true
git status --short -- TARGET.md
git diff --check -- TARGET.md
```

`git diff --check -- TARGET.md` 對 untracked 新檔可能實際無檢查內容；遇到新檔要另跑 trailing-whitespace audit，或者用 `git diff --no-index --check /dev/null TARGET.md`，並留意 exit 1 可以只代表檔案有差異，判斷以有冇 whitespace error 輸出為準。

完成回覆要交代：PDF scan 頁數、printed-page continuity、目前 source PDF／render hash 是否同 manifest 相符、是否逐頁視覺核對、獨立 auditor 是否收齊、重複／缺頁、補 OCR／外部復原、本書 notation policy、來源例外、剩餘疑問、殘留 markers／brackets／OCR notices、合法拉丁詞同舊標點、段落／heading／list／table audit、receipt 狀態，以及所有驗證結果。
