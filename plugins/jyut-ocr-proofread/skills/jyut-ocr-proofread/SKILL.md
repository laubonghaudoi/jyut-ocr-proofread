---
name: jyut-ocr-proofread
description: 逐頁識別、校對同清理歷史中文 PDF／OCR Markdown；支援直排、長書分段、來源校勘符號、題名註、頁面家具、低信心與高信心錯字、缺頁／重複掃描、跨頁註腳、heading/list/table 重建，以及按風險啟用嘅 deterministic release audit。Use when asked to OCR, transcribe, 校對, proofread, clean historical Chinese PDFs or Markdown, including image-only books and jyut-ocr repos.
---

# Jyut OCR 校對

用呢個 skill 將歷史中文 PDF 忠實轉錄成 Markdown，或者按同名 PDF 校對既有 OCR。目標係保存原文，而唔係現代化、改寫或潤飾。

## 先讀邊份規則

- 先讀目標 repo 嘅 `AGENTS.md` 或其他本地指示；本地規則優先。
- 做全文、final pass、長報告、章程、表格、票據、尾頁或速記錄時，讀 `references/jyut-ocr-detailed-rules.md`。
- 遇到反覆近形錯字、官職、人名、地名、數字符號或舊式標點時，讀 `references/common-ocr-traps.md`。
- 處理單一長書、掃描 spread、旋轉／重複／缺頁、分段並行、Apple Vision 底稿、跨頁註腳或外部同版復原時，讀 `references/long-book-workflow.md`。
- 用 Codex／Claude Code subagent 分段寫稿或做獨立 visual audit 時，讀 `references/subagent-orchestration.md`；Apple Vision raw OCR 另用本機 bounded process pool，唔用一頁一個 LLM agent 包裝。
- 書內有凡例、編者校勘符號或原書不配對引號時，讀 `references/release-gate.md` 嘅相應 feature；只有已揀 `release` profile 先跟入面嘅 plan／receipt 封關部分。
- 遇到題名星號／來源題注、獨立覆核後再修改、跨冊證據重用，或者要判斷上游改動令邊批 release evidence 失效時，讀 `references/review-evidence.md`。

## 原文依據

- 以 PDF 圖像為最終依據；OCR、Apple/macOS Vision、Gemini、GJ.cool、OCR JSON 同外部研究都只係底稿或提示。
- 保留原有異體字、舊字形同舊式標點，例如 `粤/粵`、`淸/清`、`决/決`、`吿/告`、`塲/場`、`箚/劄/剳/札`。唔好靜靜正規化。
- 優先核對 `[]` 低信心字；冇括號嘅字亦可能係高信心錯字，要按語境同圖像檢查。
- 開始正文前先讀凡例／編輯說明，建立本書符號政策。`[]`、`［］`、`〈〉`、`□`、`（？）` 同註號可能係原書校勘內容，唔可以當 residual 一律刪；同一系列各冊亦要分開判斷。
- Annotated audit 唔接受 notation policy 豁免：已核實嘅來源符號可以原樣保留並記成 known annotated-stage issue，但呢一關唔可以聲稱 clean；移除 OCR confidence markup 後，只喺 final mode 先按 exact policy 接受來源 notation。
- PDF 模糊而底稿合理時，保留最有根據讀法或報告 uncertainty；唔好憑空補字。
- 未逐頁視覺對照，就唔可以話「已逐頁同 PDF 對照完成」。
- 聲稱逐頁視覺完成時，每個 in-scope scan 都要以 individual original／full-resolution render 作文字依據，並同目前 page manifest 嘅 render hash 對返；contact sheet、thumbnail、OCR output 只可導航或產生 risk queue，唔算逐頁直接覆核。

## OCR 工具次序

1. 立即 render PDF 並由圖像開始視覺識別，唔為任何服務停低。
2. Apple/macOS Vision 可以批量產生有 page provenance 嘅底稿；直排次序、表格、淡字、註腳同跨頁段落仍要按圖重建。
3. 只有已有 token、API 正常而且 5 秒內即時回應時，先用 GJ.cool 作輔助底稿。timeout、quota、auth、服務錯誤或 `wait for ... seconds` 後，本輪唔再自發重試。
4. Tesseract/Tessdata 係硬性禁用：唔安裝、唔調用、唔等待、唔讀取現成輸出、唔採納結果。

## 安全並行

