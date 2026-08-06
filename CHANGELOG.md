# 更新摘要

## 0.2.4 — 2026-08-05

- 長目錄 reconciliation 新增明確 `reconciliation_scope` 同分冊／分卷 partitions；合訂本只交付其中一冊時，範圍外目錄項仍要逐項記錄 disposition。只要 active closure 出現 TOC reconciliation 專用 evidence type 就強制要 scope，唔可以靠省略欄位繞過。
- 新增 typed release evidence lifecycle：sidecar 明分 `active`、`historical`、`superseded`，active artifact 唔可以係 provisional／failing；dependency graph 要無環，並用 canonical subset hash 避免 ledger 互相封完整 hash 形成循環。
- 新增 `assemble_ocr_segments.py`，按 sealed page manifest、ordered segment SHA-256、連續 scan ranges 同 LF-only separator 重建 combined annotated；逐段同整體 markers 都要 exact 對上 `marker_expected`，dry-run 可 byte-for-byte replay 現有輸出。
- 新增 `validate_release_evidence.py`，驗證 `release-plan.json` 對所有 discovery 命中嘅 release artifacts 做 `classification: include`，或者 `exclude` 加理由；同時驗 dependency closure、singleton roles、supersession、typed scope evidence、partitions 同 canonical subset hashes。
- Finalizer receipt 升級至 schema v2，強制唯一 `release-plan` role並自動封存 plan-order active closure；output／receipt 唔可覆寫任何 classified evidence或新命中 pre-final globs。Final audit 會重驗目前 plan、全部 classified hashes同 exact closure；legacy v1 receipt只可作遷移診斷，唔可再封關。
- Annotated audit 會喺提供 page manifest 時即時核對 `marker_expected`，唔再等 final receipt 先發現 marker policy 錯配；notation policy 可逐類 inventory 全形方括號、六角括號、角括號、缺字方框同明示存疑符號。
- `audit_ocr_markdown.py` 新增 opt-in `--json-report`，輸出穩定嘅 machine-readable errors、warnings、accepted exceptions、issue counts 同 exit status，方便 release plan 封存及重播。
- 測試由 35 個增至 69 個，新增 assembly／release-plan／receipt v2、schema spoof、path alias、TOCTOU、legacy migration 同 adversarial closure regressions。

## 0.2.3 — 2026-08-05

- 長目錄先按每個 TOC scan 建立末端印刷頁碼 inventory，保存黐入題名／日期嘅 recovery，同時逐頁獨立核對全部引用；唔再用 parser 輸出或總項數代替目錄完整性。
- 將目錄條目、目錄分組同正文結構分成獨立 ledger：只比較有明確納入／排除理由嘅 TOC-eligible headings，並以正文分隔頁、running header 同首項互證分組起點。
- `source-verified-*` 新增獨立 challenge gate：除純註號等 routine apparatus 外，題名、日期、人名、官職、數字、機構等實質異文，以及所有頁碼差異／異序，都要由另一 reviewer 同時核對 TOC 同正文頁圖。
- 頁碼差異用 `page-exact`、`fixed-ocr-reference`、`source-printed-ref-differs-from-body-start` 等細分類，保留原目錄頁碼、正文實際起頁同鄰頁證據；唔再壓成普通文字異文或靜靜改正來源。
- Independent review 後嘅 late patch 要重新核對受影響頁，或者用包含 exact before/after、scan/render hash、獨立 verifier 同 evidence hash 嘅 amendment ledger 封存；所有 release decision sidecars 都要加入 assembly receipt upstream。
- `audit_ocr_markdown.py` 同 `finalize_page_markers.py` 新增 opt-in `--summary-only`，長書封關時可隱藏逐頁 context，同時保留 deterministic issue counts、exit code、write／receipt 摘要；預設 verbose 行為不變。
- 測試由 33 個增至 35 個，新增兩個 summary-only regression。

## 0.2.2 — 2026-08-03

