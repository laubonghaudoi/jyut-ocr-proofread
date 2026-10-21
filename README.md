# 粵文 OCR 校對

呢個係一份跨 Codex／Claude Code 嘅 Agent Skill，亦包裝成 Codex plugin，用嚟按同名 PDF 校對歷史中文 OCR Markdown；流程基於 `jyut-ocr` repo 嘅校對守則。

plugin 會安裝一個 skill：`jyut-ocr-proofread`。同一個 canonical skill directory 可以由 Codex 同 Claude Code 共用；skill body 同 bundled OCR rules 都用粵文寫，令 agent 處理 OCR 校對時直接讀到同 repo 指示一致嘅工作語言。

## 佢會做咩

- 以 PDF 圖像作最終依據；
- 先揀 `basic`、`structured` 或 `release` profile，再按文件實際特徵啟用 notation、題名註、長目錄、表格等專項 gate；
- 保留 PDF 原有字形、異體字、舊字形同舊式標點；
- 查 PDF 後先刪頁眉、頁腳、書脊、承印資訊、OCR notice 等頁面家具；
- 優先處理低信心括號同語境唔通嘅高信心 OCR 錯字；
- 先讀本書凡例，分清 OCR confidence bracket 同原書訂正／增補／缺損符號；
- 按 PDF 版面重整 heading、段落、bullet list、table；
- 新 OCR 或補漏時先用視覺識別，GJ.cool 只作背景並行或極短時間輔助；
- Apple Vision 底稿可以用最多八個本機 process 並行，安全續跑並保存逐頁 hash／run report；
- 單一長書可以按頁段寫入獨立 segment，再由主 agent 驗證交界同統一合併；
- Codex／Claude Code 可以用同一份角色 contract 分派 segment writer 同 read-only visual auditor；LLM worker 依平台有效 model-resolution chain 揀模型，Apple Vision process 本身無 LLM model；
- 分辨 PDF scan page 同書內 printed page，記錄旋轉、spread、重複掃描同真正缺頁；
- 分開對賬長目錄條目、分組、TOC-eligible 正文題名、原書頁碼差異同高風險異文；合訂本可明定分冊 scope，而範圍外項目仍有 disposition；
- 人工核對題名尾端星號同來源題注 ownership，另用 deterministic auditor 檢查 item-level arity 同跨頁 hazard；
- `structured` 工作用 project-local review 記錄同 annotated＋boundary exact reconstruction 封關；只有要求可重播交付時，先用 canonical late-amendment ledger 同 dependency graph 重建 stale evidence；
- 用 bundled scripts 重播 ordered segments，並檢查 page marker、跨頁黐行、tight list、table 欄數、成對標點、Markdown 結構、final residue；`release` profile 再驗完整 evidence closure 同 stale assembly；
- 長文或 final pass 時載入更完整嘅粵文 `jyut-ocr` 校對 reference。

## 泛化原則

- 書名、頁碼、人名、地名、頁面家具、單一來源句式同版次符號全部留喺 project-local policy／ledger／profile，唔 hard-code 入 plugin。
- 一本書出現一次嘅問題先記做 candidate lesson；只有跨互不相關材料重現，或者揭示格式無關嘅資料遺失、fail-open、hash／provenance／安全問題，先更新核心。
- 核心 regression tests 只用最小 synthetic fixtures；實書可以做外部 forward test，但唔將書名、實頁碼或大段原文寫入技能規則。

## Unreleased 本機更新