- LLM subagent 只用嚟分段轉錄、版面重建同獨立核圖；Apple Vision OCR 本身無 LLM model，由主 agent 啟動最多八個本機 process。兩種併發唔好互相套娃。
- 同時運行嘅 LLM child 數量不得超過平台實際可用 slots，亦最多八個；agent tasks 多過 slots 時分 wave 執行。Worker／auditor 預設沿用主 session 嘅有效模型策略，唔喺通用技能鎖死平台 model ID。
- 多份獨立 Markdown/PDF pair 可以一檔一 agent 並行；每個 writer 嘅檔案範圍必須互斥。
- 單一長書亦可以按連續頁段分工，但每個 agent 只寫獨立暫存 segment，例如 `segments/p001-080.md`；只由主 agent 組裝最終 Markdown。
- 開工前列明 PDF 頁數、頁段、marker 範圍、表格／尾頁／缺頁風險同輸出檔。唔好畀多人同時改同一份最終檔。
- 主 agent 要 review 每段、驗證 segment 交界、處理衝突，並統一跑 final audit。subagent 回報唔等於全文完成。
- 交付前收齊所有 writer 同 read-only auditor 嘅 final report，確認冇 active、未合併或可能再寫入嘅頁段 task。獨立覆核後再 patch，受影響 scan 要重新獨立覆核；啟用 `release` 時，亦可以建立 canonical sealed amendment ledger，再用 `validate_review_evidence.py` 驗 status、reviewer、coverage 同目前 hashes。
- Writer 封 candidate 後要停止寫入；independent verifier 只核 formal seals，唔採納未封存嘅 live bytes。Verifier open 0 先可轉 final；如果仍要 patch，重新指定唯一 writer、產生新 candidate，再由另一位 reviewer 重核，唔可以一邊覆核一邊繼續改同一份 artifact。
- `release` evidence 要分清目前生效嘅 `active`，同被排除嘅 `historical`／`superseded`；後者用 classification、reason 同必要嘅 `superseded_by` 交代。任何 provisional、未驗證或 failing artifact 都唔可以進入 active release closure。

## 工作 profile

Profile 只控制證據封存重量，唔會降低逐頁視覺校對標準。先揀最低足夠 profile，再按實際素材疊加專項 gate；唔好按書名、固定頁數或上一冊做法自動升級。

- `basic`（預設）：單一文件、單一 writer，無複雜跨頁結構或可重播交付要求。完成 scan count、逐頁圖文核對、Markdown 整理、residual search 同格式檢查；唔強制建立 release plan、tool snapshot 或 receipt。
- `structured`：有 page markers、跨頁 join、缺頁／重複掃描、分段或多 agent。建立 project-local page／boundary manifest、segment assembly、review 記錄同實際需要嘅專項 auditor；多 writer、重核 late patch 或外部復原本身唔會自動升級。
- `release`：只有用戶／目標 repo 明確要求可重播 evidence closure、跨 release 證據重用，或者決定要將現有多 writer／獨立覆核／外部復原證據封成 durable closure 時先啟用 sealed review ledger、release plan、tool snapshot、receipt 同 final audit。

來源校勘符號、星號題名註、長目錄、表格同外部復原係互相獨立嘅 feature gate；只喺來源實際出現時啟用，亦唔單憑其中一項自動揀 `release`。唔適用嘅 artifact 記做 `not-applicable`，唔為湊齊流程建立空 sidecar。

## 核心流程

1. 找出 PDF/Markdown pair，確認 PDF scan page count，同時辨認書內 printed page number；先讀凡例、編輯說明同交付範圍。
2. 保留檔首 `---` metadata；只有 PDF、既有 OCR 或可靠同版書目清楚提供時先補 metadata。
3. render 足夠解像度嘅頁圖或 crop；確認現有 render 數量同 PDF 頁數一致。
4. 改文前掃 `[]`、page marker、OCR notice、頁面家具、可疑拉丁／符號及 project correction ledger；將命中分類成 OCR、來源內容或合法格式。
5. 按頁推進：先修低信心字，再修語境唔通嘅高信心字，之後重建段落、heading、list、table、表單同圖說。
6. 每次視覺確認一個無括號錯字後，搜尋全書同形／一字之差嘅人名、官名同固定語；逐處核圖，唔做全域替換。
7. 校對期間保留 `<!-- page_... -->` 定位。若啟用 `structured`，逐個 marker 判斷刪除後應係正文 join、真正 paragraph break、新 list item，定同一 list item 續文；判斷時保留原始 leading whitespace。
8. 若頁底註腳插斷跨頁正文，先接完整正文，再將註腳放到所屬完整段落後；核對註號順序。移動任何註腳／題注後重判相關 boundary。
9. 完成逐頁視覺對照同所有可能改文嘅獨立 high-confidence sweep 後，先跑適用嘅 deterministic audits、residual search、結構檢查同 whitespace check；最後一次改文會令所有依賴舊 bytes 嘅檢查失效，要重跑受影響 gate。Post-final auditor 只可 read-only；一發現問題就返回 late-amendment／rebuild 流程，唔喺已封 final 上補改。

