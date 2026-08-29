# 來源例外同可重播封關

用呢份 reference 處理來源校勘符號、原書本身嘅不配對標點、平行篇章互校，同埋 `release` profile 嘅 assemble／交付。呢啲規則唔係每份 PDF 嘅預設負擔，亦唔係用嚟代替逐頁視覺校對。

開工先寫 applicability matrix：`basic`／`structured`／`release` profile，同 notation、題名註、長目錄、表格、外部復原等 feature gate 係 `enabled` 定 `not-applicable`。只有實際啟用嘅 gate 先需要相應 policy、ledger、inventory 或 receipt；唔好建立空 artifact 冒充完整。

## 目錄

- [先讀本書凡例](#先讀本書凡例)
- [保存來源異常](#保存來源異常)
- [以兩段 audit 建立來源例外](#以兩段-audit-建立來源例外)
- [傳播已確認錯字](#傳播已確認錯字)
- [用平行篇章產生候選](#用平行篇章產生候選)
- [封關長目錄對賬](#封關長目錄對賬)
- [凍結 review 同 assembly](#凍結-review-同-assembly)
- [建立 release plan 同證據生命週期](#建立-release-plan-同證據生命週期)
- [遷移舊 sidecar](#遷移舊-sidecar)
- [寫入同驗證 hash receipt](#寫入同驗證-hash-receipt)
- [定義 canonical output 同 current completion](#定義-canonical-output-同-current-completion)
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

將「凡例嘅完整文字解釋」同「audit 需要嘅逐符號授權」分開保存：前者可以係詳細 inventory，後者用嚴格 `notation-policy.json`。每個 key 都只授權自己嗰種符號；例如凡例明確證明 `□` 同 `〔?〕` 係來源標記，可以寫：

```json
{
  "white_square": {
    "meaning": "source-illegible",
    "scan_page": 7,
    "note": "凡例明定每個空方框代表一個不可辨字。"
  },
  "explicit_uncertain_marker": {
    "meaning": "source-uncertain",
    "scan_page": 7,
    "note": "凡例明定六角括號內問號表示編者存疑。"
  }
}
```

支援嘅 key 係 `ascii_square_brackets`、`fullwidth_square_brackets`、`lenticular_brackets`、`angle_brackets`、`white_square`、`rectangle` 同 `explicit_uncertain_marker`；每條 record 必須恰好有 `meaning`、`scan_page`、`note`。ASCII `[]` 只接受 `source-editorial`；其他 key 接受嘅 `source-apparatus`、`source-editorial`、`source-uncertain`、`source-damage`、`source-illegible` 或 `source-omission` 組合由 audit schema 限定。未聲明嘅 `□`、`▭`、`〔?〕` 會 fail closed；聲明其中一種唔會順便放行另一種。

只喺 final audit 使用呢份 policy；annotated mode 唔會用 policy 豁免命中，傳入 policy 亦會報 `notation-policy-requires-final`。已逐頁證實嘅來源符號可以保留並記做 known annotated-stage issue，但 annotated gate 仍然唔可以聲稱 clean；先清走真正 OCR confidence markup，再由 final mode 按 exact policy 接受來源 notation。Policy 係全書聲明，唔適合一份仍混用 source brackets 同未處理 confidence brackets 嘅稿。ASCII 舊版單-key schema 仍然有效；詳細凡例 inventory 若有其他符號，可以另存 sidecar，兩份都列入 release plan。

```bash
python3 scripts/audit_ocr_markdown.py final.md --mode final \
  --notation-policy notation-policy.json
```

未有可靠凡例證據就唔好建立 policy。Residual scan 有命中唔代表一定要刪；逐項分類後先決定。

內容位置凍結後，用 `build_notation_inventory.py` 建立逐 scan／line／column inventory，並封目前 annotated target、authority 同可選 project inventory-profile hash。新見到嘅版次符號用 literal-only profile 加 ID，唔改核心 detector；只有來源確有星號題名註先加 `--include-starred-title-notes`。題注搬位或其他 patch 令 line locator 改變時要重建；inventory 只記位置，唔取代凡例授權。跨冊引用另一冊凡例嘅 edition identity／render evidence 見 `review-evidence.md`。

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

## 以兩段 audit 建立來源例外

來源 delimiter exception 要由 marker-free provisional candidate 建立，唔可以先寫白名單再搵位置迎合。第一段用 final mode 跑 pre-exception audit，但唔傳 source exceptions；errors 只可係預先界定嘅 delimiter codes，warnings 同任何其他 error 都要先修正。逐個 issue保存 exact line／Unicode column／delimiter／text，再投影返 annotated marker provenance同唯一 scan。

第二位 reviewer要逐張打開對應 individual original／full-resolution render，確認每個 issue真係 source-as-printed，並封 render path／hash／bytes／dimensions同逐 scan/code/delimiter distribution。之後先由 strict config deterministic產生 current exact exceptions，再跑 final audit，要求 errors／warnings／unresolved全部為零而 accepted set exact等於已核 visual set。Candidate line、text、projection、audit tool、boundary或render任何一項漂移，都要重跑 pre-exception inventory同 visual binding；舊 exception唔可以靠相似字串移位繼承。

## 傳播已確認錯字

獨立視覺 sweep 經常喺冇 `[]` 嘅高信心文字先發現人名、官名同固定搭配錯形。每次確認一處後，記錄簡短 correction ledger：

```json
{"wrong":"錯形","verified":"核圖字形","scan_page":8,"evidence":"scan 8 crop"}
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

先建立 `reconciliation_scope`，唔好預設整份 TOC 同目前正文係同一 universe。Scope 至少記 stable id、交付／卷冊範圍、TOC partitions、body partitions 同每個 partition 嘅 mapping cardinality。全部 TOC items 必須恰好分入一個互斥 partition；in-scope item 才參與本次 body mapping，out-of-scope item 亦要逐項記具體 disposition 同 scan evidence。跨卷、跨冊、只交付部分正文或目錄另收附錄時，唔可以用全目錄 count 同局部 body heading count 比較，亦唔可以將 scope 外條目靜靜刪走。

正式 reconciliation 前先完成兩個 gate：

1. 按每個 TOC scan 建立 terminal printed-reference inventory，一行一項記 TOC scan、欄／閱讀次序、index、原樣末端參照、render SHA-256 同抽取證據；漏抽、誤併、誤拆、跨欄錯序另有 recovery ledger。
2. 由同抽取者唔同嘅 reviewer，直接對頁圖逐行覆核 inventory 內 100% 末端參照。獨立 audit 要保存 reviewer、完整 index／scan coverage、逐行結果同 report SHA-256；唔可以只核 OCR 分歧或頁碼差額。

另外分開建立：TOC item ledger、TOC group ledger、body heading inventory。Group ledger 記分組題名、開始／結束 item、TOC scan 同正文對應，分組本身唔計做 item。Heading inventory 對每個正文 heading 明寫 `toc_eligible: true/false`；所有 `false` 都要有具體 exclusion reason，唔可以靠 heading level 或程式 hard-code 靜靜略過。

一行一項嘅 reconciliation ledger 至少記錄 TOC index、TOC scan／原文／原樣印刷參照、body scan／heading、mapping、題名 disposition、頁碼 disposition 同圖像證據。狀態要分清：

- `exact`／`fixed`：兩邊一致，或者真正 OCR 錯已按圖修正；
- `source-verified-apparatus-variant`：只差圈號、註號、重複來源標籤等例行裝置；
- `source-verified-semantic-variant`：涉及實質字詞、名稱、日期或數字而兩邊原書確實不同；
- `source-verified-reordered`：原書次序確實不同；
- `unresolved`：仍未有足夠圖像結論，封關必須為零。

所有 semantic／high-risk variant 同每一個頁碼差額或 reorder 必須經獨立 challenge pass：第二位 reviewer 同時睇 TOC 同 body 頁圖，防止將中間漏字、近形字或錯 mapping 誤封成來源異文。Routine apparatus omission 可以另批處理，但佢嘅 classification 規則、命中行同 reviewer evidence 仍要保存，唔可以順帶豁免人名、官職、地名、日期或數字。

頁碼要用細分類 disposition，至少分得出 `page-exact`、`fixed-ocr-reference`、`source-printed-ref-differs-from-body-start`、`source-printed-ref-maps-to-body-range`、`source-verified-reordered` 同 `unresolved`；若項目需要另一分類，要寫清楚實際原因。目錄圖上印刷參照永遠保留喺 transcription 欄，body start／range 另存 mapping 欄；唔好將目錄頁碼改成正文起頁以製造一致。

封關同時驗證：terminal-reference inventory 同獨立 reference audit 覆蓋全 TOC，所有 items 喺 partition ledger 恰好出現一次；每個要求一對一 mapping 嘅 in-scope partition，其 TOC item、TOC-eligible body heading 同 reconciliation rows 嘅 count／次序完全一致。TOC group 同正文 group 另行對賬；每邊每項只映射一次；scope 外每項有關閉 disposition；原次序、原參照、所有 recovery／override 同 exclusion 都有 scan evidence。任何 scope、heading、日期、marker boundary、recovery 或 override 後續修改，都要重跑受影響 extraction，再重跑完整 reconciliation 同 challenge pass。

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

第 5 步用 `assemble_ocr_segments.py`，唔係只比較三批各自獨立嘅 hash。Strict manifest root 必須恰好有 `schema_version: 1`、`separator_utf8`、總 `scan_start`／`scan_end`、`page_manifest: {path, sha256}` 同 ordered `segments`；每段亦只可有 `path`、`sha256`、`scan_start`、`scan_end`。Separator 只可係空字串或全由 LF newline characters 組成，唔准文字、CR、tab 或 space。Assembler 驗安全相對路徑、full hashes、ranges 無縫無重疊，再將每段實際 canonical markers 同 sealed page manifest 喺該 range 內嘅 `marker_expected` pages exact 比較；之後按 separator 重播 bytes，再驗 assembled marker sequence exact 等於 ordered segment inventory，同現有 combined annotated 做 byte-for-byte 比較。首尾語義／metadata owner 仍要由 assembly review sidecar 人工或另有 semantic gate 核實，唔好聲稱 strict parser 有驗。Write receipt 會保存 tool hash、invocation profile、manifest、page manifest、segments、separator/ranges 同 output hash。

任何 segment、ordered manifest、combined annotated、boundary manifest、finalizer input 或 final Markdown 嘅後續 patch，都令之前嘅 assembly receipt 同 final QA 失效。唔好只重跑最後一個 scanner；要由受影響嘅 assembly 步驟重新開始。

如果 patch 發生喺獨立覆核之後，舊 `independent-checked` 唔會因為更新 hash 自動繼承。二選一：由 writer 以外嘅 reviewer 重新覆核所有受影響 scan（跨頁 join、heading、list、table 或註腳要連相鄰頁）；或者建立具體而 sealed 嘅 amendment ledger，逐項保存 `before`、`after`、`scan_page`、目前 `render_sha256`、`independent_verifier`、證據 artifact 路徑同 `evidence_sha256`。Validator 只驗 before／after 非空而相異；佢哋同 baseline／current 內容嘅關係仍要由 reviewer 同 sealed evidence 證明。Amendment ledger 要記自身 hash、列入 durable review evidence，同受影響 segment 一齊重新凍結；缺任何欄位都唔可以保留 `independent-checked`。

新 ledger 用唯一 `ocr-review-amendment` v1 schema同 `independent-pass-after-amendment` status，並先通過 `validate_review_evidence.py`。Current hashes 只放 canonical `current_content_hashes`；唔喺 auditor、inline recheck同外部 amendment重複保存多份互相漂移嘅 current seal。完整 schema見 `review-evidence.md`。

## 建立 release plan 同證據生命週期

完成 replay、開始 finalizer 前建立 strict v1 `release-plan.json`。Root 必須恰好有 `schema_version`、`discovery`、`release_roots`、`artifacts`，按下述 TOC evidence 規則加 `reconciliation_scope`；unknown 或 duplicate JSON keys 會失敗。`discovery` 只有非空 `globs`，全部 pattern 同 artifact paths 都係 plan 目錄以下嘅安全相對路徑。每種 schema 要明寫相對路徑 base（例如 validation root、manifest directory或 plan directory）；先按該 base canonicalize，再做 containment、alias、case／symlink／hard-link檢查，唔可以依賴 caller CWD。

每個 artifact 必須恰好提供 `id`、`type`、`path`、`classification`、`lifecycle`、`validation`、`sha256`、`depends_on`；按需要先加 `exclusion_reason`、`superseded_by`、`singleton_role`。`id`／`type`／role 用 kebab case，`sha256` 永遠係該 artifact 完整 bytes 嘅 64 位小寫 digest，唔使用 `include` boolean、`hashes` array 或 `file-sha256` 欄名。

- `classification: include` 只接受 `lifecycle: active`、`validation: pass`，並禁止 `exclusion_reason`／`superseded_by`。
- `classification: exclude` 只接受 `historical` 或 `superseded`，必須有 `exclusion_reason`；`superseded` 必須再以 `superseded_by` 指向 active included replacement，其他 lifecycle 禁止呢個欄。
- Optional `singleton_role` 用嚟聲明某種現行證據只可有一份；任何兩個 active included artifacts 重複同一 role 都失敗。Pre-final plan 唔包含 final output；post-final bundle 如用 `final-output` role，release roots 亦最多一份。
- `release_roots` 必須係 active included IDs；沿 `depends_on` 計出嘅 closure 要恰好等於全部 active included artifacts。Active edge 只可指向 active included target；orphan、自依賴、dependency cycle 同獨立 supersession cycle 全部 fail closed。

每條 `depends_on` 係 `{target, relation, hash}`，`relation` 用 kebab case，同一 artifact 重複 target／relation edge 會失敗。`hash` 只接受兩種 exact contract：`{"mode":"full","sha256":"…"}`，或者 `{"mode":"canonical-subset","sha256":"…","pointers":["/…"]}`。Full mode 禁止 `pointers`；subset pointers 必須係非空、唯一、互不為 ancestor／descendant 嘅 RFC 6901 paths。Validator 將 pointers 排序，抽成 `{"pointer":…,"value":…}` array，再用 UTF-8、`ensure_ascii=false`、sorted object keys、compact separators、無尾 newline 計 SHA-256。Subset hash 只封指定 projection；artifact 自己嘅 `sha256` 仍然封完整檔案。

Discovery 要作 canonical inventory，而唔係臨尾 recursive glob 猜依賴。先將 pre-final inputs／historical candidates 放入固定 evidence roots，避開之後先生成嘅 final／receipt／audit output 目錄；用窄而穩定嘅 globs，按 path 排序列出全部 regular files。逐份決定 current `include + active + pass`，或者 `exclude + historical/superseded + reason`，計 full SHA，再加 typed dependencies、singleton roles 同 roots。Plan 自身係唯一 bootstrap exemption；任何 discovered file 未分類、artifact 唔被 glob 發現、path alias 或 active artifact 唔可由 roots 到達都失敗。Final output／receipt path 唔可以 alias plan 或任何 classified artifact；如果檔案未存在但生成後會命中 pre-final discovery glob，finalizer 亦會喺寫入前拒絕。每次 input 增刪／改 hash，都重建 inventory 並重跑：

```bash
python3 scripts/validate_release_evidence.py release-plan.json
```

Dependency edge 要表達真實導出方向，唔用 combined annotated 加同名 catch-all relation 直連晒所有 artifacts。上游改動後可先跑 `plan_release_rebuild.py`；佢會將 direct stale 沿 reverse dependencies 傳播同列重建次序，但結果只可靠到 plan 已寫明嘅依賴。Release validator 亦唔理解 opaque sidecar 內部 status，專用 review／題注／notation／TOC semantic gate仍要獨立通過。

只要 active included artifacts 出現以下五種專用 type 任一種，就必須有 `reconciliation_scope`；真正完全冇呢類 TOC evidence 嘅 plan 先可以省略。Scope 必須恰好有 `id`、`unit`、`source_count`、`included_count`、`evidence_ids`、`partitions`；`id`／`unit` 用 kebab case，source count 為正整數，included count 介乎 0 同 source count。`evidence_ids` 五個 key 同 target type 固定如下：

| key | artifact `type` |
|---|---|
| `toc_item_ledger` | `toc-item-ledger` |
| `body_heading_inventory` | `body-heading-inventory` |
| `reconciliation_ledger` | `toc-body-reconciliation-ledger` |
| `variant_challenge_ledger` | `variant-challenge-ledger` |
| `invariants_qa` | `invariants-qa` |

五個 targets 都要 active included、可由 roots 到達而且 type 相符。每個 partition required fields 係 `id`、`start`、`end`、`disposition`，optional 只係 `reconciled_count`、`reason`；要由 1 至 `source_count` 無縫、無重疊覆蓋。In-scope `reconciled_count` 等於 span；out-of-scope 要有 `reason`，如有 count 只可為 0；所有 in-scope spans 合計等於 `included_count`。呢個 validator 只證明 arithmetic 同 sealed evidence binding，唔讀 ledger row/status、reviewer coverage 或 challenge 語義；仍須另跑 semantic invariant／challenge 工具，或者逐項人工審核並封存報告，否則唔可以聲稱 reconciliation 完成。

生成同封存分兩層。Pre-final `release-plan.json` 只收 finalizer 當刻已存在嘅 combined annotated、boundary、segments、review／TOC evidence、notation／exception 同其他 active inputs；唔可以包含待生成嘅 final、receipt 或 post-final audit。Finalizer 會要求 annotated target 同 boundary manifest 各自恰好係一份 active included artifact，並拒絕 output／receipt alias plan 或任何 classified artifact、以及生成後新命中 pre-final discovery globs。Schema v2 receipt 封存 plan 同全部 active inputs 之後，final audit JSON 再封 final、receipt、interpretation gates 同 audit tool hash。如要完整成品 inventory，另建 post-final `release-bundle-plan.json` 封 final／receipt／audit report 等第二層 artifacts；只獨立驗證，永不回餵舊 finalizer，避免 reciprocal hash cycle。

## 遷移舊 sidecar

舊 rich page/review manifest、notation inventory、provisional QA 或項目自訂 ledger 係 migration input，唔係 strict parser 可以靠 unknown fields 猜測嘅新 schema。先凍結舊檔 full SHA-256，再產生只含目前 schema 欄位嘅 projection，同一份 sealed crosswalk：逐欄記來源 path／field、目標 artifact／field、轉換規則、保留／捨棄理由同核對者。舊檔喺 discovery inventory 分類為 `historical` 或 `superseded`，新 projection 分類為 active，並用 typed dependency hash 封住來源關係；之後重跑 validator、assembler／audit 同 receipt。

Schema migration 本身唔等於 OCR 內容失效。只要舊逐頁視覺 evidence、render hashes、review coverage 同目前文字仍然可驗，完成 projection／crosswalk 同重新封存即可，毋須重做已驗證 OCR；只有 hash、coverage、語義結論或受影響頁無法對返時，先重做相應 review／challenge gate。Legacy schema v1 assembly receipt 仍可讀作診斷，但因為冇唯一 `release-plan` role，final audit 必報 `assembly-receipt-release-plan-missing`；要由目前有效 plan 重新 finalize 產生 v2 receipt，唔可以只改 receipt version number。

## 寫入同驗證 hash receipt

長書 finalizer 寫 `--receipt` 時必須同時帶 `--release-plan`；兩個 option 互相依賴。Finalizer 會即場重跑 strict validator，凍結同重驗 plan／classified hashes snapshot，再自動封存 complete active included set。Receipt schema v2 要恰好一個 `annotated`、`boundary-manifest` 同 `release-plan` role；其他 active artifacts 依 plan 次序寫成 `upstream`，annotated／boundary 唔重複。毋須手寫 `--upstream`；呢個 option 只係可選 membership assertion，亦只接受已屬 active closure 嘅路徑，唔可以加 unclassified evidence：

```bash
python3 scripts/finalize_page_markers.py combined-annotated.md \
  --expected-count "$EXPECTED_SCAN_COUNT" \
  --boundary-manifest boundaries.json \
  --output final.md \
  --receipt assembly-receipt.json \
  --release-plan release-plan.json \
  --write
```

Receipt 以 SHA-256 記錄 release plan 同佢導出嘅 ordered active inputs、combined annotated、boundary manifest 同 final output；唔用 mtime。Final audit 會重跑目前 release plan validator、重算全部 classified hashes、確認 annotated／boundary 各自仍恰好係 active included artifact，並要求 receipt `upstream` paths exact 等於 plan ordered active closure 扣除 annotated／boundary；然後用目前 annotated 同 boundary contract 即場重建 expected final，再同 final 做 byte-for-byte 比較。任何 plan、classification、closure、input 或 final 漂移都要 fail。Final audit 必須帶同一份 v2 receipt：

```bash
python3 scripts/audit_ocr_markdown.py final.md --mode final \
  --notation-policy notation-policy.json \
  --source-exceptions source-exceptions.json \
  --page-manifest page-manifest.json \
  --source-pdf /current/path/source.pdf \
  --assembly-receipt assembly-receipt.json \
  --json-report final-audit.json
```

長書第一次 release review 用預設 verbose 輸出；全部 issue context 已核實後，重跑先可加 `--summary-only`。摘要模式只抑制逐項 context，保留 deterministic issue counts、finalizer write／receipt 摘要同原有 exit code；任何非零 count 都要回到 verbose 模式定位，唔係豁免 gate。

凡 final audit 會讀嘅 interpretation／provenance 檔，包括 notation policy、source exceptions 同 page manifest，都要列為 pre-final plan 嘅 active included artifacts；其他 release decision sidecars，例如 TOC inventories／ledgers、independent review、variant challenge、reconciliation、late amendment、correction、invariant 同外部 evidence 亦一樣。Historical／excluded artifacts 留喺 plan 作 disposition，但唔入 active receipt closure。若 delimiter issue 只喺第一份 provisional final 先浮出，先核圖建立 exception，再重建／驗 plan、finalize 同 receipt；唔可以沿用早過 exception 嘅 plan 或 receipt。

`final-audit.json` 會封 final target、receipt、interpretation gate hashes 同 `audit_ocr_markdown.py` 自身 hash；佢係 post-final output，唔加入 pre-final plan。需要完整交付 inventory 時，完成後另建並驗證 `release-bundle-plan.json`，但唔再傳入 finalizer。

`assembly-receipt-release-plan-missing` 表示仍係 legacy v1；`assembly-receipt-release-plan-invalid` 表示目前 plan／classified evidence 已經過唔到 validator；`assembly-receipt-annotated-not-active`／`assembly-receipt-boundary-not-active` 表示專用 input 唔再恰好屬於 active set；`assembly-receipt-release-plan-closure-mismatch` 表示 receipt upstreams 唔再 exact 等於 plan closure；`assembly-receipt-generated-path-classified` 表示 final／receipt alias 咗任何 classified artifact。`assembly-input-changed` 表示 late patch 後未重新 finalize；`assembly-output-changed` 表示 final 喺 receipt 之後被改；`assembly-final-stale` 表示目前 annotated 加 boundary contract 重建唔出目前 final。Receipt 同重建可以證明目前 plan closure 同 annotated-to-final 關係，但唔會自行證明 combined annotated 當初語義上按正確 segment 次序拼接；所以 ordered manifest、assembled marker replay 同 assembly semantic review 仍然係必要 gate。

## 定義 canonical output 同 current completion

- Canonical delivered Markdown 由 schema v2 receipt `output.path` 指定，唔由慣例檔名決定。直接寫 public path係合法；唔要求每個 project都有 `release/final.md`。
- 如果 internal同 public兩份都宣稱 final，兩者 live bytes／SHA必須一致。每份各有自己嘅 schema v2 receipt同 final audit，或者由 post-final bundle封 canonical target、secondary target、parity result同兩者tracking state；因 output path唔同，receipt hashes本身可以唔同。
- Pre-final root summary `pass`只表示 candidate readiness，必須聲明 `scope: pre-final`；佢唔證明 final/public target已寫入或仍 current。可選 post-final completion summary要封 canonical target(s)、receipts、audits、parity同 tracked／untracked／ignored／repo外狀態，但 summary只係索引，authority仍係 receipt＋audit＋live hashes。
- 回答既有 release「而家係咪完成」時做 read-only current-seal check：由 receipt解析 target，重算 target／receipt／plan／source hashes，重跑 plan validator，要求 rebuild planner direct／transitive stale為零，再以 check-only／stdout或 discovery scope外暫存重跑 final audit。Persisted pass報告唔可以代替呢次 live check；多冊完成係每冊 current closure嘅 conjunction。
- OCR coverage、逐頁視覺校對、deterministic release同 repo／publish delivery係四條獨立軸。Untracked final可以係本機內容同 release complete，但未 repo-integrated；用 `git status`加 `git check-ignore -v`如實報告，唔自動 stage、commit或publish。

## 驗證來源 PDF 同 renders

`source_pdf` 路徑可以因檔案搬位而失效，來源身份要靠 SHA-256。Page manifest 要保存 `source_sha256`、`expected_scan_pages`，以及每頁 `scan_page`、`render_file`、`render_sha256`。每頁預設 `marker_expected: true`；只有空白、重複等已逐頁確認而刻意唔設 Markdown marker 嘅 scan，先明寫 `"marker_expected": false`。封關時用目前實際 PDF 路徑重算來源 hash，再逐一重算所有 render；唔可以只 hash manifest 本身。

Page manifest 呢一關只證明 source/render、scan sequence 同 marker facts。Boundary labels 由獨立 `boundaries.json` 封存；writer／reviewer 身份、獨立 coverage、status 同 report hash 由 review ledger／sidecar 封存。三者一齊列入 release plan，但 page manifest 嘅附加 `visual_status` 唔可以取代 durable review evidence，亦毋須為新版 gate 將 reviewer 狀態硬寫入 page manifest。

封關命令：

```bash
python3 scripts/audit_ocr_markdown.py final.md --mode final \
  --page-manifest page-manifest.json \
  --source-pdf /current/path/source.pdf
```

Audit 要求 scan sequence 恰好係 `1..expected_scan_pages`，每個 render 唯一、存在而且 hash 相符。帶 receipt 做 final audit 時，亦會將 sealed annotated markers 同 sealed page manifest 對賬；缺 marker 只可由該頁已封存嘅 `marker_expected: false` 解釋，唔可以靠漏傳 `--expected-count` 避過。搬動 PDF 時可以用新路徑，只要內容 hash 同已封入 receipt 嘅 manifest 一致；唔好為咗令路徑存在而靜靜換另一份 PDF。Render set 由 manifest 內逐頁 hash 封存，final audit 每次都重驗目前檔案。

## 封關清單

先按 applicability matrix 篩出目前 profile 真正啟用嘅項目；所有核心項目同所有已啟用 feature gate 成立，先可以交付。標明 `not-applicable` 嘅項目唔需要建立空 sidecar，但理由要同文件實際結構一致：

- 每個非空白 scan page 已有 `worker-checked`，要求獨立覆核嘅範圍亦有 `independent-checked`；
- 聲稱逐頁 review嘅每個 scan都以 individual original／full-resolution render作文字依據，render hash同 current page manifest一致；contact sheet／thumbnail只作導航，唔計直接 coverage；
- independent reviewer 同 writer 身份分開，覆核 range、ledger／report hash 同逐頁 status 無降級；
- 所有 agent final report 已收齊，無 active writer／auditor；
- 高信心 visual sweep、correction propagation 同平行篇章候選已處理；
- notation policy 有凡例證據，或者所有 ASCII bracket 命中已逐項分類；
- delimiter issue 已修正或由 current exact-location source exception 承認；
- 長目錄已定義 reconciliation scopes／partitions；全部 TOC items 恰好入一個 partition，in-scope mappings 同 out-of-scope dispositions 都已關閉，無殘留泛稱 `review`／`unresolved`；
- 每個 TOC scan 嘅末端印刷參照已 inventory、復原漏抽並由獨立 reviewer 100% 覆核；item／group ledger 分開，所有非 TOC-eligible heading 有 exclusion reason；
- 所有 semantic／high-risk variant、頁碼差額同 reorder 已通過獨立 challenge，頁碼 mapping 無改寫目錄原樣參照；
- 獨立覆核後嘅 late patch 已重新覆核受影響頁，或者由欄位完整而已封存嘅 amendment ledger 覆蓋；
- formal candidate已STOP-WRITE；bulk text／structure delta可逆重建pre-seals，機械changed scans／fields同聲明集合exact，無collateral，independent verifier open0；
- canonical amendment ledger 已通過 `validate_review_evidence.py`，無自創 pass status、重複 current seal 或 stale content／render／evidence hash；
- semantic changed inventory由 text同全部 normative facts／structure channels機械導出；final union、facts-only、structure-only、restored、delta rows同neighbor/context sets各自可驗，無用重疊counts冒充coverage；
- 如有星號題名註，逐 item 核對 ownership，`audit_title_notes.py` 無 missing／orphan／未覆核 cross-page hazard；raw heuristic hazards同 fixed／accepted-source-faithful dispositions exact set-equal，release open0。任何 accepted transition 都由目前 target／boundary hash 綁定嘅 exact visual review 放行。若有來源 notation，inventory 同目前 annotated／authority／inventory-profile hash 相符；
- ordered manifest 已由目前 segments deterministic replay，重建結果同 combined annotated byte-for-byte 相同；strict range／marker gate 通過，首尾句／metadata ownership 亦由另行 semantic review 核實；
- release plan 已 inventory 每份 candidate artifact；active dependency closure 完整、hash typed 而相符、DAG 無循環，亦無 orphan active artifact；
- 所有 active artifacts 都係 validation pass、非 provisional、非 failing；historical／superseded artifacts 有 exclusion reason，replacement 唯一而有效；
- marker、boundary、註腳、tight list、table 欄數、heading、soft break、EOF whitespace 同 residual audit 通過；
- 目前 source PDF、完整 scan sequence、annotated marker coverage 同每個 render hash 都同已封存 page manifest 相符；
- receipt 係 schema v2，恰好一個 `release-plan` role；legacy v1 已用目前有效 plan 重新 finalize，唔係手改版本號；
- receipt `output.path`已定義 canonical target；若同時宣稱 internal／public finals，target bytes／SHA parity同各自receipt／audit（或post-final bundle）已驗；
- final audit 已重驗目前 plan／classified hashes；receipt ordered upstreams exact 等於 active closure，annotated／boundary／final hashes 同重建結果亦相符；
- output／receipt 無 alias plan／任何 classified artifact，亦無新命中 pre-final discovery globs；
- final audit JSON 已封 final／receipt／interpretation gates／audit tool hash；如有 post-final bundle plan，佢只作第二層 inventory，無回餵 finalizer；
- 最後一次 patch 之後已重新跑所有相關 gate；
- content-changing sweep全部喺finalize前完成；post-final檢查只read-only，若發現問題已重開assembly／plan／receipt／audit cycle；
- 若聲稱現有release仍current，live target／receipt／plan／source hashes、plan validator、stale planner 0同check-only final audit已重驗；
- final 回覆清楚區分逐頁視覺核實、外部復原、source-as-printed exception、仍未解決疑問，同canonical target嘅tracked／untracked／ignored／repo外狀態。