- 新增平台中立 `subagent-orchestration.md`，將 writer assignment、互斥寫入、candidate seal、read-only independent audit、late-patch 重核同最多八個 LLM child 嘅規則集中成一份 contract。
- 新增 Codex TOML 同 Claude Code Markdown adapters。Codex 省略 model fields；Claude 明寫 `model: inherit`，兩邊都唔鎖死會過時嘅 model ID。
- 新增 bounded Apple Vision runner；`--workers auto` 最多用八個本機 process，逐頁 staging／atomic commit，並只續用 render、tool、profile 同 output hashes 全部吻合嘅 draft。
- 新增 recoverable local adapter installer：兩邊 skill 同兩個 Claude agents 用 exact symlink；目前 Codex 會拒絕 leaf-symlink custom-agent TOML，所以兩個 Codex agents 改用可驗 hash 嘅 sealed regular-file projection。替換現有 object 前先保存包含 regular-file hashes、directory modes 同 nested symlink link text 嘅完整 tree inventory，亦可以 fail-closed restore。

## 0.2.5 更新重點

- 新增 `audit_title_notes.py`，逐 main item 核對題名 `*`／`**` 同題注 marker arity，列 missing／orphan 同跨頁 note hazard。任何語言嘅 note→running-body transition 都預設 fail closed；只可由 project-local、綁定目前 target／boundary hash 同逐項視覺證據嘅 transition review 放行，逐 heading ownership 仍由人核圖。
- 新增 `build_notation_inventory.py`，封存目前 annotated target、凡例 authority、可選 project literal profile、generator toolchain／invocation profile，同通用 residual symbols 嘅 scan provenance；metadata、code、單行 HTML comments 同 Markdown links 唔當來源 notation，星號題名註亦只喺明確啟用時 inventory。
- 新增 strict `validate_review_evidence.py`，統一 late-amendment status、current hashes、非空而相異嘅 before／after rows、render／evidence、writer／independent verifier 同 changed＋neighbor scan coverage；before／after 嘅內容關係仍由 reviewer 證明。
- 新增 `plan_release_rebuild.py`，找 direct hash drift／可由 discovery 重建嘅 missing artifacts，再沿 reverse dependencies 傳播 transitive staleness，輸出 dependency-first rebuild order；scope 外 missing path 直接 invalid，JSON output 亦唔可 alias plan、declared artifacts 或工具，clean／stale／invalid 有獨立 exit status。
- 補跨冊 evidence import 規則：byte-identical renders 仍要封 upstream receipt、scan mapping、review coverage 同 projection→amendment→current hash chain；亦明確分開 annotated-only 同 final audit CLI options。
- 衍生報告封 generator toolchain hash 同 invocation profile；要令 planner 偵測工具/schema 升級，release plan 亦要封 project-local tool snapshot 同 typed dependency。
- 技能新增明確泛化門檻：單一材料嘅書名、頁碼、專名、符號同來源句式留喺 project contracts；核心只吸收跨無關材料重現嘅失效類型，或者格式無關嘅資料遺失、fail-open、hash／provenance／安全問題。Regression tests 只用 synthetic fixtures；實書只作外部 forward test。

## 0.2.4 更新重點

- 長目錄對賬新增 `reconciliation_scope` 同 partitions，分開「全書目錄」同「本次交付正文」，所有範圍外條目要有明確 disposition。
- Release sidecars 改用 typed evidence graph，明分 active／historical／superseded；active provisional／failing artifact、失效 supersession、依賴環同未封存 closure 都會阻止交付。
- 新增 deterministic segment assembler，按 ordered manifest 重播 combined annotated；除咗 byte-for-byte 同連續 ranges，亦將實際 markers 同 sealed page manifest 嘅 `marker_expected` exact 對賬。
- 新增 `release-plan.json` validator，要求每個發現嘅 artifact 都係 `include`，或者 `exclude` 並寫理由；同時核對 reconciliation partitions 同 canonical subset hashes。
- Finalizer 寫 schema v2 receipt 時必須帶已驗證 `--release-plan`，用唯一 `release-plan` role 封存 pre-final active closure；final audit 會重驗目前 plan 同 exact closure，legacy v1 receipt 唔可以通過 final gate。Final／receipt／post-final audit 另屬第二層 bundle，唔回餵舊 receipt。
- Annotated audit 會即時核對 page manifest marker policy，但唔接受 notation policy 豁免；來源符號可記為 known issue，只有 final mode 先 exact accept。Notation policy 已擴充至多種來源符號，audit 亦可用 `--json-report` 保存 tool hash 同穩定 QA 報告。
- Page manifest 專責 source/render、scan facts 同 marker policy；boundary contract 同 independent review evidence 分開封存，唔靠 page manifest 一個 status 欄代替。

