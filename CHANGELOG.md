# 更新摘要

## 0.1.2

今版將 `jyut-ocr` repo 入面近期香港中文 OCR 校對守則整理成可重用 Codex plugin skill，重點係令 agent 做長文校對、缺 OCR 補漏、表格整理同 final pass 時有一致嘅流程。

- Skill metadata 用穩定 ID `jyut-ocr-proofread`，方便 Codex 以 skill 名稱載入。
- 目標 repo 如有 `AGENTS.md` 或其他本地指示，必須先讀並優先跟本地指示；本地指示高於 plugin 內通用規則。
- 兩份或以上互不重疊 Markdown/PDF pair 時，預設可以用並行 subagent 分工；主 agent 仍要 review 改動、處理遺漏，並統一做最後驗證。
- 同名 PDF 仍係最終依據。OCR、GJ.cool、Apple/macOS OCR、Gemini、Tesseract、OCR JSON 同外部資料都只係提示，唔可以覆蓋 PDF 圖像原文。
- 新 OCR、補漏 OCR 或 page marker 跳頁時，立即由 PDF render 圖像視覺轉寫；唔會為等 GJ.cool 而暫停。
- GJ.cool 只作背景或極短時間輔助：有 token、API 正常、未爆 quota，而且 5 秒 timeout 內即時返到，先用作底稿；timeout、quota、auth 失敗、服務不可用就直接繼續視覺識別。
- 如果 GJ.cool API 回覆 `wait for ... seconds`，本輪 OCR/校對任務餘下部分即刻停止自發重試 GJ.cool；之後全部改用模型視覺識別，GJ.cool 之後補返結果都只可當校對提示。
- 深入改文前要查 page continuity：page marker 是否順序、有冇覆蓋最後非空白頁；無 marker 就用 render 圖或 contact sheet 建立 section-to-page map。
- 缺 OCR、跳頁或 residual scan 乾淨但內容唔連續時，要先當成內容缺漏處理，找本地底稿或直接從 PDF 圖像補回，唔可以只做格式 pass 就當完成。
- Markdown 結構規則更具體：page comments 只作臨時定位，完成前要刪；普通正文應一段一行；heading、bullet list、table、跨頁續文同 list spacing 要按 PDF 版面整理。
- 表格、票據、圖、淡字空白頁、尾頁印章等都要 render 放大檢查；直排表格要按版面理解欄列，必要時轉成 Markdown table。
- 完成前驗證固定化：掃低信心括號、page comments、OCR notice、拉丁殘留、常見頁面家具；刪 page comments 後檢查 soft wrap；最後跑 `git diff --check`。
- 最終回覆要交代 PDF 頁數、page continuity、是否逐頁視覺對照、有冇補 OCR、有冇疑難字、有冇殘留 brackets/page comments/OCR notices，以及 `git diff --check` 結果。

Bundled references 亦保留長文、表格、尾頁、章程、速記錄、講義合訂同常見高信心錯字陷阱，供 final pass 或高風險頁段再讀。