`structured` 文件先建立 page manifest，分開記 source/render、scan facts、重複／缺頁同 `marker_expected`；boundary、notation、TOC 同 review evidence 各自用專責 contract，唔靠一個 status 欄包辦。`release` 文件先收齊 review，以 `validate_review_evidence.py` 驗 late amendments、以 `plan_release_rebuild.py` 找 stale descendants、用 `assemble_ocr_segments.py` 重播，再驗 release plan、finalize receipt 同 final audit。完整次序見相關 references。

若來源明確用星號題名註，先由凡例／頁圖確認該 apparatus，先用 `audit_title_notes.py`；普通 Markdown `*` list／emphasis 同題注語法可能相撞，auditor 只係結構 gate，ownership 仍要逐 heading 核圖。所有 note→running-body 跨頁 transition 預設 actionable；只可由 project-local、綁定目前 target／boundary hash 同逐項視覺證據嘅 transition review 放行，唔可以靠來源句式、語言或詞頭猜。

## 完成狀態同 canonical output

- 分開報告四條軸：OCR／scan coverage、逐頁視覺校對、deterministic release closure、repo／publish delivery。前三者 open 0 唔自動代表檔案已 tracked、committed 或發布；未追蹤／ignored／repo 外輸出要如實交代，亦唔自動 stage 或 commit。
- `release` profile 嘅 canonical final path 由 schema v2 receipt `output.path` 決定，唔預設一定有 `release/final.md`。直接 receipt-bound／final-audited public Markdown 可以係唯一 canonical output。
- 如果同時宣稱 internal 同 public 兩份 final，兩份 target bytes／SHA 必須一致，並各有 current receipt＋final audit；或者由 post-final bundle 明確封存 canonical target、secondary copy 同 parity。Output path 唔同時 receipt hash 可以唔同，唔可以誤當內容不一致。
- 回答既有 release「而家仲係咪完成」時用 read-only current-seal profile：解析 receipt target、重算 target／receipt／plan／source hashes、驗 release plan、要求 rebuild planner direct／transitive stale 為零，並 check-only 重跑 final audit。舊 `pass` JSON 或 pre-final root summary 本身唔證明目前 bytes 仍然有效。

## 經驗泛化同技能更新門檻

- 每份材料新見到嘅字形、題名、頁碼、人名、地名、來源句式、版式、頁面家具同編輯符號，先放入目標 project 嘅 correction ledger、notation／inventory profile、boundary contract、source exception 或 review sidecar，唔直接寫入核心 skill。
- 只喺以下情況更新核心 skill／bundled scripts：同一失效類型喺至少兩份互不相關嘅出版物或 OCR pipeline 重現；單一案例揭示與書種無關嘅資料遺失、fail-open、hash／provenance 或安全問題；或者公開 contract 同工具實際行為矛盾。
- 核心規則同程式唔可以 hard-code 書名、scan number、人名、地名、單一版次符號或特定來源措辭。新 acceptance 必須由結構、exact locator、目前內容 hash 同視覺 evidence 證明。
- Regression tests 用最小 synthetic fixtures；實書只作外部 forward test。README／CHANGELOG 描述失效類型，唔保存實書名、實頁碼或大段原文。
- 每本書完成後先將新發現記做 candidate lesson。若現有 project contract 已經可以表達，而且未達以上門檻，預設結論係毋須更新技能。

## `release` profile 完整流程

以下 11 步只喺 `release` profile 使用；`basic`／`structured` 只跑上面核心流程同適用 feature gates。

