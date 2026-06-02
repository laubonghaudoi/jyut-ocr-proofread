---
name: jyut-ocr-proofread
description: 校對同清理 jyut-ocr 類 repo 入面由歷史中文 PDF OCR 出嚟嘅 Markdown；按同名 PDF 核對，刪頁面家具，修低信心括號同高信心錯字，重建 table/表格、forms/票據、figures/圖，完成 Markdown transcription/轉寫。Use when asked to 校對, proofread, clean OCR, page furniture, low-confidence brackets, historical Chinese OCR.
---

# Jyut OCR 校對

用呢個 skill 處理 `jyut-ocr` 或相近 repo 入面，由歷史中文 PDF OCR 出嚟嘅 Markdown 校對工作。

如果目標 repo 有本地指示，例如 `AGENTS.md`，先讀同優先跟本地指示；本地指示高於呢個可重用 skill。做全文校對、長報告書、合訂講義、表格、尾頁、或者 final pass 之前，要再讀 `references/jyut-ocr-detailed-rules.md`。

## 核心約定

- 同名 PDF 係最終依據。處理 `NAME.md` 時，優先對 `NAME.pdf`。
- OCR 可以當可靠底稿，但唔係最終依據；所有疑難字、補漏、刪改都要回到 PDF 圖像判斷。
- 保留 PDF 原有字形、異體字、舊字形同舊式標點。唔好因現代慣用寫法而改 `粤/粵`、`淸/清`、`决/決`、`吿/告`、`塲/場`、`箚/劄/剳/札` 等字。
- `[]` 入面嘅字代表 OCR 信心低，要優先查 PDF；冇括號嘅字都可能係高信心錯字，要按語境查。
- 新 OCR 或補漏 OCR 時，立即由 PDF render 圖像做視覺轉寫，唔好等 GJ.cool 先開始。GJ.cool 古籍 OCR 只係背景並行或極短時間輔助：有 token、API 正常、未爆 quota、幾秒內即時返到，先用佢做底稿；否則即刻繼續視覺識別。GJ.cool 之後補返結果，最多當校對提示。
- Apple/macOS OCR、Gemini、Tesseract、OCR JSON 等都只係提示；最後仍以 PDF 圖像為準。
- 外部研究可以幫手判斷人名、官職、地名、年代、典故，但唔可以覆蓋 PDF 原文。
- 校對目標係忠實轉錄 PDF 原文，唔係現代化整理文本。
- 未逐頁視覺對照 PDF，就唔可以話「已逐頁同 PDF 對照完成」。
- 如果 Markdown/OCR 底稿漏頁、跳頁、或者 page marker 跳到遠處，先當成缺 OCR/缺轉寫處理，補回內容後先做校對。

## 標準流程

1. 找出 Markdown/PDF pair，確認 PDF 頁數。
2. 檢查 Markdown 開頭有冇 `---` metadata block；有就保留喺最前面。只有 PDF 或既有 OCR 清楚提供 metadata 時先補入。
3. 深入改文之前先查頁面連續性：
   - 有 `<!-- page_... -->` marker 就確認是否順序、是否到最後一個非空白頁；
   - 無 marker 就用 render 圖/contact sheet 建立粗略 section-to-page map；
   - 特別檢查標題轉換、表格、圖、空白頁提示、卷冊/篇章轉換。
4. 校對期間可以暫時保留 page comments 定位；最終輸出前要刪除。
5. render PDF 做圖像對照。若 `tmp/pdfs/...` 圖像新鮮又完整，可以沿用；否則用 `magick`、`pdftoppm` 或本機可用工具重新 render。
6. 需要新 OCR/補漏時：
   - 先直接由 PDF 圖像逐欄視覺轉寫；
   - 若已有 `GJCOOL_ACCESS_TOKEN` 或 `GJCOOL_AUTH`，可以同時或極短時間試 GJ.cool；
   - GJ.cool 幾秒內有結果就用作底稿再核 PDF；若 timeout、無回應、叫 `wait for ... seconds`、quota 爆、auth 失敗、服務不可用，就停止等候，繼續視覺轉寫；
   - 其他 OCR 工具只當提示。
7. 改文前先做機械掃描：

```bash
rg -n "\[|\]|<!-- page_|gjcool OCR|^>此頁OCR無有效文字|[A-Za-z]" TARGET.md
```

8. 按頁或按小段落推進：
   - 先修低信心括號；
   - 再修語境唔通嘅高信心錯字；
   - 人名、官職、地名、公司、日期、案情因果唔通時，先查 PDF 字形，再用可靠資料輔助；
   - 只刪明顯頁面家具；如果同正文黐住，先對 PDF；
   - 重整語義段落、heading、bullet list、table。
9. 內容完成後刪除所有 page comments，再檢查 Markdown 結構。普通正文應該係一段一行；唔好留下相鄰普通文字行靠 soft wrap 分段。
10. 跑 `git diff --check -- TARGET.md`。