- Boundary contract 新增反向保護：open list item 後嘅縮排續文或 nested item 若誤標 `join`，annotated audit 同 finalizer 都會拒絕，避免 `.strip()` 吞走 `list-cont`／`list-new` 語義後輸出 hard break 或黐爛子項；receipt 同時保留經視覺確認嘅 paragraph／structural list break，唔放寬無證據嘅 loose-list audit。
- Final audit 使用嘅 notation policy、source-exception ledger 同 page manifest 必須由 assembly receipt 封存；另傳未記錄 interpretation 檔會報 `assembly-audit-input-unsealed`。
- 新增 `--page-manifest`／`--source-pdf` provenance gate，逐次重算目前 PDF SHA-256、完整 `1..N` scan sequence 同每個 render hash；帶 receipt 時亦會對賬 sealed annotated markers，刻意略去頁必須以 sealed `marker_expected: false` 聲明。
- Review evidence 改成單向狀態：sidecar 重建唔可以將 `independent-checked` 降級，並要求 reviewer 同 writer 分開、覆核 range 同 evidence ledger／report hash 可追溯。
- 長目錄對賬要求逐項 disposition；TOC／正文數量相同唔代表文字完全一致，圈號省略、跨頁日期、題名／頁碼／排序真差異要各自按圖保留，唔可以留低未關閉嘅泛稱 `review`。
- 測試由 27 個增至 33 個，新增 list-cont／nested list 誤標、獨立 lists 結構邊界、unsealed final-audit input、PDF/render provenance 同 sealed marker coverage regression。

## 0.2.1 — 2026-08-03

- 將兩冊長書實作經驗整理成 release gate：先讀本書凡例、收齊所有 writer／auditor、凍結 segment hash，最後一個 patch 後重新 assemble、finalize 同完整 audit。
- 新增 `notation-policy.json` 支援；凡例證明 ASCII `[ ]` 係來源編者訂正時，final audit 唔再將佢無條件當 OCR confidence residual。
- 新增 stack-aware 全形 delimiter audit，同 duplicate-safe exact line／column／symbol `source-exceptions.json`；原書本身未閉或錯序標點要保存 scan page、整行文字同核圖說明，失效 exception 會報錯。
- Finalizer 新增 SHA-256 assembly receipt，同 ordered `--upstream` dependencies；final audit 可以偵測 late segment／manifest／annotated patch同 receipt 後 final 修改，並由目前 annotated + boundary contract byte-for-byte 重建 final，唔靠 mtime。
- 結構 audit 擴充至 tight-list item／continuation 空行、Markdown table 欄數、space-before-tab、missing final newline 同 EOF 多餘空行。
- 新增高信心 correction propagation、同名／相似平行篇章互校同 multi-pass OCR disagreement 工作法；全部只產生逐頁核圖候選，絕不自動同步或投票定稿。
- 收緊一次性 front matter／正文 apparatus 判斷：全書轉錄通常保留扉頁、英文題名、CIP／版權、凡例、圖說同來源資料，唔同重複頁面家具混為一談。
- 測試由 14 個增至 27 個，覆蓋 hash freshness、hard-link／case alias 防護、notation policy、精確來源例外、delimiter nesting、nested list、table 欄數、code fence 同完整 EOF whitespace。

## 0.2.0 — 2026-08-03

- 單一長篇 PDF 可以按連續頁段分派，但每個 writer 只寫獨立 segment；主 agent 獨佔最終 Markdown、assemble、頁段交界同 final audit。
- 新增 long-book workflow，分開記錄 PDF scan page 同 printed page，覆蓋 spread、旋轉、空白、完整／局部重複、缺失印刷頁同 page provenance。
- 明確規定原 PDF 真缺頁時嘅同版復原流程：先排除漏 OCR／重複／render 缺檔，再以可靠同版來源交叉核實，保存 evidence manifest，披露未能視覺核實嘅標點同行款。
- Apple/macOS Vision 可批量建立底稿，但直排閱讀次序、淡字、表格、註腳同跨頁內容仍須逐頁對 PDF。
- page marker 新增 `start`／`join`／`paragraph`／`structural`／`list-cont`／`list-new` 顯式 contract；finalizer 無完整 JSON manifest 或另一個 output path 會拒絕寫入，唔靠空行猜邊界，亦唔覆寫 annotated 底稿。
- final pass 新增結構 invariants：條號連續、人名或項目實數、A–J 類枚舉、表格列數及總數。
- 新增純 Python stdlib `audit_ocr_markdown.py` 同 `finalize_page_markers.py`，用 deterministic 檢查代替反覆即席腳本。
- 補充 untracked 新檔令 `git diff --check -- TARGET.md` 可能成為 no-op 嘅盲點，要求另做 trailing-whitespace audit。
- 更新 skill description、UI metadata、plugin metadata、README 同詳細 reference 導航。