1. 找出 PDF/Markdown pair，確認 PDF scan page count，同時辨認書內 printed page number；先讀凡例、編輯說明同交付範圍。
2. 建立或核對 page manifest：記錄 source/render、scan facts、重複／缺頁同 `marker_expected`。若來源有特殊 notation，另建 project-local notation／inventory profile；boundary、writer 同 independent review evidence 亦各存 sealed sidecar，唔好靠 page manifest 一個 status 欄代替。內容位置凍結後先產生適用 inventory；之後再移字／題注就重建。衍生 audit／inventory 要封 generator toolchain hash 同 invocation profile；要偵測 skill/tool 升級，release plan 亦要收錄 project-local tool snapshot，並由報告 typed-depend on 該 snapshot。
3. 保留檔首 `---` metadata；只有 PDF、既有 OCR 或可靠同版書目清楚提供時先補 metadata。
4. render 足夠解像度嘅頁圖或 crop；確認現有 render 數量同 PDF 頁數一致。
5. 改文前掃 `[]`、page marker、OCR notice、頁面家具、可疑拉丁／符號及已知高信心錯字；將命中分類成 OCR、來源內容或合法格式。
6. 按頁推進：先修低信心字，再修語境唔通嘅高信心字，之後重建段落、heading、list、table、表單同圖說。
7. 每次視覺確認一個無括號錯字後，搜尋全書同形／一字之差嘅人名、官名同固定語；逐處核圖，唔做全域替換。
8. 校對期間保留 `<!-- page_... -->` 定位。逐個 marker 判斷刪除後應係正文 join、真正 paragraph break、新 list item，定同一 list item 續文。產生 boundary manifest 時保留下一個非空行嘅原始 leading whitespace；唔可以先 `.strip()` 再判斷 `list-cont`／nested `list-new`，亦要保留經 PDF 確認嘅兩個獨立 lists 之間嘅真正 break。
9. 若頁底註腳插斷跨頁正文，先接完整正文，再將註腳放到所屬完整段落後；核對註號順序。若來源有星號題名註，題注要跟返所屬 heading，唔套用普通頁底註腳位置；用 `audit_title_notes.py` 核對 marker balance 同跨頁 hazard，再由人逐 heading 判 ownership；移動題注後重判相關 boundary。
10. 收齊所有 review；canonical amendment ledgers 要先通過 `validate_review_evidence.py`。由目前 segment bytes、boundary／page facts、heading／list／table 結構同 note ownership 等全部 normative channels 機械導出 final changed-scan union，分開記 text、facts-only、structure-only、restored-to-baseline、delta-review 同 neighbor/context sets，唔用手填總數或相加 counts 代替 set arithmetic。任何 late patch 先用舊 release plan 跑 `plan_release_rebuild.py` 找 direct／transitive stale descendants，再依 dependency-first order 重建。凍結 segments 後用 `assemble_ocr_segments.py` 按 sealed ordered manifest 重播；separator 只可係空字串或全由 LF newline characters 組成，segment ranges、每段同組裝後嘅實際 markers、sealed page manifest 嘅 `marker_expected` 必須 exact，combined annotated 要 byte-for-byte 相同。
11. 建立並用 `validate_release_evidence.py` 驗證 pre-final `release-plan.json` 嘅 discovery inventory、classification、full/subset dependency hash、active closure、DAG 同必要 reconciliation evidence binding；任何 active TOC evidence type 都強制要 `reconciliation_scope`，plan 唔包含待生成 final／receipt／audit。Finalizer 必須帶 `--release-plan` 寫 schema v2 receipt；output／receipt 唔可 alias plan／classified artifact 或新命中 pre-final discovery globs。再跑 final audit，重驗 plan 同 receipt exact active closure；legacy v1 receipt 必須重新 finalize。Post-final auditor 只可 read-only，發現任何內容或結構問題就返回第 10 步；要完整成品 inventory 就另建 post-final bundle plan，唔回餵 finalizer。

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