## 0.2.3 更新重點

- 每個 TOC scan 先建立末端印刷頁碼 inventory；保存 OCR 將頁碼黐入題名／日期嘅 recovery，並獨立逐頁核對全部引用。
- 將條目 ledger、分組 ledger 同 TOC-eligible heading inventory 分開；正文未列目錄嘅後記、書目等要有明確 exclusion reason。
- 實質題名／日期／人名／官職／數字異文，以及所有頁碼差異／異序，必須由另一 reviewer 同時核對目錄同正文頁圖，先可以標成 `source-verified-*`。
- Independent review 後嘅修改要 fresh re-review 受影響頁，或者保存 exact、可重驗嘅 amendment ledger；final 聲明依賴嘅所有 sidecar 都封入 receipt。
- Audit 同 finalizer 新增 opt-in `--summary-only`，可避免數百頁 context 淹沒終端，同時保留 issue counts、exit code 同 write／receipt 摘要。

## 0.2.2 更新重點

- 防止跨頁 list 續文／nested item 因預先 `.strip()` 而被誤標 `join`；audit／finalizer 會要求 `list-cont`、`list-new` 或經視覺確認並由 receipt 保存嘅真正結構 break。
- Receipt 必須封存 final audit 實際使用嘅 notation policy、source exceptions 同 page manifest，避免定稿後新增白名單改變結果。
- Final audit 可用目前 PDF 路徑重驗來源 SHA、scan sequence、sealed annotated marker coverage 同每張 render hash；刻意略去 marker 嘅 scan 要喺 manifest 明寫 `marker_expected: false`。
- Review sidecar 狀態禁止降級，獨立覆核要記 writer 以外嘅 reviewer、頁段同 evidence hash；長目錄每個差異亦要有已核圖 disposition。

## 0.2.1 更新重點

- 新增本書 notation policy：凡例證明 ASCII `[ ]` 係編者訂正時，可以同 OCR 低信心 markup 分開處理，避免機械刪除來源符號。
- 新增 exact line／column／symbol source exception ledger；原書本身未閉或錯序引號／括號要先核圖，位置、line text 或 issue 改變就會報 stale exception。
- Finalizer 可以輸出 SHA-256 assembly receipt，hash ordered upstream segments、combined annotated、boundary manifest 同 final；late patch 或 receipt 後修改會令 final audit 失敗。
- 結構 audit 補 tight-list continuation、table 欄數、delimiter 配對、space-before-tab、missing final newline 同 EOF 多餘空行。
- 將高信心 correction propagation、平行篇章互校、multi-pass OCR disagreement 同「收齊所有 auditor 後重新 assemble」寫成 release gate；所有差異只產生核圖候選。

## 0.2.0 更新重點

- 長單檔可安全並行：每個 agent 只寫獨立頁段 segment，主 agent 獨佔最終檔、assemble 同跨段覆核。
- 新增 long-book page manifest 流程，同時追蹤 scan page／printed page、spread、旋轉、空白、完整或局部重複、缺失印刷頁。
- 原 PDF 真缺頁時，先證實缺頁，再以可確認嘅同版來源交叉復原，保存 evidence manifest，並披露非視覺核實部分。
- 明確分類 page marker 移除後應係正文 join、段落 break、list boundary 定 item continuation；頁底註腳插斷正文時先重建完整正文。
- 新增純 Python stdlib audit/finalize scripts，檢查 marker inventory、顯式 boundary manifest、soft break、heading/list spacing、OCR residue 同重複長段；無完整 boundary contract 時拒絕寫入。
- Final pass 加入結構 invariants，例如條號、人名數、A–J 項目同表格總數；亦補充 untracked 新檔令 `git diff --check` 失效嘅盲點。
- Apple/macOS Vision 可以加速建立有 page provenance 嘅底稿，但直排次序、註腳、表格同跨頁內容仍以 PDF 圖像為準。

