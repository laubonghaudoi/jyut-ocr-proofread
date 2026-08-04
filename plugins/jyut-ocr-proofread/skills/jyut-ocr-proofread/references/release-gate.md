# 長書校對封關同來源例外

用呢份 reference 處理本書校勘符號、原書本身嘅不配對標點、平行篇章互校，同埋長書最後 assemble／交付。呢啲規則係 release gate，唔係用嚟代替逐頁視覺校對。

## 目錄

- [先讀本書凡例](#先讀本書凡例)
- [保存來源異常](#保存來源異常)
- [傳播已確認錯字](#傳播已確認錯字)
- [用平行篇章產生候選](#用平行篇章產生候選)
- [封關長目錄對賬](#封關長目錄對賬)
- [凍結 review 同 assembly](#凍結-review-同-assembly)
- [寫入同驗證 hash receipt](#寫入同驗證-hash-receipt)
- [驗證來源 PDF 同 renders](#驗證來源-pdf-同-renders)
- [封關清單](#封關清單)

## 先讀本書凡例

開始正文前，先逐頁讀凡例、編輯說明、校勘說明同符號表。建立每本書自己嘅 notation policy，至少分類：

- OCR confidence markup；
- 編者訂正、增補、存疑或刪節；
- 原件破損、遮字或不可辨字；
- 註號、引文符號同舊式標點；
- Markdown 或 metadata 所需符號。

`[]` 喺一個 OCR pipeline 可能表示低信心，但同一本書嘅凡例亦可能明定佢係編者訂正。`［］`、`〈〉`、`□`、`（？）`、`……` 亦可能係來源內容。同一系列唔同冊可以採用半形／全形或唔同政策，唔好沿用上一本嘅假設。

如果凡例明確證明正文所有 ASCII square brackets 都係來源校勘符號，建立 `notation-policy.json`：

```json
{
  "ascii_square_brackets": {
    "meaning": "source-editorial",
    "scan_page": 7,
    "note": "凡例明定半形方括號表示編者訂正。"
  }
}
```

只喺 final audit 使用呢份 policy；annotated 階段仍要逐個處理 OCR 低信心候選。policy 係全書聲明，唔適合一份同時混用 source brackets 同未處理 confidence brackets 嘅稿。

```bash
python3 scripts/audit_ocr_markdown.py final.md --mode final \
  --notation-policy notation-policy.json
```

未有可靠凡例證據就唔好建立 policy。Residual scan 有命中唔代表一定要刪；逐項分類後先決定。

## 保存來源異常

日期矛盾、重複字、文句不通、未閉引號／括號或連續開引號，先放大核對 PDF。若印刷本確實如此，原樣保留；唔好為語意合理、年代次序或符號平衡改書。原書用 `□` 表示破損時亦保留 `□`，唔由上下文猜補。

Audit 會對以下全形 delimiter 做 stack 檢查：`“”`、`‘’`、`「」`、`『』`、`（）`、`［］`、`〈〉`、`《》`、`【】`、`〔〕`。命中先回 PDF；確認係 OCR 錯就修，確認原書如此就寫 exact-location exception：

```json
{
  "exceptions": [
    {
      "code": "unmatched-opening-delimiter",
      "line": 1284,
      "column": 9,
      "delimiter": "「",
      "text": "原書呢一整行只有「開引號而未閉合。",
      "scan_page": 297,
      "note": "普通圖同放大 crop 都見到原書未印閉引號。"
    }
  ]
}
```

支援嘅 code 係 `unmatched-opening-delimiter`、`unmatched-closing-delimiter` 同 `misnested-closing-delimiter`。`line` 同 `column` 都由 1 起計，column 以 Unicode character 計；佢哋連同 `delimiter`、`text` 必須同目前 final Markdown 完全一致，而且一條 exception 只承認一個 issue。一旦位置、內容或 issue 改變，audit 會報 `stale-source-exception`，避免舊白名單永久遮住新錯。

```bash
python3 scripts/audit_ocr_markdown.py final.md --mode final \
  --source-exceptions source-exceptions.json
```

Exception 只承認「原書如此」，唔係忽略未核對 issue。保留 scan page、核圖說明同必要 crop 路徑；final 回覆列出 accepted exception 數量同位置。

## 傳播已確認錯字

獨立視覺 sweep 經常喺冇 `[]` 嘅高信心文字先發現人名、官名同固定搭配錯形。每次確認一處後，記錄簡短 correction ledger：

```json
{"wrong":"釣鑒","verified":"鈞鑒","scan_page":88,"evidence":"p088 crop"}
```

再做三層候選搜尋：

1. 全書完全相同錯形；
2. 同一人名、官名、機關名或固定套語嘅一字之差；
3. 同題名、同日期或高度相似平行篇章入面嘅對應位置。

每個命中仍要核自己所屬 PDF 頁；唔做全域替換。定稿前重跑 ledger 入面所有 `wrong` 搜尋，將殘留分類成已修、另一個合法詞或尚待核圖。

## 用平行篇章產生候選

同一冊、同系列兩冊或附錄可能重收同一電文、條例、呈文。用正規化題名、日期、來源同正文相似度配對，再輸出逐字差異，可以有效浮出無括號錯字；但只可以做 risk queue。

按以下次序比較：

1. 完全同名 heading；
2. 題名近似，而且日期／發文人／受文人相同；
3. 正文有長共同片段或高相似度；
4. 對齊後列出替換、插入、刪除同所在 scan page。

唔可以用多數票、自動同步或較通順版本覆蓋另一冊。兩份平行文本可能來自不同報刊、版次、刪節、標點或編輯處理；每一個差異最後仍以各自 PDF 為準。

同理，多個 OCR pass、不同 DPI、corrected／nocorrected 或不同 crop 只用嚟比較字數、差異率同 unmatched run，優先排查漏句／高風險頁。多個 pass 一致亦唔證明正確。

## 封關長目錄對賬

長目錄同正文 heading 係兩套獨立來源文字。數量相同只係 invariant，唔證明逐項題名、日期、註號、頁碼或排序相同；唔好用正文 heading 直接重建目錄，再將輸出當成已核對目錄。

建立一行一項嘅 reconciliation ledger，至少記錄 TOC index、TOC scan／原文／頁碼、body scan／heading、對應狀態同圖像證據。狀態要收斂成 `exact`、`fixed`、`source-verified-variant`、`source-verified-reordered` 或 `unresolved` 等明確 disposition；交付時唔可以仍有泛稱 `review` 而未記視覺結論嘅行。正文題名有圈號而目錄省略、日期跨 scan marker、或者兩邊真係異文／異序時，各自按自己頁圖保留，對比時先用 normalization key，唔將 normalization 寫返來源文字。

封關同時驗證：TOC entry count、body heading count、ledger row count 相同；每邊每項只映射一次；目錄原次序、原頁碼同所有 override 都有 scan evidence。任何 heading、日期、marker boundary 或 override 後續修改，都要重跑完整 reconciliation。

## 凍結 review 同 assembly

每個 segment 或頁面 review manifest 至少記錄：

| 欄位 | 內容 |
|---|---|
| `scan_pages` | 唯一頁段範圍 |
| `writer` | 唯一 writer；舊 manifest 可保留 `owner` alias，但唔可以當 independent reviewer |
| `visual_status` | `ocr-only`、`worker-checked`、`independent-checked` |
| `unresolved` | 未解決 issue 數同位置 |
| `segment_sha256` | 最後一次凍結後 hash |
| `risk` | 表格、註腳、淡字、重複、跨段 heading 等 |
| `independent_reviews` | reviewer、覆核 scan range、證據 ledger／report path 同 SHA-256 |

Review evidence 係狀態，唔係可以由 sidecar builder 隨時重建嘅裝飾。狀態只能按 `ocr-only` → `worker-checked` → `independent-checked` 單向提升；builder 必須讀 durable writer／auditor ledger，唔可以用 hard-coded range 重寫狀態。每次重跑 sidecar 後比較逐頁 status、`unresolved`、reviewer coverage 同 evidence hash；一有降級即停止 assembly。標成 `independent-checked` 時 reviewer 必須同 writer 不同，而且覆核頁集合要覆蓋聲稱嘅全部 scan pages；top-level status 由逐頁記錄導出，唔可以手動高報。

進入 assembly 前必須：

1. 收齊所有 writer 同 read-only auditor 嘅 final report；
2. 確認冇 active、未回報或可能繼續寫檔嘅 page-range task；
3. 將所有 correction 同 exception 落到唯一 owner 嘅 segment；
4. 重算 segment hash，按 manifest 固定順序 assemble；
5. 驗證 combined annotated 真係由目前 ordered segments 產生；
6. 再 audit、dry-run 同 finalize。

任何 segment、ordered manifest、combined annotated、boundary manifest、finalizer input 或 final Markdown 嘅後續 patch，都令之前嘅 assembly receipt 同 final QA 失效。唔好只重跑最後一個 scanner；要由受影響嘅 assembly 步驟重新開始。

## 寫入同驗證 hash receipt

長書 finalizer 用 `--receipt`，並依 assembly 次序重複傳入 `--upstream`。至少包括 ordered-segment manifest 同每個 segment；combined annotated 同 boundary manifest 會自動記錄：

```bash
python3 scripts/finalize_page_markers.py combined-annotated.md \
  --expected-count 290 \
  --boundary-manifest boundaries.json \
  --output final.md \
  --receipt assembly-receipt.json \
  --upstream ordered-segments.json \
  --upstream segments/p001-100.md \
  --upstream segments/p101-200.md \
  --upstream segments/p201-290.md \
  --upstream notation-policy.json \
  --upstream source-exceptions.json \
  --upstream page-manifest.json \
  --write
```

Receipt 以 SHA-256 記錄 ordered upstream、combined annotated、boundary manifest 同 final output；唔用 mtime。Final audit 亦會用目前 annotated 同 boundary contract 即場重建 expected final，再同 final 做 byte-for-byte 比較。Final audit 必須帶同一份 receipt：

```bash
python3 scripts/audit_ocr_markdown.py final.md --mode final \
  --notation-policy notation-policy.json \
  --source-exceptions source-exceptions.json \
  --page-manifest page-manifest.json \
  --source-pdf /current/path/source.pdf \
  --assembly-receipt assembly-receipt.json
```

凡 final audit 會讀嘅 interpretation／provenance 檔，包括 notation policy、source exceptions 同 page manifest，都要喺最後一次 finalize 以 `--upstream` 加入 receipt；無相應檔案先省略。Annotated target 同 `--boundary-manifest` 已由 finalizer 自動記錄，唔好再當 `--upstream` 重複傳入，重複 input 會拒絕。若 delimiter issue 只喺第一份 provisional final 先浮出，先核圖建立 exception，再重新 finalize／寫 receipt；唔可以沿用早過 exception 嘅 receipt。

`assembly-input-changed` 表示 late patch 後未重新 finalize；`assembly-output-changed` 表示 final 喺 receipt 之後被改；`assembly-final-stale` 表示目前 annotated 加 boundary contract 重建唔出目前 final。Receipt 同重建可以證明 annotated-to-final 關係，但唔會自行證明 combined annotated 當初按正確 segment 順序拼接；所以 ordered manifest 同 assembly 驗證仍然係必要 gate。

## 驗證來源 PDF 同 renders

`source_pdf` 路徑可以因檔案搬位而失效，來源身份要靠 SHA-256。Page manifest 要保存 `source_sha256`、`expected_scan_pages`，以及每頁 `scan_page`、`render_file`、`render_sha256`。每頁預設 `marker_expected: true`；只有空白、重複等已逐頁確認而刻意唔設 Markdown marker 嘅 scan，先明寫 `"marker_expected": false`。封關時用目前實際 PDF 路徑重算來源 hash，再逐一重算所有 render；唔可以只 hash manifest 本身：

```bash
python3 scripts/audit_ocr_markdown.py final.md --mode final \
  --page-manifest page-manifest.json \
  --source-pdf /current/path/source.pdf
```

Audit 要求 scan sequence 恰好係 `1..expected_scan_pages`，每個 render 唯一、存在而且 hash 相符。帶 receipt 做 final audit 時，亦會將 sealed annotated markers 同 sealed page manifest 對賬；缺 marker 只可由該頁已封存嘅 `marker_expected: false` 解釋，唔可以靠漏傳 `--expected-count` 避過。搬動 PDF 時可以用新路徑，只要內容 hash 同已封入 receipt 嘅 manifest 一致；唔好為咗令路徑存在而靜靜換另一份 PDF。Render set 由 manifest 內逐頁 hash 封存，final audit 每次都重驗目前檔案。

## 封關清單

只有以下項目全部成立，先可以交付：

- 每個非空白 scan page 已有 `worker-checked`，要求獨立覆核嘅範圍亦有 `independent-checked`；
- independent reviewer 同 writer 身份分開，覆核 range、ledger／report hash 同逐頁 status 無降級；
- 所有 agent final report 已收齊，無 active writer／auditor；
- 高信心 visual sweep、correction propagation 同平行篇章候選已處理；
- notation policy 有凡例證據，或者所有 ASCII bracket 命中已逐項分類；
- delimiter issue 已修正或由 current exact-location source exception 承認；
- 長目錄 reconciliation 每行已有視覺 disposition，無殘留泛稱 `review`／`unresolved`；
- marker、boundary、註腳、tight list、table 欄數、heading、soft break、EOF whitespace 同 residual audit 通過；
- 目前 source PDF、完整 scan sequence、annotated marker coverage 同每個 render hash 都同已封存 page manifest 相符；
- receipt 同目前全部 upstream／annotated／boundary／final hash 相符；
- 最後一次 patch 之後已重新跑所有相關 gate；
- final 回覆清楚區分逐頁視覺核實、外部復原、source-as-printed exception 同仍未解決疑問。
