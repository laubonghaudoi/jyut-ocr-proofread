# 題名註、late amendment 同證據重建

用呢份 reference 處理來源確實存在嘅題名星號／題注、獨立覆核後嘅修改、跨 release 證據重用，同埋 release evidence 因上游改動而失效。呢啲係條件式 feature／`release` gates，唔係每份 OCR 都要建立嘅 artifact。一般逐頁校對、boundary labels、release plan schema 仍分別跟主 skill、`long-book-workflow.md` 同 `release-gate.md`。

## 目錄

- [題名註係獨立結構](#題名註係獨立結構)
- [審核跨頁題注同註腳](#審核跨頁題注同註腳)
- [建立 notation inventory](#建立-notation-inventory)
- [計 semantic changed-scan inventory](#計-semantic-changed-scan-inventory)
- [使用 canonical amendment ledger](#使用-canonical-amendment-ledger)
- [封 candidate 同 delta recheck](#封-candidate-同-delta-recheck)
- [重建失效證據](#重建失效證據)
- [重用另一冊或另一 release 嘅證據](#重用另一冊或另一-release-嘅證據)
- [分清 annotated 同 final 命令](#分清-annotated-同-final-命令)

## 題名註係獨立結構

歷史資料彙編常用題名尾端 `*`／`**` 指向編者題注或來源說明。佢唔係普通 Markdown 強調，亦唔係可以統一移到段末／全文末嘅頁底註腳。

- 每個主條目由相應 heading 開始，到下一個同級 heading 前結束；預設主條目係 `##`。
- 條目內 heading 尾端星號總數，要同該條目內 `* `／`** ` 題注星號總數一致。
- 題注屬於有星號嘅題名；一般放喺日期／署名之後、正文之前。PDF 若明確放另一位置，仍按圖保留，但要保存 ownership 結論。
- 題注喺 scan 頁底、下一頁先出下一個 heading 時，唔可以因 OCR 行序將佢掛到下一個條目。
- 移動題注會同時改變內容次序同 page boundary。修正後要重判涉及 marker 前後嘅 `join`／`paragraph`／`structural`，並重封 affected segment、boundary 同 review evidence。

用 bundled auditor 檢查題名星號、孤兒／漏註同跨頁 hazard：

```bash
python3 scripts/audit_title_notes.py combined-annotated.md \
  --boundary-manifest boundaries.json \
  --json-report title-note-audit.json
```

只喺凡例／頁圖已確認 `*`／`**` 係題名註 apparatus 時先跑呢個 adapter。普通 Markdown `*` list item 或 emphasis 同來源題注語法會相撞，auditor 唔可以取代逐 heading ownership 核對。如果來源用另一個 heading level 作主條目，明確傳入相應選項；唔好為咗令 count 相等而改星號。任何 mismatch 或 actionable hazard 都要回 PDF 圖核對。

## 審核跨頁題注同註腳

Auditor 專門檢查「marker 前最後一行係題注／圈號註，marker 後第一個非空行似正文」嘅位置：

- `title-note` 同 `numbered-note` 都預設 actionable，因為佢哋可能錯掛下一條目，亦可能遮住真正正文 continuation。行首似「來源」、citation 格式、語言、標點、下一個 heading 或 boundary label 都唔可以自行證明 ownership。
- 放行必須用 `--transition-reviews`：每行 exact 封 scan、note kind、marker 前後目前文字、boundary label、writer、另一位 reviewer 同非空 visual evidence。Root 同時封目前 target／boundary SHA-256；任何 hash／文字／label 漂移、duplicate、unused 或 unknown row 都 fail closed。
- Matching row 記做 `accepted-reviewed-note-transition`；呢個 status 只代表 project-local visual review 對目前 bytes 作出結論，唔係核心程式理解咗某種來源句式。
- `join`、`paragraph`、`structural`、`list-cont` 或 `list-new` 都唔會靠 label 自動放行；若確實合理，由 exact transition review 交代。

Strict v1 sidecar 例子：

```json
{
  "schema_version": 1,
  "artifact_type": "ocr-note-transition-reviews",
  "target_sha256": "<CURRENT_TARGET_SHA256>",
  "boundary_manifest_sha256": "<CURRENT_BOUNDARY_SHA256>",
  "reviews": [
    {
      "scan_page": 12,
      "note_kind": "title-note",
      "previous_text": "* 題注原文。",
      "following_text": "下一頁第一行原文。",
      "boundary_label": "paragraph",
      "disposition": "accepted-reviewed-note-transition",
      "writer": "writer-id",
      "independent_reviewer": "reviewer-id",
      "visual_evidence": "同時核對前後兩頁完整 render，確認為兩個獨立段落。"
    }
  ]
}
```

```bash
python3 scripts/audit_title_notes.py combined-annotated.md \
  --boundary-manifest boundaries.json \
  --transition-reviews transition-reviews.json \
  --json-report title-note-audit.json
```

完成修正後重跑 auditor，要求 item-level marker arity、orphan/missing 同未覆核 actionable hazard 全部為零。Raw heuristic hazard count 可以仍然大過零：如果工具刻意將 source-faithful interface 長期列為 actionable，就要以 deterministic hazard ID 做 exact disposition ledger，要求 raw hazard set 恰好等於 `fixed` 同 `accepted-source-faithful` 兩個互斥集合之 union、每項有另一位 reviewer 同目前 target／boundary／render seals，release gate 只接受 `open=0`，唔要求為令 raw count 歸零而改原文。Auditor 唔會按語義逐 heading 自動配對題注；ownership 仍要逐頁人工核圖，保存相關 review evidence。`release` profile 將 JSON 報告、transition reviews／disposition ledger、target SHA-256、generator toolchain hash 同 invocation profile 一齊放入 active closure。

## 建立 notation inventory

Notation policy 只授權符號種類；inventory 記目前 annotated bytes 入面每個符號實際喺邊頁、邊行、邊欄。每次題注或內容位置改動後都要重建，唔沿用舊 line locator：

```bash
python3 scripts/build_notation_inventory.py combined-annotated.md \
  --authority notation-authority.json \
  --inventory-profile inventory-profile.json \
  --require-markers \
  --json-report notation-inventory.json
```

Inventory 會封 target／authority／inventory-profile SHA-256、generator toolchain hash、invocation profile、marker range，同通用 `[]`、`［］`、`〔〕`、`〈〉`、`□`、`▭`、`〔?〕`、刪節號對等位置。YAML frontmatter、fenced／inline code、單行 HTML comments、Markdown links／images 同 reference definitions 係格式區域，唔當來源 notation；遮罩保持原 Unicode column。`occurrence_record_count` 係按類別記錄嘅位置 records 數；同一字位可以同時命中整體 marker 同組成 glyph。`counts` 依 `count_units` 計 glyph 或 marker／literal token，所以兩個總數唔一定相等，亦唔應直接將所有 category counts 相加當唯一字數。

新材料嘅特殊符號唔需要改核心程式；用 strict literal-only project profile 擴展：

```json
{
  "schema_version": 1,
  "artifact_type": "ocr-notation-inventory-profile",
  "symbols": [
    {"id": "reference-mark", "literal": "※"},
    {"id": "geta-mark", "literal": "〓"}
  ]
}
```

Profile 只接受唯一 kebab-case ID 同無換行 literal，拒絕 regex、duplicate 同 built-in collision。佢只決定「要 inventory 邊個 literal」，唔證明符號獲凡例授權；authority／notation policy 仍要逐頁核圖。來源明確有星號題名註時，命令先另加 `--include-starred-title-notes`；普通文件預設唔將 Markdown stars 當來源 apparatus。

如果本冊無凡例而引用同版另一冊，authority sidecar 要另外封：兩冊 edition identity、authority PDF／render hash、凡例 scan、適用冊次／範圍，同逐項 glyph mapping。唔可以只寫「同上冊」。

## 計 semantic changed-scan inventory

Release delta 唔可以只睇 Markdown page bytes。先列明全部 normative state channels，例如 segment text、boundary／page facts、heading／list／table 結構、note ownership 同其他會改變 final interpretation 嘅 sidecar；由 baseline 同 current bytes 機械導出每個 channel 嘅 changed scan set，再做 exact union、intersection 同 difference，唔手填 `changed_scans`，亦唔將有 overlap 嘅 counts 直接相加。

每個 partition 至少分開保存：source span／count、initial full-review set、text-changed set、facts-only set、structure-only set、final semantic union、restored-to-baseline／historically-touched set、direct delta-review rows、neighbor／context set同 unique reviewed union。每個 set 用排序後 scan list同 set hash封存，並驗算 coverage、overlap同 uncovered disposition。Page bytes同 baseline相同但 boundary／list／table／note ownership改咗，仍屬 semantic change；曾修改後還原 baseline嘅 scan唔入 final union，但要留喺 historical audit trail同寫明 disposition。`reviewed_count`如果唔講係邊個 set，唔可以用作完成證據。

Boundary 必須有唯一 normative owner。若 page facts、boundary manifest 或其他 sidecar 重複表示同一欄，指定一份做 authority，其餘只可係由 authority deterministic 產生嘅 sealed projection；保存 exact field crosswalk、before／after receipt同 current hashes，禁止兩份各自手改。

## 使用 canonical amendment ledger

獨立覆核後再改 segment，任何 profile 都要由 writer 以外 reviewer 重核 changed scans 同相鄰頁。`structured` 更新 project-local review 記錄、重新凍結、assemble 同 audit 即可；只有 `release` 要將結論放入 durable closure 時，先用以下唯一 canonical schema。唔好創造新 status 字串，亦唔好同時喺多個 nested 欄位複製 current hashes。

```json
{
  "schema_version": 1,
  "artifact_type": "ocr-review-amendment",
  "status": "independent-pass-after-amendment",
  "scan_range": {"start": 10, "end": 12},
  "writer": "/root/writer",
  "independent_verifier": "/root/reviewer",
  "reviewer_differs_from_writer": true,
  "changed_scans": [11],
  "reviewed_scans": [10, 11, 12],
  "current_content_hashes": {
    "segments/p0010-p0012.md": "…",
    "segments/p0010-p0012.boundaries.json": "…"
  },
  "changes": [
    {
      "scan_page": 11,
      "before": "舊字／舊結構",
      "after": "按圖修正後文字／結構",
      "render": {"path": "renders/page-011.png", "sha256": "…"},
      "evidence": {"path": "evidence/change-011.md", "sha256": "…"}
    }
  ],
  "unresolved_count": 0
}
```

相對路徑以 schema 明確指定嘅 validation root 為準；其他 artifact type 亦要聲明係相對 validation root、manifest directory 定 plan directory，唔可以靠當時 CWD 猜。先 canonicalize 路徑，再做 containment、alias、case／symlink／hard-link 檢查。驗證器拒絕 duplicate／unknown fields、非 canonical path、symlink escape、stale hashes、writer 同 verifier 相同、changed/change rows 對唔上、漏核 changed scan 或 immediate in-range neighbor，同任何 unresolved：

```bash
python3 scripts/validate_review_evidence.py \
  --root /path/to/book-evidence \
  /path/to/book-evidence/segments/p0010-p0012.amendment.json
```

同一 scan 可以有多個 change rows；`changed_scans` 仍只列該 scan 一次。`before`／`after` 要具體到足以辨認修改，唔好只寫「fixed typo」。目前 validator 只驗佢哋非空而且不同，唔會自動證明 `before` 出現於舊 baseline、`after` 出現於目前 segment；呢個語義關係要由獨立 reviewer 對頁圖、baseline 同 sealed evidence 核實。跨 segment interface 嘅相鄰頁若唔喺本 ledger `scan_range`，要由另一份 interface review／amendment evidence 封存。

## 封 candidate 同 delta recheck

Writer 每批修改只可用 exact singleton／marker-local before→after operation；完成後同時封 segment、全部 normative facts／structure artifacts、受影響 range同 evidence builder outputs，宣告 `STOP-WRITE`。Independent verifier只核呢組 formal seals，唔用未封存 live bytes補證；逐張重開 changed scans同必要 prev/current/next individual original／full-resolution renders，contact sheet只可導航。

對 bulk paragraph、list、table、boundary或note-ownership重排，另存 structural-operation ledger：每個 exact operation、pre／post hashes、affected interface scans同 direct visual verdict。驗證要由 candidate逆向重建所有 pre-seals，或者由 pre-snapshot順序 replay到 current seals；mechanical delta只可命中聲明 scans／fields，並證明 no collateral。任何 mismatch、額外 drift或新 finding都令 candidate open；指定唯一 writer窄修、產生新 seals，再由另一位 reviewer重核。只有 formal candidate `open=0`先可以成為 final evidence。

## 重建失效證據

Late patch 之後唔好逐個撞 error 先估邊份 sidecar 過期。先對目前 `release-plan.json` 跑 stale planner：

```bash
python3 scripts/plan_release_rebuild.py release-plan.json \
  --json-report release-rebuild.json
```

Planner 先找檔案 missing／full SHA mismatch 嘅 direct stale artifacts，再沿 reverse `depends_on` 將所有 dependents 標成 transitive stale，並輸出 dependency-first rebuild order。Missing active path 只有仍可由至少一條 `discovery.globs` 命中先係可重建 stale；若重建後必然變成 discovery scope 外 artifact，就直接 invalid。`--json-report` 會拒絕 plan、任何 declared artifact、planner／validator 嘅 symlink、hard-link 或 case alias，並喺寫檔前重驗 plan hash。Exit status：clean 為 0、發現 stale 為 1、plan/schema/path/graph 本身無法安全分析為 2。

Rebuild order 只可靠到 release plan 真正表達嘅依賴。唔好用一條 catch-all `release-evidence` 邊將 combined annotated 直接連晒所有 artifacts；至少表達以下方向：

1. segment／boundary／render／authority；
2. writer／independent review／amendment；
3. title-note、notation、TOC／variant／invariant audits；
4. review manifest、ordered assembly、combined annotated；
5. release plan validation、finalize receipt、final audit。

Release plan 自己嘅 validator 證明 classification、hash、closure 同 DAG；佢唔理解任意 opaque sidecar 內部 status。Review amendment、title-note、notation、TOC semantic challenge 等仍要先各自用專用 validator／auditor產生 current pass evidence，再列入 plan。

Planner 亦唔會憑舊報告內一個 tool hash 猜「而家安裝咗新版工具」。每個會左右 release 結論嘅 generator／shared parser，要將目前 bytes 複製成 project-local sealed tool snapshot，列為 active artifact；衍生報告要用 typed `depends_on` 指向該 snapshot。咁 tool/schema 升級時，snapshot hash drift 先會成為 direct stale，再傳播到報告同下游。報告內 `tool`／`tool_dependencies` 係 provenance；plan dependency 先係 freshness gate。

## 重用另一冊或另一 release 嘅證據

兩冊有 byte-identical scan／render 時可以重用已完成 visual evidence，但要建立 cross-release import sidecar，唔好淨係抄另一冊 path：

- upstream source PDF identity、release plan／receipt path 同 SHA-256；
- 被重用 artifact path、type、full SHA-256 同 upstream validation status；
- source scan → target scan marker mapping；
- 每一對 render SHA-256 exact 相同嘅證明；
- reviewer identity、原 review coverage、目前 target scope；
- projection baseline hash、之後每一份 amendment ledger，同目前 artifact hash嘅連續 chain。

將 import sidecar、必要 upstream snapshot／receipt 同 target projection 一齊列入目前 release plan active closure。只係題名相同、OCR 相同或圖像肉眼相似都唔足以重用；render bytes唔同就重新視覺覆核。

## 分清 annotated 同 final 命令

- Annotated audit／finalizer dry-run 先使用 `--expected-count`、`--skip-pages` 同 `--boundary-manifest`。
- `structured` final audit 唔傳 expected-count／skip-pages，但會傳 `--annotated-source` 同 `--boundary-manifest` 即場 exact 重建；marker coverage 由同一 annotated source 加 page manifest 驗證。
- `release` final audit 唔再傳 annotated-stage options；marker coverage 同 boundary replay 由 schema v2 receipt、sealed page manifest 同 annotated input驗證。
- Dry-run finalizer唔傳 `--release-plan`／`--receipt`；先獨立驗 plan。真正 `--write` 時兩個選項必須一齊提供。
- 如果 final mode 傳咗 annotated-only option，warning 係命令 profile 錯，唔係多跑一層驗證；移除 option 後按標準 final command重跑。