詳細版本摘要見 [CHANGELOG.md](CHANGELOG.md)。

## Bundled audit scripts

喺 skill 目錄執行。`basic` profile 唔需要完整 evidence chain；以下係 `release` profile 範例，只加入文件實際啟用嘅 feature gate。`--transition-reviews` 只喺逐項視覺核對後加入；`--authority`、`--inventory-profile` 同 `--include-starred-title-notes` 亦各自按來源結構選用。

```bash
python3 scripts/assemble_ocr_segments.py ordered-segments.json
python3 scripts/assemble_ocr_segments.py ordered-segments.json --output TARGET-annotated.md --write --receipt segment-assembly-receipt.json
python3 scripts/assemble_ocr_segments.py ordered-segments.json --output TARGET-annotated.md
python3 scripts/audit_ocr_markdown.py TARGET-annotated.md --mode annotated --expected-count "$EXPECTED_SCAN_COUNT" --boundary-manifest boundaries.json --page-manifest page-manifest.json --source-pdf /current/path/source.pdf
python3 scripts/audit_title_notes.py TARGET-annotated.md --boundary-manifest boundaries.json --json-report title-note-audit.json
python3 scripts/audit_title_notes.py TARGET-annotated.md --boundary-manifest boundaries.json --transition-reviews transition-reviews.json --json-report title-note-audit.json
python3 scripts/build_notation_inventory.py TARGET-annotated.md --authority notation-authority.json --inventory-profile inventory-profile.json --include-starred-title-notes --require-markers --json-report notation-inventory.json
python3 scripts/validate_review_evidence.py --root /path/to/book-evidence /path/to/book-evidence/segments/*.amendment.json
python3 scripts/finalize_page_markers.py TARGET-annotated.md --expected-count "$EXPECTED_SCAN_COUNT" --boundary-manifest boundaries.json
python3 scripts/validate_release_evidence.py release-plan.json
python3 scripts/plan_release_rebuild.py release-plan.json --json-report release-rebuild.json
python3 scripts/finalize_page_markers.py TARGET-annotated.md --expected-count "$EXPECTED_SCAN_COUNT" --boundary-manifest boundaries.json --output TARGET.md --receipt assembly-receipt.json --release-plan release-plan.json --write
python3 scripts/audit_ocr_markdown.py TARGET.md --mode final --notation-policy notation-policy.json --source-exceptions source-exceptions.json --page-manifest page-manifest.json --source-pdf /current/path/source.pdf --assembly-receipt assembly-receipt.json --json-report final-audit.json
```

第一次 boundary review 保留預設 verbose 輸出；全部 contract 已逐頁核實之後，長書重跑可以加 `--summary-only`。Audit 會按 severity/code 輸出 deterministic `ISSUE-COUNT`，finalizer 仍會保留 dry-run、write 同 receipt 摘要；任何非零 issue 要移除 `--summary-only` 重跑以查看位置。

`ordered-segments.json` 要用 strict v1 root：`schema_version`、`separator_utf8`、`scan_start`／`scan_end`、`page_manifest: {path, sha256}` 同 ordered `segments`；每段提供 path／sha256／scan range。Separator 只可係空字串或全由 LF newline characters 組成，唔准夾文字或其他 whitespace。Assembler 會將每段同組裝後嘅實際 markers 同 sealed page manifest 嘅 `marker_expected`／ordered segment inventory exact 比較，唔只係 concatenate 檔案。