- `scripts/run_apple_vision_ocr.py`＋`scripts/apple_vision_ocr.swift`（macOS OCR 底稿）：用 bounded process pool 逐頁產生可續跑、可驗 hash、有 scan provenance 嘅 Apple Vision JSON／text；有效 worker 上限八個，任何輸出仍只係 draft，亦絕不 fallback 去 Tesseract／Tessdata。
- `scripts/assemble_ocr_segments.py`（`structured`／`release`）：按 strict ordered manifest 驗 segment hashes／連續 scan ranges，並按 sealed page manifest 驗 marker coverage；重播 bytes 同現有 combined annotated 不一致就失敗。
- `scripts/audit_title_notes.py`（星號題名註 feature）：按主 item 核對 heading 尾端 `*`／`**` 同題注 marker，列 missing／orphan 同 note-shaped 跨頁 hazard。任何 note→running-body transition 預設 fail closed；只有 strict `--transition-reviews` exact match 目前 target、boundary、前後文字、reviewer 同 visual evidence 先接受。報告唔自動證明題注 ownership；普通 Markdown star syntax 必須由人分辨。
- `scripts/build_notation_inventory.py`（來源 notation feature）：逐頁 inventory 通用 residual symbols；`--inventory-profile` 可以用 project-local literal tokens 擴展，`--include-starred-title-notes` 只喺來源確有該 apparatus 時啟用。工具避開 metadata、code、單行 HTML comments 同 Markdown links，並封 target／authority／profile、toolchain hash 同 invocation profile；佢唔取代凡例判讀。
- `scripts/validate_review_evidence.py`（`release`）：驗 canonical late-amendment ledger 嘅 exact schema、安全相對路徑、current content／render／evidence hashes、非空而相異嘅 before／after rows、writer／verifier 獨立性、changed＋neighbor scan coverage 同零 unresolved；before／after 是否對應 baseline／目前字串仍由 reviewer 同 evidence 證明。
- `scripts/plan_release_rebuild.py`（`release`）：重算 active artifact hashes，將 direct stale 沿 reverse dependencies 傳播，輸出 dependency-first rebuild order；clean／stale／invalid 分別用不同 exit status。缺失 active path 若唔可能由 discovery globs 命中就係 invalid，唔係可重建 stale；JSON report 唔准 alias plan、任何 declared artifact 或工具。佢只理解 plan graph，未列入 plan 嘅 generator 升級唔會自動變 stale。
- `scripts/validate_release_evidence.py`：按 canonical discovery globs 驗全部 artifact classification、SHA-256、typed dependency contracts、active closure、dependency／supersession DAG，同 optional reconciliation arithmetic／sealed evidence binding。
- `scripts/audit_ocr_markdown.py`：審核 annotated/final Markdown、marker inventory、顯式 boundary contract、OCR residue、soft break、tight-list continuation、table 欄數、成對標點、完整 EOF whitespace、來源 notation／exception、PDF／render provenance、marker coverage 同 assembly proof。`structured` final 可用 `--annotated-source` 加 boundary manifest 即場 exact 重建；`release` receipt gate 則重驗 schema v2 plan 同 ordered active closure。Notation policy 只喺 final mode exact accept；legacy v1 receipt 只可診斷，唔會放行。
- `scripts/finalize_page_markers.py`：先 dry-run，逐個核對 marker 左右 context、boundary manifest 同預期移除數；`--write` 必須提供完整 `--boundary-manifest` 同另一個 `--output`。長書寫 `--receipt` 時亦必須提供已驗證嘅 `--release-plan`；腳本唔覆寫 annotated 底稿，並自動用 schema v2 唯一 `release-plan` role 封存 plan 嘅 active closure，唔再靠手寫重複 `--upstream` 清單。Output／receipt 唔可以 alias plan 或任何 classified artifact，亦唔可以變成 pre-final discovery 新命中。
- 第一次 boundary／issue review 保留預設 verbose 輸出；全數位置已核實後，長書重跑可加 `--summary-only` 壓縮終端輸出。呢個選項只隱藏逐項 context，唔改檢查、deterministic issue counts 或 exit code；任何非零 issue 要除去選項重跑定位。

最少再人工檢查：

```bash
rg -n "\[|\]|<!--|gjcool|此頁OCR|[A-Za-z]" TARGET.md || true
rg -n "[[:blank:]]+$" TARGET.md || true
test ! -s project-suspects.txt || rg -n -f project-suspects.txt TARGET.md || true
git status --short -- TARGET.md
git diff --check -- TARGET.md
```

`project-suspects.txt` 由目前文件嘅重複頁面家具、correction ledger、OCR disagreement 同已核實錯形產生；書名、專名同單一材料先入呢份 project-local 清單，唔回寫核心 skill。

`git diff --check -- TARGET.md` 對 untracked 新檔可能實際無檢查內容；遇到新檔要另跑 trailing-whitespace audit，或者用 `git diff --no-index --check /dev/null TARGET.md`，並留意 exit 1 可以只代表檔案有差異，判斷以有冇 whitespace error 輸出為準。

完成回覆一定交代：所用 profile、PDF scan 頁數、printed-page continuity、是否逐頁用 individual original／full-resolution renders 視覺核對、重複／缺頁、補 OCR／外部復原、剩餘疑問、殘留 markers／brackets／OCR notices、合法拉丁詞同舊標點、段落／heading／list／table audit，以及所有實際執行嘅驗證結果。只有啟用相應 feature／profile 時，先再交代 source/render manifest hashes、text／facts／structure changed-set arithmetic、notation inventory、題名註、獨立 auditor／late amendment、release plan、canonical receipt target、final audit、internal/public parity 同 tracked／untracked／ignored／repo 外交付狀態；唔適用就直接寫 `not-applicable`。