## 0.1.2

今版將 `jyut-ocr` repo 入面近期香港中文 OCR 校對守則整理成可重用 Codex plugin skill，重點係令 agent 做長文校對、缺 OCR 補漏、表格整理同 final pass 時有一致嘅流程。

### 2026-06-16 同步摘要

本 repo 版本已同本機已安裝嘅 `jyut-ocr-proofread` 0.1.2 skill 對齊；主 `SKILL.md`、plugin metadata、詳細規則 reference 同常見陷阱 reference 都保持一致。呢次文件同步將最近新增同收緊嘅工作約束整理成以下幾類：

- Skill metadata 維持穩定 ID `jyut-ocr-proofread`，方便 Codex 以 skill 名稱載入；目標 repo 如有 `AGENTS.md` 或其他本地指示，仍然要先讀並優先跟本地指示。
- 多份互不重疊 Markdown/PDF pair 時，可以用並行 subagent 分工；主 agent 要先列出檔案、PDF 頁數或頁段、page marker 狀態、表格/尾頁/缺 OCR/跳頁風險，並避免多人同時改同一份 Markdown。
- 主 agent 要 review subagent 輸出、處理衝突同遺漏，並統一做完成前驗證；未逐頁視覺對照 PDF，唔可以話全文已完成校對。
- OCR 來源次序收緊：缺 OCR、補漏 OCR 或 page marker 跳頁時，即刻用 PDF render 圖像做視覺轉寫，唔為任何 OCR 服務停低。
- GJ.cool 只作背景或極短時間輔助：有 token、API 正常、未爆 quota，而且 5 秒 timeout 內即時返到，先用作底稿再核 PDF；timeout、quota、auth 失敗、服務不可用就直接繼續視覺識別。
- 如果 GJ.cool API 回覆 `wait for ... seconds`，本輪 OCR/校對任務餘下部分即刻停止自發重試 GJ.cool；之後即使用其他 token 或 IP 可能重置用量，都唔主動再試，除非用戶另開要求測 API/token。
- Apple/macOS OCR、Gemini、Tesseract、OCR JSON 同外部資料都只係提示；PDF 圖像仍係最終依據，外部研究唔可以覆蓋 PDF 原文。
- 深入改文前要查 page continuity：page marker 是否順序、有冇覆蓋最後非空白頁；無 marker 就用 render 圖或 contact sheet 建立 section-to-page map。
- 缺 OCR、跳頁或 residual scan 乾淨但內容唔連續時，要先當成內容缺漏處理，找本地底稿或直接從 PDF 圖像補回，唔可以只做格式 pass 就當完成。
- Markdown 結構規則更具體：page comments 只作臨時定位，完成前要刪；普通正文應一段一行；heading、bullet list、table、跨頁續文同 list spacing 要按 PDF 版面整理。
- 表格、票據、圖、淡字空白頁、尾頁印章等都要 render 放大檢查；直排表格要按版面理解欄列，必要時轉成 Markdown table，唔可以照 OCR 行序硬排。
- 常見高信心錯字 reference 擴充到官職、人名、地名、套語、數字符號、條文清單、heading、舊式標點、尾頁視覺陷阱同合法拉丁詞，供長文同 final pass 使用。
- 完成前驗證固定化：掃低信心括號、page comments、OCR notice、拉丁殘留、常見頁面家具同高信心錯字；刪 page comments 後檢查 soft wrap；最後跑 `git diff --check`。
- 最終回覆要交代 PDF 頁數、page continuity、是否逐頁視覺對照、有冇補 OCR、有冇疑難字、有冇殘留 brackets/page comments/OCR notices、舊標點/合法拉丁詞處理、一段一行檢查，以及 `git diff --check` 結果。

Bundled references 亦保留長文、表格、尾頁、章程、速記錄、講義合訂同常見高信心錯字陷阱，供 final pass 或高風險頁段再讀。