`boundaries.json` 用 marker page 作 key，label 係 `start`、`join`、`paragraph`、`structural`、`list-cont`、`list-new` 或 `table-cont`。`table-cont` 保留跨頁表格行之間嘅單換行。每個實際 marker 必須恰好有一條記錄；無 manifest 時 dry-run 只會列 context，任何 `--write` 都會拒絕。寫入亦必須指定另一個 `--output`，腳本永不覆寫 annotated 底稿。完整 schema 同跨頁例子見 `references/long-book-workflow.md`。

Annotated mode 故意唔用 `--notation-policy` 放行來源符號；已核圖符號可以列為 known annotated-stage issue，但唔可以將該 audit 稱為 clean。Final mode 先按 exact notation policy 接受；本書冇相應來源符號／異常時，省略 notation／exception 選項。

`release-plan.json` 只 inventory finalizer 之前已存在嘅 active inputs，同所有 discovery 命中嘅 historical／superseded candidates；唔包含待生成 final、receipt 或 `final-audit.json`。只要 active artifacts 出現任何 TOC reconciliation 專用 type，就必須提供 `reconciliation_scope`。Finalizer 會先驗 plan，再自動將 plan 同 active closure 封入 schema v2 receipt，毋須手寫 `--upstream`；output／receipt 唔可以 alias plan 或任何 classified artifact，亦唔可以喺生成後新命中 pre-final discovery globs。Final audit 會重驗 plan、每份 classified hash 同 receipt 嘅 ordered active closure；legacy v1 receipt 雖可讀作診斷，但 final audit 必定失敗，必須重新 finalize。如要封存成品同 post-final QA，完成後另建 `release-bundle-plan.json`，將 pre-final plan、final、receipt、interpretation gates、audit report 同 audit tool hash 列入新 closure，再獨立跑 validator；唔好將佢回餵舊 finalizer 造成 hash cycle。完整 schema 同 migration 規則見 `references/release-gate.md`。

舊 rich manifests／policies 唔由 strict parser 猜欄位；先做 current-schema projection 同 sealed crosswalk，再重驗／重封。只要既有逐頁 OCR、render hashes 同 review evidence 仍可對返目前內容，schema migration 毋須重做 OCR。

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

### Codex 同 Claude Code 共用 canonical skill

