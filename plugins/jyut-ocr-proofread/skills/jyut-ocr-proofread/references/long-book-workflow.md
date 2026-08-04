# 長單檔 OCR 校對工作流

用呢份 reference 處理頁數多、需要分段並行、存在重複掃描或缺頁風險嘅單一 PDF。先跟目標 repo 嘅 `AGENTS.md`，再用呢份流程補充主 skill；基本字形、頁面家具同一般 OCR 規則唔喺度重複。

## 目錄

- [先建立頁面清單](#先建立頁面清單)
- [安全分段並行](#安全分段並行)
- [保存 OCR 來源](#保存-ocr-來源)
- [處理原 PDF 真缺頁](#處理原-pdf-真缺頁)
- [定義 page marker 邊界](#定義-page-marker-邊界)
- [重排跨頁正文同註腳](#重排跨頁正文同註腳)
- [集中合併同定稿](#集中合併同定稿)
- [對賬長目錄](#對賬長目錄)
- [凍結同封關](#凍結同封關)
- [驗證結構不變量](#驗證結構不變量)
- [檢查未追蹤新檔](#檢查未追蹤新檔)

## 先建立頁面清單

深入轉寫之前，先分開「PDF 掃描頁序」同「書內印刷頁碼」。唔好假設兩者只相差固定數字，亦唔好用印刷頁碼命名 render 圖。

建立一份 machine-readable page manifest；至少記錄：

| 欄位 | 要記錄嘅內容 |
|---|---|
| `scan_page` | PDF 內由 1 起計嘅實際頁序，唯一且連續 |
| `render_file` | 對應 render 圖檔，例如 `p-001.png` |
| `printed_page` | 書內頁碼；封面或無頁碼頁留空，唔好估 |
| `rotation` | `0`、`90`、`180`、`270` |
| `layout` | `single`、`spread`、折頁、插頁等 |
| `content` | `body`、`frontmatter`、`blank`、`plate` 等 |
| `relation` | `unique`、`exact_duplicate`、`partial_duplicate` |
| `boundary_before` | 同上一掃描頁／前置內容嘅 `start`、`join`、`paragraph`、`list-cont`、`list-new` 或 `structural` |
| `marker_expected` | 預設 `true`；只有已核實刻意唔設 Markdown marker 嘅 scan 先明寫 `false` |
| `visual_status` | `ocr-only`、`worker-checked` 或 `independent-checked` |
| `notes` | 缺頁、遮字、印章、裁邊、重複來源等例外 |

逐頁視覺確認以下情況，並將決定寫入 manifest：

- 將跨頁 spread 當一個 scan page 記錄，但另記左右半頁對應嘅印刷頁碼同閱讀次序。
- 將旋轉頁記錄實際校正角度；校對時用校正後圖，但保留原 scan page provenance。
- 只喺放大檢查後先標記 `blank`；唔好沿用 OCR 嘅空白判斷。
- 用圖像同正文一齊確認 `exact_duplicate`；只保留第一個有效掃描，另記被略去頁。
- 將只重複半版、跨頁重疊或重影記成 `partial_duplicate`；只刪已確認重複區域，保留獨有內容。
- 見到印刷頁碼跳號時，先排除無編號插頁、錯釘、重複 scan、spread 同裁切，先判斷係咪真缺頁。

## 安全分段並行

長單檔可以按 scan page 分段並行，但只容許每個 worker 寫自己嘅獨立暫存檔。唔好畀多個 agent 同時改最終 Markdown，亦唔好畀佢哋共同 append 同一個 segment。

開始前先由主 agent：

1. 凍結 page manifest 同 render 圖命名。
2. 按章節或穩定頁界切段，例如 `p001-080.md`、`p081-160.md`；優先喺篇章邊界切，避開表格、長 list、引文同註腳中間。
3. 為每段指定唯一 owner、scan page 範圍、輸入圖、輸出檔同已知風險。
4. 讓相鄰 worker 只讀前後一至兩頁作上下文；寫入內容仍以自己範圍為限，避免重複轉錄。
5. 要求每段保留 scan page marker，並回報首尾句、首尾結構、疑難位置同未解決邊界。

如果切口無法避開跨頁正文，明確指定邊界 owner。前段保留頁尾正文，後段由下一 scan page 第一個獨有字開始；由中央合併者決定最終 `join` 或 `break`。

合併前先凍結 segments，記錄每段 SHA-256。worker 完成後只做 read-only 覆核；如要再改，先由主 agent 重新分派唯一 owner。保留一輪獨立 visual audit，除交界、表格、註腳同 heading 外，亦要掃冇 `[]` 嘅高信心人名、官名、固定搭配同漏句。

交付前收齊所有 writer／auditor final report，確認冇 active 或可能再寫檔嘅 page-range task。任何 late patch 都令 segment hash、assembly、receipt 同舊 final audit 失效；套用修正後重新凍結同完整 assemble，唔可以只喺舊 final 上補一刀就交付。

## 保存 OCR 來源

Apple Vision、macOS Live Text 或其他本地 OCR 只可以產生有頁面 provenance 嘅底稿。每批輸出至少連同以下資料保存：

- 原 PDF 路徑或穩定識別碼；
- `scan_page` 同 `render_file`；
- render DPI、rotation、crop 座標或 crop 檔；
- OCR 引擎同執行日期；
- 未經視覺核對嘅狀態標記。

將 OCR 結果按 scan page 分隔，或者直接用 `<!-- page_001 -->` 呢類 marker 包住。唔好將無頁碼嘅大段 Apple Vision 輸出貼入正文，否則之後無法證明漏頁、重複或錯序。

將 Apple Vision 當提示：用佢加快建立底稿同尋找可疑字，但逐頁睇 PDF 圖核對直排次序、淡字、跨欄、註腳、表格同 spread。即使用戶明確要求使用 macOS OCR，都唔好將「OCR 已跑完」當成「已視覺校對」。

同頁有 corrected／nocorrected、不同 DPI 或不同 crop OCR 時，比較字數、低信心數、差異率同長 unmatched run，建立 risk queue 並優先核對漏句候選。分歧只係風險訊號，多個 pass 高度一致亦唔證明正確；唔用投票取代 PDF 視覺判斷。

## 處理原 PDF 真缺頁

只有證據顯示原 PDF 本身缺頁，先進入外部同版復原流程。先記錄：

- 缺失嘅印刷頁碼同前後 scan page；
- 前頁末句、後頁首句同預期銜接；
- PDF 物件頁數、render 頁數同 page manifest 結果；
- 已排除嘅重複、插頁、spread、錯序同漏 render 情況。

再搜尋同版來源。優先使用題名、作者／編者、出版社、出版年、版次、卷冊、館藏編號或 OCLC 等互相吻合嘅掃描或全文。至少用兩個獨立館藏／平台交叉核對；如果兩個平台實際轉載同一份 OCR，就唔當兩份獨立文字證據。

新增 evidence manifest，至少保存：

```yaml
missing_printed_page: 352
between_scan_pages: [201, 202]
edition_identity:
  title: "..."
  publisher: "..."
  year: "..."
sources:
  - url: "https://..."
    identifier: "..."
    accessed: "YYYY-MM-DD"
  - url: "https://..."
    identifier: "..."
    accessed: "YYYY-MM-DD"
recovered_text: "..."
verified:
  characters: true
  punctuation: false
  layout: false
notes: "完整頁圖受限；兩個同版全文來源字序一致。"
```

只補兩個同版來源都支持嘅文字。若只取得全文資料而睇唔到完整頁圖，分開聲明「字序已交叉核實」同「標點／分段／行款未能視覺核實」；唔好聲稱該頁已逐頁視覺對照。若無法確認同版，保留缺頁標記或疑難說明，唔好用相近版本靜靜補入。

將缺頁復原寫入 Markdown metadata 或校補說明，並喺最後交付列出來源同限制。唔好因為復原後上下文通順就刪除 evidence manifest。

## 定義 page marker 邊界

刪 marker 之前，為每個相鄰 scan page 明確指定邊界。唔好等刪完先靠 soft-wrap scanner 猜返原意；錯誤空白段落唔會被一般相鄰行檢查捉到。

boundary contract 以「marker 所屬 scan page 同佢之前內容嘅關係」為準，避免 page N「接上一頁」定「接下一頁」嘅 off-by-one 誤會。使用以下 labels：

- `start`：第一個 marker 前面完全無非空白內容；移除 marker 同檔首空白。若前面有 metadata，就唔係 `start`，而係 `structural`。
- `join`：同一段、同一詞或同一句跨頁，移除 marker 後直接黐合，唔留換行。
- `paragraph`：上一段已完，下一頁另起正文段；移除後保留一個空白行。
- `list-cont`：下一頁係同一 item 續文；移除後按 item 語法黐合或保留兩空格縮入，唔開新 item。
- `list-new`：下一頁開始新 item；移除後保留 list 所需單換行，連續 items 中間唔加空白行。
- `structural`：下一頁開始 heading、table、引文或其他區塊；按 Markdown 結構保留所需空白行。

Annotated segment 可以保持普通 marker：

```markdown
這句在頁尾尚未完
<!-- page_042 -->
成，下一頁繼續。
```

但要喺 manifest 對 page 42 記 `boundary_before: join`，並由 finalizer 產生：

```markdown
這句在頁尾尚未完成，下一頁繼續。
```

真正分段就記 `paragraph`：

```markdown
上一段完。

<!-- page_043 -->

下一段開始。
```

將 page manifest 嘅 `boundary_before` 匯出成獨立 JSON；key 係 marker page，唔係印刷頁碼：

```json
{
  "boundaries": {
    "1": "start",
    "2": "join",
    "3": "paragraph",
    "4": "list-cont"
  }
}
```

每個實際 marker 必須恰好有一條記錄；多一條、少一條、重複 key、無效 label 或 `start` 前有正文都要失敗。唔好用一條會吞掉 marker 前後所有 whitespace 嘅全域 regex。無 JSON manifest 時，finalizer 只可以列出 marker 同左右 context，`--write` 必須拒絕；寫入時亦必須指定另一個 `--output`，永不覆寫 annotated 底稿。任何 heuristic suggestion 都唔係寫入依據。

由 annotated 稿產生 manifest 時，判斷空行可以用 `.strip()`，但分類內容必須保留原始 leading whitespace。同一清單繼續時，下一個非空行若係兩格／tab 縮排普通續文就用 `list-cont`，若本身係 nested bullet 就用 `list-new`；如果 PDF 顯示前後係兩個獨立 lists，則用 `paragraph`／`structural`。先 `.strip()` 再分類會誤標成 `join`，final 會將縮排或子項黐落上一行，甚至產生 Markdown hard break。Bundled audit／finalizer 會拒絕破壞性 `join`，但保留經視覺確認嘅真正 break。Final audit 只有帶相符 assembly receipt 時先承認呢類 verified break；冇 receipt 嘅 final 仍會保守報 `loose-list-blank`。

先 audit annotated 稿，再 dry-run finalizer；兩者輸出都核對後先寫另一個 final 檔：

```bash
python3 scripts/audit_ocr_markdown.py combined-annotated.md --mode annotated --expected-count 240 --skip-pages 17 --boundary-manifest boundaries.json
python3 scripts/finalize_page_markers.py combined-annotated.md --expected-count 240 --skip-pages 17 --boundary-manifest boundaries.json
python3 scripts/finalize_page_markers.py combined-annotated.md --expected-count 240 --skip-pages 17 --boundary-manifest boundaries.json --output final.md --receipt assembly-receipt.json --upstream ordered-segments.json --upstream segments/p001-120.md --upstream segments/p121-240.md --upstream notation-policy.json --upstream source-exceptions.json --upstream page-manifest.json --write
python3 scripts/audit_ocr_markdown.py final.md --mode final --notation-policy notation-policy.json --source-exceptions source-exceptions.json --page-manifest page-manifest.json --source-pdf /current/path/source.pdf --assembly-receipt assembly-receipt.json
```

本書冇 notation policy 或 source exception 時，先省略相應 option／upstream；長書 page manifest 唔好省略。Page manifest 每個 scan record 預設都應有 marker；若空白、重複或其他已核實原因刻意唔設 marker，要喺該 record 明寫 `"marker_expected": false`，唔靠未封存嘅 command-line skip 暗示。Final audit 會將 sealed annotated marker inventory 同 sealed page manifest 對賬。

## 重排跨頁正文同註腳

頁底註腳經常插喺跨頁正文中間。先分辨「版面出現次序」同「正文閱讀次序」，唔好照 OCR 輸出將註腳塞入未完成句。

按以下次序處理：

1. 從頁底讀到正文未完時，記低正文末句同註號。
2. 到下一 scan page 先接返正文，直至句子或段落真正完成。
3. 再將前頁註腳放喺完整正文段落之後；保留原註號同文字，唔改成現代腳註格式，除非目標 repo 另有規定。
4. 如果下一頁仍有正文續文，繼續接完先插註腳；唔好因 page marker 強行斷段。
5. 如果註腳本身跨頁，將佢標記成獨立 `join` 邊界，唔好同正文續文混合。

段落交界 final audit 要同時睇前頁、後頁同 assembled Markdown。只睇單頁 crop 無法確認註腳應插喺邊個完整段落之後。

## 集中合併同定稿

只由主 agent 或唯一 assembly owner 合併 segments。唔好直接將各段輸出覆寫最終 Markdown。

先產生 `combined-annotated.md`，再依次驗證：

1. metadata 只出現一次，而且位於最前面；
2. segment 順序同 manifest 一致；
3. 預期 scan page marker 每頁恰好一次，已記錄 duplicate／blank 例外除外；
4. 每個 segment 首尾句無重複、無漏字，所有 boundary 都有 contract；
5. 缺失印刷頁 marker 同 evidence manifest 一一對應；
6. heading、list、table 同註腳跨 segment 後仍然完整。

先對 annotated 合併稿跑 audit，再用唯一 finalizer 刪 marker。finalizer 要支援 dry-run，列出預期／實際 marker 數量、每種 boundary 數量同異常位置；數量唔符就停止，唔好輸出最終檔。

定稿後再跑 residual scan、一段一行、heading/list/table spacing、重複長段同 whitespace 檢查。另做一輪 read-only visual audit；抽查之外，所有未被 worker 視覺核對嘅頁都要補核，先可以聲稱完成逐頁對照。

如果同一冊或同系列有同名／高度相似篇章，用題名、日期、來源同正文相似度對齊，輸出差異作核圖候選。平行文本可能有真實版次、報源、刪節同標點差異，唔可以自動同步；各自仍以 PDF 為準。

## 對賬長目錄

目錄同正文 heading 分開轉錄，唔用正文自動生成目錄後當作核對完成。建立逐項 ledger，記錄 TOC index、TOC scan／原文／頁碼、body scan／heading、disposition 同視覺證據；圈號註號省略、跨 marker 日期、題名異文或原書異序各自保留。封關時 entry count、body heading count 同 ledger row count 要一致，而且每行由 `exact`、`fixed`、`source-verified-variant`、`source-verified-reordered` 等明確狀態關閉；唔保留未有視覺結論嘅泛稱 `review`。詳細要求見 `release-gate.md`。

## 凍結同封關

最後一輪 assemble 之前，review manifest 要列出每段 owner、`visual_status`、未解決 issue、風險同 frozen hash。只有所有 final report 已收齊、無 active writer／auditor，而且需要獨立覆核嘅頁已標成 `independent-checked`，先可以產生 release candidate。

Review sidecar builder 必須由 durable writer／auditor ledger 重建，狀態只可由 `ocr-only` 單向升至 `worker-checked`、`independent-checked`；重跑前後比較逐頁狀態、reviewer range、`unresolved` 同 evidence hash，禁止降級。`independent-checked` 要記錄同 writer 不同嘅 reviewer、完整覆核頁集合同 report／ledger SHA-256，唔可以只改 top-level 字串。

長書 finalizer 用 `--receipt`，並按 ordered manifest 次序重複傳入 `--upstream` segments；final audit 會讀嘅 notation policy、source exceptions 同 page manifest 亦要加入 `--upstream`。Receipt 會 hash upstream、combined annotated、boundary manifest 同 final output；final audit 用 `--assembly-receipt` 重算。任何 input 或 final 改變都必須重新 assemble／finalize／audit。Annotated target 同 boundary manifest 已自動記錄，唔好重複當 upstream。完整 JSON schema、notation policy、source exception 同指令見 `release-gate.md`。

封關時另用 `audit_ocr_markdown.py --page-manifest ... --source-pdf ...` 驗證目前 PDF SHA、`1..N` scan sequence、annotated marker coverage 同所有 render hash。Manifest 入面舊 source path 可以因搬檔而失效，但所選新路徑內容必須同 `source_sha256` 完全一致；page manifest 本身要由 receipt 封存。

Receipt 只證明記錄後檔案無變，唔自行證明 combined annotated 當初按正確次序產生；所以仍要驗證 ordered segment manifest、marker inventory 同首尾句。唔用 mtime 判 freshness。

## 驗證結構不變量

開始校對時就從 PDF 記錄可驗證嘅 invariants，完成後逐項比較 expected 同 observed。至少考慮：

- 名單或目錄明示嘅總項數；
- `第一條` 至末條、`一` 至 `十`、`A` 至 `J` 等序號連續性；
- 表格列數、欄數、每列小計同頁上總數；
- 人數、金額、百分比或分類合計；
- 註號有冇缺號、重號或無對應註文；
- 章節目錄同正文 heading 是否互相對應；
- manifest 嘅 unique／duplicate／missing page 數目能否重算 PDF 總頁數。

將檢查結果保存成簡短 QA 表：`invariant`、`expected`、`observed`、`status`、`evidence_page`。任何不一致都要回到 PDF 圖核查，唔好為咗令總數吻合而改字或改數。自動 count 只可以證明結構一致，唔可以代替逐字視覺校對。

## 檢查未追蹤新檔

先用以下命令確認 Git 狀態：

```bash
git status --short -- TARGET.md
git ls-files --error-unmatch -- TARGET.md >/dev/null 2>&1 && echo tracked || echo untracked
```

留意 `git diff --check -- TARGET.md` 對未追蹤新檔可以完全無輸出，並唔代表新檔無 trailing whitespace。對 tracked 檔照跑：

```bash
git diff --check -- TARGET.md
```

對 untracked 檔另外直接檢查內容，或者用 no-index diff：

```bash
git diff --no-index --check -- /dev/null TARGET.md 2>&1 || true
```

解讀 no-index 結果時睇輸出內容：因為檔案有差異，exit status `1` 可以係正常；任何 `trailing whitespace`、`space before tab` 或 `new blank line at EOF` 訊息先係 whitespace 錯誤。再用 UTF-8 safe 腳本作確定性檢查：

```bash
python3 - TARGET.md <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
bad = []
for number, line in enumerate(lines, 1):
    body = line[:-1] if line.endswith("\n") else line
    if body.endswith("\r"):
        body = body[:-1]
    if body.endswith((" ", "\t")):
        bad.append(number)
if bad:
    print(f"{path}: trailing whitespace at lines {bad}")
    raise SystemExit(1)
print(f"{path}: whitespace clean")
PY
```

最後同時記錄 residual scans、invariants、marker finalizer 同 whitespace check 結果；唔好只用一個乾淨嘅 `git diff --check` 當成全文完成證據。