## 缺 OCR 或跳頁

- residual scan 乾淨唔等於內容完整。見到章節開頭、page marker、PDF 圖像同 Markdown 明顯跳頁，要停低查缺頁範圍。
- 先找其他本地底稿：舊 git 版本、`tmp/pdfs/.../cleaned-draft*.md`、OCR JSON、raw OCR draft、repo OCR scripts。
- GJ.cool 可用時只短促或背景試；唔好為佢停低視覺轉寫。
- Apple Vision/LiveText 對直排 crop 有時有用，但會漏淡字、誤判空白、拆亂表格；只當提示。
- 唔好貼網上相近文本當原文依據。外部資料只可幫手識別疑難字詞，最後仍按 PDF。
- 有缺頁或只係盡力轉寫嘅範圍，要清楚講明，唔好當全文已校對。

## Markdown 結構

- 全書題名用 `#`。
- 主要議案、呈文、報告、課目、大篇用 `##`。
- 其下 `呈文`、`議草`、`批答`、`請議書`、`箚覆`、章節等用 `###`；講義再下層可以用 `####`、`#####`。
- 合訂講義常見 `法學通論`、`政治學`、`憲法講義`、`行政法學`、`經濟學` 等課目，可作 `##`。
- 目錄、條文、問題清單、辦法清單、證據清單等可以用 bullet list；保留原文項號如 `一`、`二`、`第一條`、`甲一`，唔好現代化重排。
- 連續 bullet list 入面項目之間唔加空行；跨頁續文若屬同一 item，要縮入該 item 底下。
- `至...今照修正...` 呢類承接句通常係普通段落；除非 PDF 明顯作題名，唔好升做 heading。
- 課目或篇章突然轉換時，按 PDF 頁序忠實保留，唔好自行補橋接文。

## 頁面家具

刪除重複或非正文內容，例如：

- `gjcool OCR 結果`、空白頁提示、`<!-- page_... -->`；
- 頁眉、頁腳、書脊、頁碼、卷冊標籤、承印資訊、藏書/叢書邊欄；
- `廣東1議局...`、`廣東諮護局...`、`議局籌辦處第...` 等變體；
- `廣州大典`、`史部政書類`、`經濟學K`、`縱濟學`、`澄 濟` 等邊欄/頁眉殘片。

只刪明顯家具；似家具但同正文連住時，先睇 PDF。

## 表格、票據、圖、空白頁

- PDF 明顯係表格就轉 Markdown table。
- 直排表格要按版面理解欄列，必要時想像逆時針旋轉九十度；唔好照 OCR 行序硬排。
- 表單、票據、圖要轉錄有意義文字；只有 table 更忠實時先做 table。
- OCR 話空白嘅頁可能有淡字、尾頁小字、印章、表格或圖；render 放大檢查後先刪。
- table cell 需要保留多行時可以用 `<br>`；final `[A-Za-z]` 掃描命中 `br` 係預期，但要確認無其他拉丁殘留。
- 模糊表格唔好默默推斷格位；crop/zoom 後仍唔肯定就報告 uncertainty。

## 完成前驗證

回覆完成前，至少跑：

```bash
rg -n "\[|\]|<!--|gjcool|此頁OCR|[A-Za-z]" TARGET.md || true
rg -n "廣東.*報告書|議局籌辦處第|承印|廣州大典|太典|史部|政書|經濟學K|縱濟學|澄 濟" TARGET.md || true
git diff --check -- TARGET.md
```

刪 page comments 後，再檢查普通正文 soft wrap：

```bash
python3 - <<'PY'
from pathlib import Path
lines = Path("TARGET.md").read_text().splitlines()
def structural(s):
    t = s.strip()
    return not t or t.startswith(("#", "- ", "|", ">", "```")) or s.startswith("  ")
print(sum(1 for i in range(len(lines)-1) if not structural(lines[i]) and not structural(lines[i+1])))
PY
```

最後回覆要講清楚：

- PDF 頁數；
- page marker 或 section-to-page continuity 有冇檢查；
- 是否每頁都有視覺對照；
- 有冇缺 OCR、補 OCR、或由其他底稿重建；
- 有冇剩低疑難字/疑難段；
- 有冇殘留 brackets、page comments、OCR notices；
- 舊式標點同合法拉丁詞係經查保留，唔係機械漏清；
- 刪 page comments 後有冇做一段一行同 heading/list/table spacing 檢查；
- `git diff --check` 是否通過。

## 詳細參考

長文、final pass、表格、尾頁、章程、速記錄、講義合訂、常見高信心錯字，先讀 `references/jyut-ocr-detailed-rules.md`。

遇到長報告、表格、list、印章、尾頁、或者反覆 OCR 陷阱，再讀 `references/common-ocr-traps.md`。
