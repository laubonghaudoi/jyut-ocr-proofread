# 改 skill 嘅人睇：check、回歸測試、幾時改 skill

> 人工標註介面統一由 `jyut-ocr-bench` 維護。先讀 `manual-review-tool.md`，經技能嘅 `scripts/review.py` 開啟共用畫布；呢份筆記只描述資料、工作流程同採納規則，唔要求另建 HTML／server。同步快照、人工標註同採納答案係三個獨立步驟。

跑書嘅 agent 唔使讀呢份（`SKILL.md` 第十節）。

## Check

- 全套測試用 `python3 tests/run_all.py`（`--only unit`、`--only checks`、檔名過濾同 `--log-dir`，見 README〈維護〉）：每個 `tests/test_*.py` 同 `tests/check_*.py` 喺自己嘅 subprocess 跑，幾個檔一齊跑（`--jobs N`，預設最多 8 個；`--jobs 1` 逐個跑），全部 exit 0 先算過；唔使 GPU、唔使模型，喺 source 咗 `env.sh` 嘅 shell 跑結果都一樣。成套約一分鐘；一個檔慢到拖住成套，就將佢加入 `run_all.py` 嘅 `SPLIT_BY_CLASS`，等佢嘅 test class 分開跑。
- 有啲 check 釘住 `SKILL.md` 同 `references/` 嘅 command、開關同規矩（`check_documented.py` 核 `SKILL.md` 引用嘅每個開關真係存在，同埋每類疑點、決定編號、開關同修書計數都喺 `references/` 有寫）：改文就喺同一個改動改埋 check；唔好為咗過 check 刪咗仍然有效嘅規矩。
- `tests/check_generic_text.py`：`SKILL.md`、`references/`、`scripts/` 唔准寫 case id 或者 golden 嘅原文（見下面〈經驗泛化同技能更新門檻〉）。
- `SKILL.md` 係流程表，agent 每本書都成份讀入（本機模型做 agent 嗰陣 context 尤其緊）：保持短（上限 11,000 個字元，Python `len`），設計、實測同細節放 `references/`，`SKILL.md` 淨係講幾時讀邊一節。疑點、決定編號、開關同修書計數嘅意思寫喺 `long-book-workflow.md`。

## 回歸測試（golden reference）

改咗任何 prompt、任何 threshold、任何 merge 規則之後，**一定要跑**：

```bash
python3 tests/run_regression.py OUTPUT_DIR            # 只睇結果目錄，唔使 GPU
python3 tests/run_regression.py OUTPUT_DIR --strict   # 連 machine 級都當失敗
python3 tests/run_regression.py BOOK/repaired/pages --stage repaired   # 修復後嘅頁，用 repaired 嗰級 ratchet
```

同一版 golden 會喺兩個階段評：phase 3 嘅 seal（GPU 跑，同一份 code 兩次結果都唔同）同修復後嘅頁（唔使模型，次次一樣）。所以 assertion 可以每個階段各有一套 ratchet：`"ratchets": {"phase3": {...}, "repaired": {...}}`，`--stage` 揀邊套；冇 `--stage` 或者冇嗰個階段嘅一套，就用 assertion 頂層嘅欄位。`golden_page` 嘅 ratchet 有 `maxErrors`、`minMarkRecall`、`minMarkPrecision`、`maxStructureErrors`；`golden_table` 有 `minCellsExact`（冇 ratchet 嘅表要格格啱先過）。`golden_page` 亦比每行嘅種類（標題、列表、腳註、表格行、普通行）：標題跌咗、句尾變標題、腳註寫成列表都計結構錯；標題級數唔同只係報，唔計錯。腳註號 `[^N]`（golden 嘅寫法）同 ①（pipeline 嘅寫法）當同一個記號。改咗修復或者 finish 之後，喺你自己嘅語料庫跑修復閘：用保留咗工作夾嘅書重做修復再評 golden。

`tests/cases/*.json` 每本書一個檔，入面係一條條 assertion。**Assertion 分三級，因為佢哋唔係同樣可信：**

| provenance | 意思 | 失敗會點 |
|---|---|---|
| `human` | 有人真係核過頁圖，講明頁上係乜 | 硬失敗 |
| `regression` | 曾經出過、已修好、唔准返嚟嘅 bug | 硬失敗 |
| `machine` | 模型讀出嚟、有 adversarial 覆核，但冇人確認 | 只報 warning |

**點解要有呢個嘢**：本技能所有準確率數字本來都係喺一本書上面臨時度出嚟，改動係「講道理」而唔係「量出嚟」。同一節課入面，有兩次改動睇落係乾淨嘅勝仗，其實唔係——一次改標點 prompt，「，」由 125 個跌到 0（睇落完美），但同一次跑，有一頁變成差唔多逐個字後面一個「。」（每百字 96.6），另一頁一個都冇；另一次 crop matcher 出咗 141 張圖，第一張畀人核嘅就係錯 block。呢兩次都會喺呢度紅。

