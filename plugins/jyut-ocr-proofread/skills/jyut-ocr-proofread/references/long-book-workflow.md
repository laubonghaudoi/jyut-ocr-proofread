# 長單檔 OCR 校對工作流

用呢份 reference 處理需要 `structured`／`release` profile 嘅單一 PDF，例如分段並行、跨頁結構、重複掃描或缺頁風險。頁數本身唔係升級理由；單一 writer 可以安全逐頁完成嘅文件仍可用 `basic`。先跟目標 repo 嘅 `AGENTS.md`，再用呢份流程補充主 skill；基本字形、頁面家具同一般 OCR 規則唔喺度重複。

## 目錄

- [先建立頁面清單](#先建立頁面清單)
- [安全分段並行](#安全分段並行)
- [保存 OCR 來源](#保存-ocr-來源)
- [處理原 PDF 真缺頁](#處理原-pdf-真缺頁)
- [定義 page marker 邊界](#定義-page-marker-邊界)
- [重排跨頁正文同註腳](#重排跨頁正文同註腳)
- [集中合併同定稿](#集中合併同定稿)
- [對賬長目錄](#對賬長目錄)
- [建立 release plan 同證據圖（只限 release）](#建立-release-plan-同證據圖)
- [凍結同封關](#凍結同封關)
- [驗證結構不變量](#驗證結構不變量)
- [檢查未追蹤新檔](#檢查未追蹤新檔)

## 先建立頁面清單

深入轉寫之前，先分開「PDF 掃描頁序」同「書內印刷頁碼」。唔好假設兩者只相差固定數字，亦唔好用印刷頁碼命名 render 圖。

建立一份 machine-readable page manifest。Root 至少要有：

| 欄位 | 要記錄嘅內容 |
|---|---|
| `source_sha256` | 來源 PDF 完整 bytes 嘅 lowercase SHA-256 |
| `expected_scan_pages` | PDF 實際 scan 總數，正整數 |
| `pages` | 逐 scan records，恰好覆蓋預期 sequence |

每個 `pages` record 至少要有 `scan_page`、`render_file` 同 `render_sha256`；`marker_expected` 省略時預設係 `true`。其他版面 facts 可以按 project 需要加入：

| 欄位 | 要記錄嘅內容 |
|---|---|
| `scan_page` | PDF 內由 1 起計嘅實際頁序，唯一且連續 |
| `render_file` | 對應 render 圖檔，例如 `p-001.png` |
| `render_sha256` | render 圖完整 bytes 嘅 lowercase SHA-256 |
| `printed_page` | 書內頁碼；封面或無頁碼頁留空，唔好估 |
| `rotation` | `0`、`90`、`180`、`270` |
| `layout` | `single`、`spread`、折頁、插頁等 |
| `content` | `body`、`frontmatter`、`blank`、`plate` 等 |
| `relation` | `unique`、`exact_duplicate`、`partial_duplicate` |
| `marker_expected` | 預設 `true`；只有已核實刻意唔設 Markdown marker 嘅 scan 先明寫 `false` |
| `notes` | 缺頁、遮字、印章、裁邊、重複來源等例外 |

Page manifest 封存 source/render 身份、scan facts 同 marker policy；`boundaries.json` 另行封存 Markdown 邊界契約，writer／independent reviewer、page coverage、status 同 report hash 另存 durable review sidecar。三者可以用 scan page 對齊並一齊入 release plan，但唔好將一個 page-manifest `visual_status` 當成獨立覆核證據，亦唔要求將 reviewer 狀態硬塞返 page manifest。每份 schema要明寫相對路徑係以 validation root、manifest directory定 plan directory為 base；解析後先 canonicalize同做 containment／alias／symlink檢查，唔依賴執行命令時嘅 CWD。

逐頁視覺確認以下情況，並將決定寫入 manifest：

- 將跨頁 spread 當一個 scan page 記錄，但另記左右半頁對應嘅印刷頁碼同閱讀次序。
- 將旋轉頁記錄實際校正角度；校對時用校正後圖，但保留原 scan page provenance。
- 只喺放大檢查後先標記 `blank`；唔好沿用 OCR 嘅空白判斷。
- 用圖像同正文一齊確認 `exact_duplicate`；只保留第一個有效掃描，另記被略去頁。
- 將只重複半版、跨頁重疊或重影記成 `partial_duplicate`；只刪已確認重複區域，保留獨有內容。
- 見到印刷頁碼跳號時，先排除無編號插頁、錯釘、重複 scan、spread 同裁切，先判斷係咪真缺頁。

## 安全分段並行

長單檔可以按 scan page 分段並行，但只容許每個 worker 寫自己嘅獨立暫存檔。唔好畀多個 agent 同時改最終 Markdown，亦唔好畀佢哋共同 append 同一個 segment。

用 Codex／Claude Code subagent 時，assignment contract、writer seal、read-only independent audit、模型繼承、併發上限同 late-patch 重核要求見 `subagent-orchestration.md`。LLM child 最多用平台可用 slots 同八個兩者之中較小者；Apple Vision raw OCR 係另一個本機 process pool，唔計做 LLM reviewer，亦唔由每個 subagent 重複啟動。

開始前先由主 agent：

1. 凍結 page manifest 同 render 圖命名。
2. 按章節或穩定頁界切段，例如 `p001-080.md`、`p081-160.md`；優先喺篇章邊界切，避開表格、長 list、引文同註腳中間。
3. 為每段指定唯一 owner、scan page 範圍、輸入圖、輸出檔同已知風險。
4. 讓相鄰 worker 只讀前後一至兩頁作上下文；寫入內容仍以自己範圍為限，避免重複轉錄。
5. 要求每段保留 scan page marker，並回報首尾句、首尾結構、疑難位置同未解決邊界。

如果切口無法避開跨頁正文，明確指定邊界 owner。前段保留頁尾正文，後段由下一 scan page 第一個獨有字開始；由中央合併者決定最終 `join` 或 `break`。

合併前先凍結 segments，記錄每段 SHA-256。worker 完成後只做 read-only 覆核；如要再改，先由主 agent 重新分派唯一 owner。保留一輪獨立 visual audit，除交界、表格、註腳同 heading 外，亦要掃冇 `[]` 嘅高信心人名、官名、固定搭配同漏句。

聲稱 direct review嘅每個 scan都要以 individual original／full-resolution render作文字依據，並驗目前 page manifest render hash；contact sheet、thumbnail同OCR只可導航或排risk queue。Durable checkpoint要保存實際continuous frontier同exact reviewed scan set，唔用chat progress或一個泛稱 `reviewed_count`代替。Writer、independent reviewer、delta rows、neighbor/context同unique union分開計，reviewer身份亦要同writer不同。

交付前收齊所有 writer／auditor final report，確認冇 active 或可能再寫檔嘅 page-range task。任何 late patch 都令 segment hash、assembly、receipt 同舊 final audit 失效；若 patch 發生喺獨立覆核之後，受影響 scan 要重新獨立覆核，或者由另一 reviewer 核實一份具體而 sealed 嘅 amendment ledger。套用修正後重新凍結同完整 assemble，唔可以只喺舊 final 上補一刀就交付。

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

macOS 有 Apple Vision 同 Swift toolchain 時，可以喺 skill 目錄用 bundled pool runner。`--first-page`／`--last-page` 同 `--pages` 互斥；`--workers auto` 會取待跑頁數、physical core 數同八者中最小值。Report 必須放喺 OCR output 目錄之外，避免污染只容許逐頁 JSON／TXT pair 嘅 project-local provenance gate：

```bash
python3 scripts/run_apple_vision_ocr.py RENDER_DIR OUTPUT_DIR \
  --first-page 1 --last-page 800 --workers auto \
  --report reports/apple-vision-run.json
```

Runner 只會自動略過 render、tool、Vision profile 同 JSON／TXT hash 全部吻合嘅已完成頁；舊 schema、殘缺 pair 或內容漂移預設 fail closed，要確認後先顯式加 `--overwrite`。單頁失敗會令整批 exit nonzero，但其他成功頁可以原子提交，下次以同一 selector 安全續跑。Run report 同 OCR JSON 係 draft provenance，唔取代 page manifest、逐頁 visual review 或 `release` tool snapshot。

## 處理原 PDF 真缺頁

只有證據顯示原 PDF 本身缺頁，先進入外部同版復原流程。先記錄：

- 缺失嘅印刷頁碼同前後 scan page；
- 前頁末句、後頁首句同預期銜接；
- PDF 物件頁數、render 頁數同 page manifest 結果；
- 已排除嘅重複、插頁、spread、錯序同漏 render 情況。

再搜尋同版來源。優先使用題名、作者／編者、出版社、出版年、版次、卷冊、館藏編號或 OCLC 等互相吻合嘅掃描或全文。至少用兩個獨立館藏／平台交叉核對；如果兩個平台實際轉載同一份 OCR，就唔當兩份獨立文字證據。

新增 evidence manifest，至少保存：

```yaml
missing_printed_page: "<printed page as shown by the source>"
between_scan_pages: ["<previous scan>", "<next scan>"]
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

但要喺獨立 `boundaries.json` 對 page 42 記成 `join`，並由 finalizer 產生：

```markdown
這句在頁尾尚未完成，下一頁繼續。
```

真正分段就記 `paragraph`：

```markdown
上一段完。

<!-- page_043 -->

下一段開始。
```

根據逐頁視覺 boundary ledger 建立獨立 JSON；key 係 marker page，唔係印刷頁碼。舊 rich page manifest 若已有 `boundary_before`，只可以經明確 projection／crosswalk 搬入，唔由新 parser 猜：

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

由 annotated 稿產生 manifest 時，判斷空行可以用 `.strip()`，但分類內容必須保留原始 leading whitespace。同一清單繼續時，下一個非空行若係兩格／tab 縮排普通續文就用 `list-cont`，若本身係 nested bullet 就用 `list-new`；如果 PDF 顯示前後係兩個獨立 lists，則用 `paragraph`／`structural`。先 `.strip()` 再分類會誤標成 `join`，final 會將縮排或子項黐落上一行，甚至產生 Markdown hard break。Bundled audit／finalizer 會拒絕破壞性 `join`，但保留經視覺確認嘅真正 break。`structured` final audit 用目前 `--annotated-source` 加 boundary manifest 即場 exact 重建並取得 verified break lines；`release` 用 receipt 重播同一證明。淨係 audit final、兩種 proof 都冇提供時，仍會保守報 `loose-list-blank`。

先 deterministic assemble／replay，再 audit annotated 稿同 dry-run finalizer。以下共同步驟適用於 `structured` 同 `release`：

```bash
python3 scripts/assemble_ocr_segments.py ordered-segments.json
python3 scripts/assemble_ocr_segments.py ordered-segments.json --output combined-annotated.md --write --receipt segment-assembly-receipt.json
python3 scripts/assemble_ocr_segments.py ordered-segments.json --output combined-annotated.md
python3 scripts/audit_ocr_markdown.py combined-annotated.md --mode annotated --expected-count "$EXPECTED_SCAN_COUNT" --boundary-manifest boundaries.json --page-manifest page-manifest.json --source-pdf /current/path/source.pdf
python3 scripts/finalize_page_markers.py combined-annotated.md --expected-count "$EXPECTED_SCAN_COUNT" --boundary-manifest boundaries.json
```

`structured` profile 如果唔要求 sealed release closure，可以直接寫另一個 final 檔，再按實際啟用嘅 notation／source gates 做 final audit；唔好虛構 receipt：

```bash
python3 scripts/finalize_page_markers.py combined-annotated.md --expected-count "$EXPECTED_SCAN_COUNT" --boundary-manifest boundaries.json --output final.md --write
python3 scripts/audit_ocr_markdown.py final.md --mode final --annotated-source combined-annotated.md --boundary-manifest boundaries.json --page-manifest page-manifest.json --source-pdf /current/path/source.pdf --json-report final-audit.json
```

只有 `release` profile 先以以下分支取代上面嘅 structured write／audit，建立並驗證 pre-final plan，再由 finalizer 寫 receipt：

```bash
python3 scripts/validate_release_evidence.py release-plan.json
python3 scripts/finalize_page_markers.py combined-annotated.md --expected-count "$EXPECTED_SCAN_COUNT" --boundary-manifest boundaries.json --output final.md --receipt assembly-receipt.json --release-plan release-plan.json --write
python3 scripts/audit_ocr_markdown.py final.md --mode final --notation-policy notation-policy.json --source-exceptions source-exceptions.json --page-manifest page-manifest.json --source-pdf /current/path/source.pdf --assembly-receipt assembly-receipt.json --json-report final-audit.json
```

第一次核對 boundary／issue 必須保留預設 verbose 輸出。全部位置已人工處理後，長書重跑可加 `--summary-only`；audit 仍按 severity/code 列 deterministic `ISSUE-COUNT`，finalizer 仍保留 dry-run、write 同 receipt 摘要，exit code 不變。見到任何非零 issue count，就移除 `--summary-only` 重跑定位，唔可以只憑摘要放行。

Annotated 命令故意唔帶 notation policy：即使來源符號已核圖，佢只可以係 known annotated-stage issue，唔可以將該 gate 稱為 clean；final mode 先按 exact policy accept。文件冇 notation policy 或 source exception 時，省略相應 final options；`structured`／`release` page manifest 唔好省略。每個 scan record 預設都應有 marker；若空白、重複或其他已核實原因刻意唔設 marker，要喺該 record 明寫 `"marker_expected": false`，唔靠未封存嘅 command-line skip 暗示。Assembler 同 audit 都會將實際 annotated markers 同 sealed policy 對賬。

## 重排跨頁正文同註腳

頁底註腳經常插喺跨頁正文中間。先分辨「版面出現次序」同「正文閱讀次序」，唔好照 OCR 輸出將註腳塞入未完成句。

題名尾端 `*`／`**` 指向嘅來源題注係另一種結構：佢屬於題名 item，唔係普通頁底註腳。題注錯掛下一個 heading，或者題注夾喺 page marker 同續正文之間，要跟 `review-evidence.md` 用 `audit_title_notes.py` 處理；搬動題注後同時重判相關 boundary。

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

用 strict ordered manifest：root 恰好有 `schema_version`、`separator_utf8`、總 `scan_start`／`scan_end`、sealed `page_manifest: {path, sha256}` 同 `segments`；每段恰好有 `path`、`sha256`、`scan_start`、`scan_end`。`separator_utf8` 只可係空字串或全由 LF newline characters 組成，唔接受文字、CR、tab 或 space。`assemble_ocr_segments.py` 會驗 segment hashes、ranges 無縫無重疊，同每段實際 canonical markers exact 等於 sealed page manifest 喺該 range 內 `marker_expected: true` 嘅 pages；按 separator 重播 bytes 後，再驗組裝後 marker sequence exact 等於 ordered segment inventory。用 `--output combined-annotated.md` dry-run 會 byte-for-byte 比較現有檔；replay 不一致要停止 finalize。首尾句、metadata ownership 同跨段語義仍由 assembly review sidecar 核實，唔屬 strict parser 保證。

先對 annotated 合併稿跑 audit，再用唯一 finalizer 刪 marker。finalizer 要支援 dry-run，列出預期／實際 marker 數量、每種 boundary 數量同異常位置；數量唔符就停止，唔好輸出最終檔。

所有可能改文嘅獨立 visual sweep同未覆核頁補核，都要喺finalize之前完成。定稿後只跑read-only residual scan、一段一行、heading/list/table spacing、重複長段、whitespace同current-seal audit；post-final auditor一發現內容或結構問題，就重新分派唯一writer，返回凍結／assemble／plan／receipt／audit cycle，唔喺已封final補改。

如果同一冊或同系列有同名／高度相似篇章，用題名、日期、來源同正文相似度對齊，輸出差異作核圖候選。平行文本可能有真實版次、報源、刪節同標點差異，唔可以自動同步；各自仍以 PDF 為準。

## 對賬長目錄

目錄同正文 heading 分開轉錄，唔用正文自動生成目錄後當作核對完成。先按 TOC scan 建立「末端印刷參照 inventory」：每一條目記 scan、欄／閱讀次序、條目序號、原樣印刷參照同 render hash。逐 scan 比較 OCR 抽取數同圖上實際數；漏抽、誤併、誤拆或跨欄錯序另寫 recovery ledger，記原候選、復原結果、原因同圖像證據。完成後由唔同 reviewer 對 inventory 每一行嘅末端參照做 100% 圖像覆核，唔可以只抽查異常行；主抽取 ledger 同獨立 reference-audit ledger 嘅 count、次序、scan 同原樣參照必須完全對得上。

正式 mapping 前先定義 machine-readable `reconciliation_scope`。目錄橫跨多卷、多冊或交付只包含部分正文時，將全部 TOC items 分入互斥而完備嘅 partitions；每項恰好屬於一個 partition。每個 partition 要記 scope id、TOC index／scan 範圍、對應 body 範圍、預期 mapping cardinality，同係 `in-scope` 定 `out-of-scope`。Scope 外條目仍要逐項用具體 disposition 關閉，例如屬另一卷冊、屬另一交付單元或本 scope 無對應正文，唔可以靜靜略去或留成泛稱 `not-mapped`。

分開保存三套結構證據：

1. TOC item ledger：逐項題名、日期、末端印刷參照同 body mapping；
2. TOC group ledger：分組題名、開始／結束 item、TOC scan 同正文 group heading；分組唔計入 item count；
3. body heading inventory：每個 heading 記 `toc_eligible`，不適合映射目錄嘅後記、附記、參考資料或內部小節逐項寫明 exclusion reason。

建立逐項 reconciliation ledger，記錄 TOC index、TOC scan／原文／原樣印刷參照、body scan／heading、mapping 同視覺證據。圈號註號、來源標籤等例行 apparatus 省略可以用獨立 disposition，唔好同實質題名異文混成一類。涉及人名、官職、地名、日期、數字或文義嘅 semantic／high-risk variant，以及每一個頁碼差額或重排，都要由第二位 reviewer 同時 challenge TOC 同正文頁圖；「兩邊讀得通」唔足以證明係來源異文。

頁碼對賬要將「目錄印刷參照」同「正文實際 mapping」分欄保存。可用 `page-exact`、`fixed-ocr-reference`、`source-printed-ref-differs-from-body-start`、`source-printed-ref-maps-to-body-range`、`source-verified-reordered`、`unresolved` 等細分類 disposition；名稱可以按項目 schema 調整，但唔可以用一個泛稱 `page-difference` 遮住原因。無論 mapping 結論係乜，TOC transcription 都保留圖上原樣印刷參照，唔改成正文起頁。

封關時逐個 `reconciliation_scope` 驗證預期 cardinality；要求一對一嘅 in-scope partition，其 TOC item、TOC-eligible body heading 同 reconciliation rows 數量先要一致。Group count 另行對賬；每邊每項只映射一次；全部 in-scope mappings 同 out-of-scope dispositions 都要關閉。所有行要由 `exact`、`fixed`、`source-verified-apparatus-variant`、`source-verified-semantic-variant`、`source-verified-reordered` 等明確狀態關閉，唔保留未有視覺結論嘅泛稱 `review`。詳細 schema 同 gate 見 `release-gate.md`。

Release plan 有 scope 時，`evidence_ids` 必須同時綁定 TOC item ledger、body heading inventory、reconciliation ledger、variant challenge ledger 同 invariants QA 五項 active evidence。留意 `validate_release_evidence.py` 只驗 partition arithmetic、五項 IDs／types 同 sealed closure binding；佢唔讀 ledger rows、disposition 語義、reviewer coverage 或 challenge 結論。上述逐項 gate 必須另由項目 semantic invariant／challenge 工具驗證，或者人工 100% 審核並封存報告；缺呢層證據唔可以因 scope validator pass 就聲稱目錄對賬完成。

## 建立 release plan 同證據圖

本節只喺 `release` profile 使用。完成 replay、finalize 前建立 strict `release-plan.json`。用窄而固定嘅 `discovery.globs` 列出 pre-final evidence roots，避開之後先生成嘅 final／receipt／audit 目錄；按 path 排序建立 inventory。Plan-relative paths一律以plan directory為base；其他artifact內相對路徑跟各自schema聲明嘅base，唔由caller CWD猜。Plan 本身以外，每個 discovered regular file 都要恰好有一個 artifact classification，classified path 亦必須被 glob 發現。Output／receipt 唔可以 alias plan 或任何 classified artifact；即使檔案未存在，只要生成後會新命中 pre-final discovery glob，finalizer 都要喺寫入前拒絕。

每項 required fields 係 `id`、`type`、`path`、`classification`、`lifecycle`、`validation`、`sha256`、`depends_on`；optional 只係 `exclusion_reason`、`superseded_by`、`singleton_role`。Current evidence 用 `classification: include`、`lifecycle: active`、`validation: pass`；working／舊版用 `exclude` 加 `historical` 或 `superseded` 同理由，後者先可以用 `superseded_by` 指向 active replacement。重複 active `singleton_role` 會失敗。

`sha256` 直接封 artifact 完整 bytes。每條 `depends_on` 用 exact `{target, relation, hash}`；hash 係 `full`，或者列 non-overlapping RFC 6901 `pointers` 嘅 `canonical-subset`。Roots 嘅 transitive closure 必須恰好等於全部 active included artifacts；dependency／supersession graph 要各自無循環。完整 schema、五項 reconciliation evidence types 同 canonical serialization 見 `release-gate.md`。

舊 rich manifest／policy 唔交俾 strict parser 猜。保留舊 hash，產生 current-schema projection 同 sealed field crosswalk，將舊檔分類 historical／superseded、新檔分類 active，再驗 plan 同重新封存；若原有逐頁 OCR／review evidence 仍可對返目前文字同 renders，毋須因此重做 OCR。

Pre-final plan 必須包括 combined annotated 同 boundary manifest，但唔包括待生成 final、receipt 或 final audit。只要 active artifacts 出現 `toc-item-ledger`、`body-heading-inventory`、`toc-body-reconciliation-ledger`、`variant-challenge-ledger` 或 `invariants-qa` 任一專用 type，`reconciliation_scope` 就唔再 optional；缺少即 fail closed。先跑 `validate_release_evidence.py`；finalizer 寫 receipt 時帶 `--release-plan`，會再驗並自動封存 plan 同 active closure。Final audit JSON 完成後，如要完整 bundle，再另建 `release-bundle-plan.json`，唔好回餵舊 finalizer 造成 hash cycle。

## 凍結同封關

最後一輪 assemble 之前，segment／review manifest 要列出每段 owner、校對狀態、未解決 issue、風險同 frozen hash。只有所有 final report 已收齊、無 active writer／auditor，而且 project 要求嘅獨立覆核已完成，先可以產生 candidate。Candidate seals產生後全部writer STOP-WRITE；verifier只核formal seals，任何patch都要建立新candidate並重核。`structured` 可以沿用 project-local manifest，毋須為咗叫做封關而建立 release plan、receipt 或 canonical amendment ledger。

獨立覆核之後如要 patch，唔可以只更新 segment hash 再繼承舊結論。要由第二位 reviewer 重新覆核所有受影響 scan；跨頁 join、heading、list、table 或註腳改動要連相鄰頁一併覆核。`structured` 更新 project-local review 記錄、重新凍結、assemble 同 audit 即可。

封關時另用 `audit_ocr_markdown.py --page-manifest ... --source-pdf ...` 驗證目前 PDF SHA、`1..N` scan sequence、annotated marker coverage 同所有 render hash。Manifest 入面舊 source path 可以因搬檔而失效，但所選新路徑內容必須同 `source_sha256` 完全一致。

### `release` 專用 evidence closure

以下只喺 `release` profile 使用。Review sidecar builder 必須由 durable writer／auditor ledger 重建，狀態只可由 `ocr-only` 單向升至 `worker-checked`、`independent-checked`；重跑前後比較逐頁狀態、reviewer range、`unresolved` 同 evidence hash，禁止降級。`independent-checked` 要記錄同 writer 不同嘅 reviewer、完整覆核頁集合同 report／ledger SHA-256，唔可以只改 top-level 字串。

Release late patch 除咗重新獨立覆核受影響 scan，另一條可接受路徑係建立具體而 sealed 嘅 amendment ledger，逐項記 `before`、`after`、`scan_page`、目前 `render_sha256`、`independent_verifier`、證據 artifact 路徑同 `evidence_sha256`；validator 驗 schema、hash 同 coverage，但 before／after 對 baseline／current 嘅語義關係仍由 reviewer 同 evidence 負責。兩條路都要更新 durable review evidence，將新 review／amendment ledger 列入 pre-final plan 嘅 active closure，再重新凍結、assemble、finalize 同 audit。

新 amendment 一律使用 `review-evidence.md` 嘅 canonical `ocr-review-amendment` schema同固定 pass status，再跑 `validate_review_evidence.py`；唔好為 semantic／title-note／boundary 修正另創 status 或 hash field。若已有 release plan，先用 `plan_release_rebuild.py` 列 direct／transitive stale artifacts同 dependency-first重建次序，再重建 plan。

長書 finalizer 寫 `--receipt` 必須帶 `--release-plan`；佢會重驗 plan，確認 annotated target 同 boundary manifest 各自恰好係 active included artifact，再以 schema v2 寫入恰好一個 `release-plan` role 同 ordered complete active closure。`--upstream` 只係 optional membership assertion，唔再用嚟人手列齊 sidecars。Final audit 用 `--assembly-receipt` 重新驗 plan、classified hashes、annotated／boundary membership 同 receipt upstreams exact 等於 plan ordered active closure；legacy v1 receipt 雖可讀，但必報 `assembly-receipt-release-plan-missing`，唔提供 verified-break exemption，必須重新 finalize。任何 input 或 final 改變都必須重新 replay assembly、重建 plan、finalize 同 audit。完整 schema、notation policy、source exception 同指令見 `release-gate.md`。

Release page manifest 本身要由 receipt 封存。

Schema v2 receipt 加 final audit 重驗可以證明當前 plan closure、recorded inputs 同 final 冇漂移，但唔自行證明 combined annotated 當初語義上按正確次序產生；所以仍要驗證 ordered segment manifest、marker inventory 同首尾句。唔用 mtime 判 freshness。

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
git check-ignore -v -- TARGET.md || true
```

`tracked`、`untracked`、`ignored`同repo外係交付狀態，唔係OCR準確度。Untracked／ignored output仍可以本機內容同release closure完成，但未repo-integrated；final回覆要分開披露，亦唔因完成OCR而自動stage、commit或publish。

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
