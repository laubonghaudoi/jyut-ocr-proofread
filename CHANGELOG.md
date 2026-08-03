# 更新摘要

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