**Assertion 類型**：`contains`／`absent` 只比對漢字（標點、空白唔計），啱用嚟守字；守標點要用 `contains_exact`（逐字逐符號比對，只忽略空白），否則 formatter 刪走頁上嘅圈都會通過。另有 `json_field`、`table_row`、`table_count`、`golden_table`、`golden_tree`、`no_repeat_run`、`all_pages_*` 同純記錄嘅 `note`。三種表格 assertion 都經 `scripts/jyut_ocr/core/tables.py` 讀表（pipeline 寫表、auditor 查表用同一個 parser），Markdown 表同 HTML 表一樣計；合併格嘅字喺佢跨嘅每一行都算。`golden_table` 將成張表同 `tests/golden/` 入面人手轉錄嘅表比：形狀、合併格佈局同每格都啱先算過，未過都會報形狀、啱咗幾多格同 CER。直排印嘅表寫法係一筆紀錄一行，但評分一筆紀錄一行、一欄一行兩樣都收（用戶裁定，2026-09-27），所以每張表照寫法同轉置（行變欄）各比一次（`_tables.orientations`；表頂成行嘅標題、表腳成行嘅附註留喺原位，淨係轉佢哋中間嗰截），報告講明用咗邊樣。轉置要計分真係好過照寫法先用：啱嘅格唔少過、CER 唔高過，而且其中一樣好啲；淨係睇啲格喺唔喺原位唔得——輸出將 golden 表頂嘅標題行寫咗做表上面一行字，照寫法就冇一格喺原位，轉置反而可以碰啱幾格（實測一張冇錯嘅表因此由 CER 7% 評成 56%）。`golden_page`（成頁同人手轉錄比錯字）嘅 golden 有表嘅話，輸出嘅每張表都照寫法同轉置試，淨係轉置令成頁錯字少咗先轉（`tablesTurned` 記低）。其他轉法——紀錄倒轉次序、每筆紀錄嘅欄位倒轉、成張表倒轉、轉九十度——用戶未裁過，一律當錯表：收咗就會將 pipeline 定向、讀向轉錯嘅表當啱。跨頁嘅大表係一張表：`golden_table` 寫 `"pages": [首頁, 尾頁]`，golden 係合併咗嘅成張表，輸出要將合併好嘅成張表寫喺呢幾頁其中一頁先算啱——逐頁搵表（一頁未收尾嘅表唔會駁落下一頁），攞最啱嗰張嚟比，每頁各有一截就只計到一截，報告亦會講輸出寫咗幾多張表。表格入面嘅字用 `contains`、`contains_exact`、`absent` 守嘅話，都可以寫 `"pages": [首頁, 尾頁]`，幾頁連埋當一段字查，因為合併咗嘅表寫喺邊一頁都得。`golden_tree` 比印出嚟嘅分類圖、大括號樹狀圖、組織系統表：golden 係人手寫嘅巢狀清單（每個節點一項，節點嘅註寫喺括號入面），輸出用巢狀清單、檔案樹（├── └──）、打橫嘅括號圖（┌ ├ ┼ └，好似維基文庫咁）定 Mermaid 圖都得，每條上下級連線同每條註都要齊，而且唔可以多咗錯嘅連線（例如一個節點同時掛喺啱同錯嘅上級）先算過；註要喺讀出嚟嘅樹入面係括號註，喺頁上第二度出現唔計。未過會報啱咗幾多條連線、錯咗幾多條、當係邊種畫法讀、註啱咗幾多。golden 入面某個節點掛喺邊度喺頁上睇唔清，assertion 可以寫 `uncertainParents`（`{"節點": ["可能嘅上級", …]}`，要包 golden 自己嗰個），輸出掛喺其中任何一個都算啱，報告講明用咗邊個（用戶決定 D10；`run_regression.py --no-uncertain-parents` 就淨係認 golden 嗰個）；同時掛喺兩個上級照算一條錯。讀樹嘅 parser 係 `scripts/jyut_ocr/core/trees.py`。

**加 assertion 嘅時機**：每次有人核圖裁決一個字、每次修好一個 bug。裁決 → `human`，bug → `regression`。唔好等到「有時間先做」，因為 assertion 嘅來源就係當時嗰個判斷，過咗就冇。

**唔好 overfit 校準書。** 本技能將來要處理幾百本未見過嘅書；校準書淨係例子。凡係「書係咁樣嘅」嘅假設——邊個引擎好啲、句讀有幾密、書眉喺邊、一頁幾多葉——都唔准寫死成常數或者 prompt 規則，要由 book profile 喺每本書自己嘅頁面量度。Golden case 係用嚟**測機制**嘅（例如 profile 要喺每本書自己量度、揀返讀得啱嗰個引擎），唔係用嚟調參數嘅；一個改動要同時喺所有 case 書都唔退步先算數。如果你發覺自己加緊一條「呢本書係咁」嘅規則，嗰個就係 overfit。淨係防真正失效（幻覺空白頁、無限重複、漏成段）嘅護欄可以係常數，而且都要喺所有書上面都成立。