本機開發可以唔經 plugin cache，直接由目前 clone 安裝兩邊嘅 personal skill 同 custom agents。兩個 skill directory 同兩個 Claude agent adapter 係 exact symlink；實測 Codex `0.149.0-alpha.4.1` 會 discover leaf-symlink agent 名，但套用 TOML 時用 [no-follow sensitive read](https://github.com/openai/codex/blob/97e7c55e2b64738ec6fe2311ad77a60b106fefae/codex-rs/exec-server/src/regular_file.rs#L20-L43) 而報 `ELOOP`，而目前 upstream 亦有明確拒絕測試，所以兩個 Codex agent adapter 係 byte-for-byte sealed regular-file projection。Repo 仍然係唯一 canonical source；adapter 改動後 `--check` 會報 hash drift，再跑 `--install` 先刷新 projection。

先 dry-run，再安裝；installer 會先備份任何現有 directory／file／foreign symlink，並封存完整 tree inventory。Source／target／backup path 有重疊，或者 nested backup／sealed projection 喺 restore 前漂移，都會 fail closed：

```bash
python3 scripts/install_local_adapters.py --dry-run
python3 scripts/install_local_adapters.py --install
python3 scripts/install_local_adapters.py --check
```

備份 manifest 會印喺 install output；要還原時用：

```bash
python3 scripts/install_local_adapters.py --restore /absolute/path/to/manifest.json
```

安裝後要開新 Codex task／Claude Code session，先保證兩邊重新 discover custom agents。Codex adapters 刻意唔設 `model`／`model_reasoning_effort`：按 [Codex custom-agent model resolution](https://learn.chatgpt.com/docs/agent-configuration/subagents)，逐項依次採用 explicit spawn override、`[agents]` default、再到 parent value。Claude adapters 用 `model: inherit`；按 [Claude Code subagent resolution](https://code.claude.com/docs/en/sub-agents)，平台級 `CLAUDE_CODE_SUBAGENT_MODEL` 或 per-invocation override 仍然有較高優先權。技能本身唔鎖死任何 model ID。

Auditor adapter 會宣告 Codex `sandbox_mode = "read-only"`，或用 Claude `permissionMode: plan` 並移除直接 write tools。不過實測上述 Codex 版本唔會將 role-level sandbox override 套入 child；Claude 嘅 live parent bypass 類 mode 亦可優先過 adapter default。主 agent 只可以喺 parent／child effective mode 確實係 read-only／plan 時，先將結果當成合約所講嘅獨立 read-only audit；單靠 prompt 禁止寫入唔係權限隔離。

### Apple Vision 並行底稿

macOS 有 Apple Vision 同 Swift toolchain 時，可直接由 skill 目錄跑：

```bash
python3 scripts/run_apple_vision_ocr.py RENDER_DIR OUTPUT_DIR \
  --first-page 1 --last-page 800 --workers auto \
  --report /path/to/reports/apple-vision-run.json
```

亦可以用 `--pages 1-40,45,50-80`。`auto` 取待處理頁數、physical cores 同八者中最小值；一頁失敗會保留其他已原子提交嘅頁並 exit nonzero，之後用同一 selector 續跑。舊 schema 或 hash／profile 漂移預設拒絕，只有確認要重做時先加 `--overwrite`。Apple Vision JSON／TXT 永遠只係 draft，逐頁校對仍以原 PDF／full-resolution render 為準，而且 runner 絕不調用 Tesseract／Tessdata。

## GJ.cool token

GJ.cool 唔係必需。若你有 token，可以喺自己本機 shell 或本地環境設定其中一個變數：

- `GJCOOL_ACCESS_TOKEN`
- `GJCOOL_AUTH`

唔好 commit 真 token。`.env.example` 只列出支援嘅變數名。skill 會先由 PDF render 圖像做視覺轉寫；只有 GJ.cool 即時有結果時，先會用佢做底稿再核 PDF。

## Repo 結構

```text
.agents/plugins/marketplace.json
adapters/
  codex/agents/jyut-ocr-segment-writer.toml
  codex/agents/jyut-ocr-visual-auditor.toml
  claude/agents/jyut-ocr-segment-writer.md
  claude/agents/jyut-ocr-visual-auditor.md
plugins/jyut-ocr-proofread/
  .codex-plugin/plugin.json
  skills/jyut-ocr-proofread/
    SKILL.md
    agents/openai.yaml
    references/common-ocr-traps.md
    references/jyut-ocr-detailed-rules.md
    references/long-book-workflow.md
    references/release-gate.md
    references/review-evidence.md
    references/subagent-orchestration.md
    scripts/_ocr_markdown.py
    scripts/apple_vision_ocr.swift
    scripts/assemble_ocr_segments.py
    scripts/audit_ocr_markdown.py
    scripts/audit_title_notes.py
    scripts/build_notation_inventory.py
    scripts/finalize_page_markers.py
    scripts/plan_release_rebuild.py
    scripts/run_apple_vision_ocr.py
    scripts/validate_release_evidence.py
    scripts/validate_review_evidence.py
scripts/install_local_adapters.py
tests/
  test_agent_adapters.py
  test_apple_vision_pool.py
  test_local_adapters.py
  test_notation_inventory.py
  test_ocr_markdown_scripts.py
  test_release_evidence_tools.py
  test_release_rebuild.py
  test_review_evidence.py
  test_title_note_tools.py
```

## License

MIT
