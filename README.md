# 粵文 OCR 校對

呢個係 Codex plugin，用嚟按同名 PDF 校對歷史中文 OCR Markdown，流程基於 `jyut-ocr` repo 嘅校對守則。

plugin 會安裝一個 skill：`jyut-ocr-proofread`。skill body 同 bundled OCR rules 都用粵文寫，令 agent 處理 OCR 校對時直接讀到同 repo 指示一致嘅工作語言。

## 佢會做咩

- 以 PDF 圖像作最終依據；
- 保留 PDF 原有字形、異體字、舊字形同舊式標點；
- 查 PDF 後先刪頁眉、頁腳、書脊、承印資訊、OCR notice 等頁面家具；
- 優先處理低信心括號同語境唔通嘅高信心 OCR 錯字；
- 按 PDF 版面重整 heading、段落、bullet list、table；
- 新 OCR 或補漏時先用視覺識別，GJ.cool 只作背景並行或極短時間輔助；
- 長文或 final pass 時載入更完整嘅粵文 `jyut-ocr` 校對 reference。

## 新版更新重點

- 多份獨立 Markdown/PDF pair 時，預設可用並行 subagent 分工；每個 subagent 只改自己負責嘅檔案，主 agent 仍然要 review 改動同做最後驗證。
- 新 OCR、補漏 OCR 或 page marker 跳頁時，立即由 PDF render 圖像視覺轉寫；GJ.cool 只係 5 秒內即時可用時先作底稿。
- 如果 GJ.cool API 回覆 `wait for ... seconds`，本輪任務餘下部分即刻停止自發重試 GJ.cool，避免為 API 輪詢停低校對。
- 開工前要先查 page marker、section-to-page continuity、最後非空白頁覆蓋情況；residual scan 乾淨唔等於全文完整。
- Markdown final output 要刪 page comments，普通正文維持一段一行，heading、bullet list、跨頁續文同 table spacing 都要按 PDF 版面整理。
- 表格、票據、圖、尾頁、淡字空白頁同印章要 render 放大檢查；直排表格要按欄列重建，唔照 OCR 行序硬排。
- 完成前檢查更明確：掃 brackets、page comments、OCR notice、頁面家具、拉丁殘留同常見高信心錯字；刪 page comments 後查 soft wrap，最後跑 `git diff --check`。

詳細版本摘要見 [CHANGELOG.md](CHANGELOG.md)。

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
```

## License

MIT