**每本新書要記低佢覆蓋咗乜、冇覆蓋乜**（`traits` / `notCovered`）。加多幾本同類型嘅書價值好低；要揀**打得爛現有假設**嘅書：橫排、簡體、多欄、爛掃描。現有 case 覆蓋咗乜、未覆蓋乜，睇各個 `tests/cases/*.json` 入面嘅 `traits`／`notCovered`，唔好喺呢度列書名。

**既有轉錄（包括你自己上一版、或者其他模型／人做嘅參考檔）唔係 ground truth，只係另一份底稿。** 本項目實測：一份用 GPT-5.6＋Apple Vision 做嘅參考檔，喺人手裁決嘅 10 個分歧之中 6 個係錯，包括整段 36 字漏失同一個讀反語意嘅近形錯字。所以分歧只係「要人核圖」嘅訊號，唔係對錯判決；用未經核實嘅參考做評分基準，會將正確輸出判成錯誤。

## 經驗泛化同技能更新門檻

- 每份材料新見到嘅字形、題名、頁碼、人名、地名、來源句式、版式、頁面家具同編輯符號，先放入目標 project 嘅 correction ledger 或 boundary contract，唔直接寫入核心 skill。
- 只喺以下情況更新核心 skill／bundled scripts：同一失效類型喺至少兩份互不相關嘅出版物或 OCR pipeline 重現；單一案例揭示與書種無關嘅資料遺失、fail-open、hash／provenance 或安全問題；或者公開 contract 同工具實際行為矛盾。
- 核心規則同程式唔可以 hard-code 書名、scan number、人名、地名、單一版次符號或特定來源措辭。新 acceptance 必須由結構、exact locator、目前內容 hash 同視覺 evidence 證明。
- Regression tests（`tests/run_regression.py` + `tests/cases/*.json`）係以實書做 golden reference：每本書一個 case 檔，記低 PDF 路徑、sha256、頁數、`traits`／`notCovered`，同埋 pin 住實頁碼嘅 `human`／`regression`／`machine` assertion（見上面〈回歸測試（golden reference）〉）。實書名、實頁碼喺 `tests/cases/` 入面係允許亦係必要嘅；唔可以寫入嘅係核心規則、`references/` 同 bundled scripts，嗰度只描述失效類型。新教訓寫入對應 case 嘅 assertion（人核裁決 → `human`，修好嘅 bug → `regression`，失效類型記喺 assertion 嘅 `note`），跨書嘅陷阱先加入 `references/common-ocr-traps.md`。版本歷史記喺 `CHANGELOG.md`，README 只係概覽。
- 每本書完成後先將新發現記做 candidate lesson。若現有 project contract 已經可以表達，而且未達以上門檻，預設結論係毋須更新技能。

## 獨立 benchmark 同相容快照

人工標註嘅維護來源係 `laubonghaudoi/jyut-ocr-bench`。呢度嘅人工 case 同 golden 係相容快照，`tests/benchmark.lock.json` 記錄來源 repo、完整 commit、資料版本、manifest SHA-256、人工斷言投影及 golden／字形規則 hashes。原始書目來源、pipeline regression／machine 檢查同階段 ratchet 繼續由本技能維護。

```bash
python3 scripts/sync_benchmark.py --benchmark-root ../jyut-ocr-bench --commit FULL_COMMIT_SHA
python3 scripts/sync_benchmark.py --benchmark-root ../jyut-ocr-bench --check
python3 tests/check_benchmark_snapshot.py
```

同步前，benchmark checkout 必須喺指定 commit，manifest 列出嘅資料全部已提交、hash 相符。`--check` 只讀；已鎖定嘅本地人工資料有改動就停止，避免覆蓋未搬去 benchmark 嘅新裁決。新增人工裁決先改 benchmark、驗證及提交，再更新呢度嘅鎖定版本；唔好直接改相容快照。同步保留本地 assertion 次序、非人工測試、source 資訊同 ratchet；只更新人工紀錄、佢哋引用嘅 golden 同人工字形規則。非人工測試或 ratchet 嘅改動唔會令人工快照失效。

舊 `tests/run_regression.py` 繼續接受原本指令，算法同門檻冇改。JSON 入面舊 `cer` 保留兼容，另加 `legacy_alignment_cer` 同 `cerMetric` 明示佢係舊 alignment 指標；唔可以當成獨立 benchmark 嘅最小編輯距離 CER。比較新 benchmark 分數，要將兩次輸出都交畀同一資料／評分版本重新評分。
