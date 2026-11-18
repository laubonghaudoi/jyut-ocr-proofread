# 跑書流程：本書目錄、各步細節、疑點同開關

`SKILL.md` 係流程表；呢份講每一步做乜、點解，同埋腳本寫出嚟嘅每類疑點、每個決定編號、每個開關同修書計數係乜意思、要點做。淨係讀要嗰一節（`grep -n '^##' references/long-book-workflow.md`）。

呢度講「要對圖」「要睇圖」嘅地方，係畀用戶做嘅：`finish_book.py` 將佢哋按類同頁碼寫入 `doubts.md`，用戶對住原書核。B 套（本機模型校對）嘅 agent 喺對話入面唔讀頁圖；A 套嘅 agent 淨係喺 packet 題目問嘅位睇圖，唔另外逐頁覆核。冇 OCR 引擎、要人手分段轉錄嘅工作流見 `structured-workflow.md`。

## 目錄

- [三次交付同本書目錄](#三次交付同本書目錄)
- [OCR 階段（ocr_book.py）](#ocr-階段ocr_bookpy)
- [Book profile 同異體](#book-profile-同異體)
- [逐頁校對（proofread_pages.py）](#逐頁校對proofread_pagespy)
- [封條同續跑](#封條同續跑)
- [跨頁表格](#跨頁表格)
- [註腳](#註腳)
- [修書（repair_book.py）](#修書repair_bookpy)
- [一個 command 做完（finish_book.py）](#一個-command-做完finish_bookpy)
- [清走工作檔（clean_book.py）](#清走工作檔clean_bookpy)
- [圖：淨係留圖片（--extract-figures）](#圖淨係留圖片--extract-figures)
- [疑點（doubts.md）](#疑點doubtsmd)
- [用戶決定嘅規則（decisionChanges）](#用戶決定嘅規則decisionchanges)
- [開關](#開關)
- [修書計數（_book_lint.py）](#修書計數_book_lintpy)
- [agent 規矩嘅實測根據](#agent-規矩嘅實測根據)

## 三次交付同本書目錄

同一本書交三次，檔名固定用英文：`draft.md`、`proofreading.md`、`final.md`（第三次連埋疑問清單 `doubts.md`），全部寫喺本書目錄 `BOOK`。`BOOK` 開喺 `"${JYUT_OCR_WORK_ROOT:-$HOME/jyut-ocr-books}/<名>"`（`JYUT_OCR_WORK_ROOT` 喺 `env.sh`），唔開喺 PDF 隔籬：render 同底稿成幾 GB，而 PDF 嗰個資料夾可能係用戶嘅 git repo。

`BOOK` 入面：`renders/`、`page-manifest.json`、`survey-report.json`（`survey_pdf.py`）；`ocr-hunyuan/`（引擎 B）、`ocr-paddle/`（引擎 A）、`reports/`（runner 嘅 run report 同 `ocr-times.json`）；`book-profile.json`；`packets/`（A 套嘅題目同 `answers.jsonl`）；`proofread/`（逐頁封存稿）；`assembled/`、`repaired/`、`finish/`、`final.md`、`doubts.md`（`finish_book.py`）。

**用戶睇嘅係 PDF 隔籬嗰幾份，流程自動抄過去**：`X.pdf` 嘅草稿叫 `X-draft.md`、定稿叫 `X.md`、疑問清單叫 `X-doubts.md`，有抽圖就有 `X-圖/`。`build_draft.py` 同 `finish_book.py` 由 page manifest 記住嘅 `source_pdf`（連 SHA-256）搵 PDF，自己抄，唔使 agent 抄：

- PDF 隔籬已經有同名檔、但唔係流程寫過嘅，唔蓋、唔抄，淨係講一句；
- 抄過去之後用戶改過嘅，再抄之前舊嗰份搬去 `X.edited-<時間>.md`（`X-draft`、`X-doubts` 一樣）；
- PDF 搬咗、改咗或者嗰個資料夾寫唔到，就唔抄，嗰行或者 `WARNING` 講點解，`BOOK` 入面嗰份照有；
- 唔想抄就加 `--no-deliver`（`build_draft.py`、`finish_book.py` 都有）；`proofreading.md` 唔抄。
- **由掃描圖資料夾（`survey_pdf.py BOOK --images-dir DIR`）開嘅書**：manifest 嘅 `source_pdf` 係空、`source_kind` 係 `images-dir`，冇 PDF，所以乜都唔抄，交付檔全部留喺 `BOOK`，`BOOK` 唔清走：
  - 草稿：`build_draft.py` 嗰行講 `not copied beside the PDF: the page manifest … names no source PDF`，`[ocr-book] DRAFT` 行就係 `BOOK/draft.md` 加呢個原因；
  - 定稿：`finish_book.py` stdout 淨係 `final.md: BOOK/final.md` 同 `doubts.md: BOOK/doubts.md`，冇 `beside the PDF` 行；log 寫 `source_kind is images-dir (no source PDF): skipping beside-PDF copy`（唔係 WARNING）；第 5 步唔清（`no source PDF`），RESULT 行尾係 `; work kept`；有 `--extract-figures` 嘅圖喺 `BOOK/figures/`，`final.md` 嘅圖行指住佢；
  - 清走：`clean_book.py`（預設同 `--all`）一律 exit 3 `the page manifest … names no source PDF: nothing removed`。呢個拒絕係啱嘅：交付檔淨係喺 `BOOK`，成個刪咗就冇晒。所以唔跑 `--all`；交付時講 `BOOK/final.md`、`BOOK/doubts.md` 喺邊，同埋 `BOOK` 留低咗（`renders/` 係掃描圖嘅副本，可能好大），用戶將交付檔抄走之後自己決定刪唔刪。冇用戶講，agent 唔好自己刪 `BOOK`，亦唔好自己將交付檔抄去掃描圖資料夾。

- **第一次：未校對草稿 `draft.md`**：引擎 B 讀完全書即刻出（`ocr_book.py` 自己叫 `build_draft.py`），唔問模型，幾秒。用引擎 B 嘅字，唔用引擎 A：引擎 A 讀直排頁會亂欄序（golden 頁實測 CER：引擎 A 75.8%，引擎 B 5.8%）。頁按 manifest 嘅掃描次序；標咗 `marker_expected: false` 或者 `content: blank` 嘅頁略過（淨係標咗 `relation: exact_duplicate` 嘅頁照出）；書眉同頁碼照留；冇 page marker，頁同頁之間分段；冇引擎 B 文字嘅頁寫一行空位，講明係掃描第幾頁；引擎 B 不停重複（loop）嘅地方，嗰段字留一次，跟住寫一行空位講明重複咗幾多次（短嘅重複照抄）。
- **第二次：進度稿 `proofreading.md`**：校對期間 `wait_for_run.py` 加 `--progress BOOK`，每 5 分鐘重建一次（`build_progress.py`）：封存齊嘅頁用校對稿，其餘用草稿嘅字。認頁淨睇封條，唔睇封嗰陣用咩參數，所以 `--overwrite` 重做緊嘅頁，舊封條照用。建唔到喺嗰行講，整唔到都唔改 exit code。
- **第三次：`final.md` 同疑問清單 `doubts.md`**：`finish_book.py BOOK` 寫 `BOOK/final.md` 同 `BOOK/doubts.md`，係最後一次交付。

## OCR 階段（ocr_book.py）

macOS 只支援原生 Apple Silicon。安裝同跑書統一用 `--engine apple-vision`（純 Vision，唔下載 HF 模型）或 `--engine hunyuan-paddle`（Hunyuan＋Paddle，保留 Metal／MLX）。純 Vision 只寫 `ocr-vision/`，唔做模型交接、草稿交付或 Hunyuan 補讀，跳過 book profile；之後用 `proofread_pages.py --single-engine`，再用 `finish_book.py BOOK --single-engine --ocr-a BOOK/ocr-vision`。Linux／WSL2 另保留 `--engine hunyuan` 例外。舊路線名稱同 `--engines` 已移除；舊混合書目保持原樣，轉路線用新工作目錄。以下 OCR 次序描述雙引擎路線。

Render 由 `survey_pdf.py PDF BOOK` 做（純 CPU，`--jobs` 係同時幾多個 process，預設 16）；之後 `ocr_book.py BOOK` 一條命令、一個時限做晒：

1. 引擎 B（HunyuanOCR）：`run_hunyuanocr.py BOOK/renders BOOK/ocr-hunyuan`；
2. 引擎 B 讀完全書就出草稿（`build_draft.py BOOK`），寫好就印一行 `[ocr-book] DRAFT 路徑`：PDF 隔籬嘅 `X-draft.md`，冇 PDF（掃描圖資料夾）或者抄唔到就係 `BOOK/draft.md`，括號講點解；`wait_for_run.py` 嗰行尾亦有 `; DRAFT 路徑`。草稿失敗唔阻流程；
3. 引擎 A（PaddleOCR-VL）：`run_paddleocr_vl.py BOOK/renders BOOK/ocr-paddle`；
4. 引擎 B 補讀：`run_hunyuanocr_second_look.py BOOK/renders BOOK/ocr-paddle BOOK/ocr-hunyuan`。補讀要用引擎 A 嘅版面 block，搵引擎 B 漏讀嘅窄欄、邊欄，裁出嚟再讀，改寫引擎 B 嗰幾頁，所以一定喺引擎 A 之後。之後 agent 再跑一次 `build_draft.py BOOK`。

封好嘅頁一律跳過，同一條命令再跑就由上次停低嘅位置繼續。經 `run_ocr_phase.py` 跑嗰陣，一個交接窗口包晒四步，模型每次 call 停一次、開一次。`--single-engine` 淨做第 1、2 步。Exit：`0` 做完；`75` 未做完（直接跑嗰陣 RESULT 行最尾有 `next call:`，即係同一條命令加 `--round N+1`，照抄；或者 RESULT 行話要經 `run_ocr_phase.py` 跑）；`1` runner 失敗，或者預算太細、第一個引擎載入模型都唔夠時間（RESULT 行列出淨低幾多秒同要幾多秒，要加大預算）；`2` 用法錯（冇 render 目錄）。時限、載入估計（`ocr-times.json`）、交接同其他 client 見 `local-ocr.md`。

兩份底稿嘅 JSON 要講明引擎：A＝PaddleOCR-VL、B＝HunyuanOCR。`proofread_pages.py` 同 `finish_book.py` 見到掉轉咗或者係其他引擎，喺寫任何嘢之前 exit 2。底稿點樣算 current、截斷咗點算、舊底稿點收，見 `local-ocr.md`〈輸出、續跑同核圖〉。

## Book profile 同異體

`book_profile.py RENDER_DIR OCR_A_DIR OCR_B_DIR PROFILE` 喺本書自己嘅頁量度，封入 `book-profile.json`，唔假設任何嘢：

- **layout**：書眉、頁碼、一頁印幾葉嘅影印本；淨用引擎 A 嘅 block 量。
- **engines**：邊個引擎讀呢本書讀得好啲（`preferEngine`）；`<=4` 頁嘅短檔自動跳過，用 `adjudicate`：有分歧就直接問。
- **variants**：兩個字形嘅分歧（粵/粤、既/旣），逐對列出，附幾頁嘅裁圖。
- **notation**：本書印乜符號（圈、點、雙圈、旁點、括號）同密度。

A 套佢一樣出 packet 題（exit `10`）；B 套經 http 問模型。`--workers` 要等於 server 嘅並行數：`env.sh` 有 `PROOFREAD_MAX_INFLIGHT`（安裝器 `--local-model-concurrency N` 寫）就唔使畀，`--backend http` 預設用佢；冇就照 `SKILL.md` 一問用戶。

**異體唔使人裁，流程亦唔等人**：一對字形邊個係字表（`references/variant-equivalents.json`、OpenCC 異體表）話係同一個字，phase 3 先當異體處理。冇裁定嘅對，跟本書裁定偏向嘅引擎；冇裁定就跟 `preferEngine`。Profile 隔籬可以有人寫嘅 `variant-rulings.json`（`--variant-rulings` 指定），逐對寫 `printed`；有就照用，冇就照上面揀，唔停低等。2026-10-12 之前 profile 會叫模型投票寫呢個檔：嗰啲裁定記住 `ruledBy: model`，冇裁到嘅對喺 `modelUnruled`；照人嘅裁定咁用，頁面嘅 seal 記 `variantRulings`（`variantRulingsSha256`）同 `variantRuledByModel`（邊啲位係模型定），合併段記 `variant-ruling`、`variant-model-ruling`、`variant-person-and-model-ruling`。

**簡體書**：兩個引擎嘅字多數係簡體（`simplified_script`）嘅書，淨係繁簡寫法唔同嘅一對（諭/谕）跟引擎 B 嘅寫法（`variant-script`）：實測引擎 A 會將簡體印嘅題目讀成繁體。裁決用繁體答嘅，寫返頁面嗰種寫法，原本答案記 `answeredTraditional`；寫唔定就記 `answer-script-converted` 疑點。

## 逐頁校對（proofread_pages.py）

每頁按情況走一條路，`mode` 記入 seal：

- **merge**（兩個引擎都有字）：對齊兩份底稿；一致嘅字唔問，分歧（唔係異體）裁圖問校對者（裁決，「字: …」）；逐段數標點（census）再加標點，唔准改字；之後補讀同排版（段落、標題、表格），排版結果要過字元守門（`verify_characters`、刪字檢查）；被拒就重試一次，再唔得就用退返嘅版本加疑點。排版唔准加標點（除咗編號括號）；不過引擎格仔唔收嘅表（`tablesRejected`）冇逐段加標點，由排版對住頁圖寫，表入面兩個引擎都喺同一位讀到嘅標點（例如列舉嘅「、」、表頭嘅括號）准寫喺佢寫嘅表入面，每個最多兩個引擎讀到嘅次數，記入 seal 嘅 `tableMarksAllowed`；表外正文照舊一個都唔准加。排版步收到嘅文字原文記入 seal 嘅 `structureInput`（`text`、`characters`、`sha256`；超過兩萬字淨留頭兩萬字，記 `truncated`），排版有冇漏嘢可以直接對。
- **transcribe**：得一份底稿（`--single-engine`、缺引擎 A、加咗 `--allow-missing-engine-b` 而缺引擎 B、引擎 B 截斷而且一開始就重複到底、冇漢字但有西文或者數字嘅頁）：對住頁圖轉錄，底稿做提示。
- **merge-order-conflict**：兩個引擎嘅閱讀次序對唔上（例如直排題名頁一個引擎亂咗欄序），逐字對齊冇意義，成頁用偏好嗰個引擎嘅讀法。
- **blank-page**：兩個引擎都冇字（或者淨係頁碼、書眉），唔問就封空頁；一個引擎讀出一大段自己重複、另一個冇見過嘅字，當冇支持丟咗，記 `unsupported-reading`。開咗 `--content-fidelity on` 而清點話有內容，就改走 **image-recovery**：對住整頁原圖補讀同覆核。

全部頁封好，同一條命令自己接跨頁表格（〈跨頁表格〉）。

**完整性 v2（`--content-fidelity on`，預設關）**：開咗每頁多三條整頁睇圖題：整頁清點（inventory，空白頁都問）、排版前補讀（content-recovery）同最後覆核（coverage-review）。佢哋定唔到嘅，嗰頁照封，記 `content-fidelity-unresolved` 疑點，唔當失敗；清點答「空白」但引擎讀到字，唔會清空嗰頁；覆核改嘅字記入 `decisionChanges`（`contentFidelity`）；表格 placeholder 保留。舊頁冇呢份紀錄：開咗之後再跑，冇紀錄嘅封存頁會報 blocked，要 `--overwrite`；開關出現之前已經行過 v2 三問、紀錄完整嘅頁，開咗照收（睇證據本身，唔睇開關 key）；關咗嘅 run 兩種都收。詳細見 `content-fidelity.md`。

**缺引擎 B 嘅底稿**（雙引擎）：預設唔封嗰頁，當 failed（引擎 B 嘅 OCR 可能仲行緊或者中斷咗），stderr 同摘要有 WARNING 列出頁碼，run report 計 `counts.engineBMissing`；等引擎 B 讀完，再跑同一條命令。真係要淨用引擎 A 轉錄先加 `--allow-missing-engine-b`；已經咁樣封好嘅頁，之後冇呢個開關都照計 current。

Exit：`0` 揀咗嘅頁全部封好；`1` 有頁 failed 或者 blocked（包括缺引擎 B 底稿嘅頁），冇題等緊；`2` 參數或者輸入錯（冇引擎 B 底稿又冇 `--single-engine`、引擎掉轉、profile 唔係呢批底稿嘅），乜都冇跑；`10` 等 packet 答案；`11` 等答案兼有頁 failed／blocked。Run report 嘅 `pages` 逐頁講點解；`wait_for_run.py` 嗰行列出 failed／blocked 頁同呢啲原因，B 套淨係睇嗰行、照講畀用戶知。冇 profile 嘅雙引擎文件，`<=4` 頁照短檔快速路徑跑，更長就要先跑 `book_profile.py`。

## 封條同續跑

每頁封 `page-NNNN.md` 同 `page-NNNN.json`：JSON 記 render、兩份底稿、profile、異體裁定、字表、規則 key 同開關（provenance），封條驗 Markdown 同 JSON 嘅 hash。續跑淨係重做唔 current 嘅頁：

- **淨係記錄、唔判過期**：校對 transport（packet／http）、model 名、endpoint、script 同 helper 嘅 hash。改 code、換答題模型都唔使重做。
- **判過期**：render、底稿（連引擎 B 嗰份）、profile、異體裁定、字表同開關改咗，或者封條對唔上：報 blocked，講明邊個 key；查清楚先對嗰幾頁加 `--overwrite --pages N`，唔好成本 overwrite。
- **舊版技能封好嘅頁照收**：封頁嗰陣仲未有嘅規則 key（`variantPairs`、`agreedMarks`、`unreadTableCharacters`，同而家長開嘅規則）缺咗都當已完成，run report 逐頁標 `legacy`，計入 `counts.legacy`。有記錄嘅 key 值唔同就仲係 blocked。
- **round-4 開關**：`--keep-arabic-numerals`、`--loop-detector`、`--neighbour-guard`、`--learned-heads` 預設開。一本書喺佢哋未預設開之前封過頁，續跑嗰陣唔加返相應 `--no-…` 就會一半開一半關：腳本一開始就 exit 2，`ERROR resume with --no-…` 講明要加乜；照加，之後呢本書每次都加。
- **續跑 OCR**：`SKILL.md` 八送返第 2 步嘅書（新 session、交接機都一樣）照四.2 跑：`env.sh` 有 `OCR_PHASE_RELEASE_CMD` 就經 `run_ocr_phase.py` 交接（前景，一個 call 一個，75／76 照 `next call:` 再叫），冇就背景跑 `ocr_book.py` 再用 `wait_for_run.py` 等；交接機上唔好直接跑 `ocr_book.py`：有頁未讀，佢唔載入引擎就 exit 75，RESULT 行叫你經交接跑。兩個引擎同補讀都跳過封好嘅頁。交接機上就算全部頁都封好，一個交接 call 都會停一次、開一次模型；所以上次 `BOOK/ocr.log` 最尾一行已經係 `RESULT exit=0`（`[ocr-phase]` 或者 `[ocr-book]`），OCR 就已經做完，唔使再跑，直接跑第 2 步尾嘅 `build_draft.py BOOK`，再去第 3 步。
- A 套：`answers.jsonl` 只加唔改；同一題重播唔再問。題目同答案綁定圖、prompt 同影響答案嘅設定，唔綁 script bytes，所以改 code 唔會令已答嘅題作廢。

## 跨頁表格

全部頁封好之後，`proofread_pages.py` 自己跑 `stitch_tables.py` 嘅 pass（唔使另外叫；舊目錄或者想睇決定可以手動跑 `stitch_tables.py OUTPUT_DIR --renders ... --drafts ... --dry-run`）：

- 前頁嘅表係嗰頁最後內容（之後只有家具、表註或者頁底註），後頁第一個內容係表（前面最多續表題名），後表冇自己題目或者另一行欄名，就按位置同欄數、重複表頭（照讀或者倒讀相同）、續表題名、斷格或者格線接上，寫喺表開始嗰頁，重複表頭只留一次，註留喺原頁；
- 表前或者表後有正文就係另一張表，唔接亦唔記疑點；
- 每個判過嘅頁界一張對照圖 `page-NNNN-stitch-01.png`（交付之後 `finish_book.py` 第 5 步連其他裁圖一齊清走；要再睇就再跑一次 `stitch_tables.py`，render 冇咗先用 `survey_pdf.py --renders-only` 整返）；改過嘅頁原文留喺 seal 嘅 `tableStitch.before`，再封過，續跑照算 current。

疑點見〈疑點〉入面 `table-continuation-` 開頭嗰幾類。

## 註腳

`finish_book.py` 將每頁配到嘅註號同註寫成 Markdown 腳註（`scripts/_footnotes.py`），跟評測規則：

- 引用 `[^n]` 留喺原本位置；預設用原印編號（印 `②④⓪` 就寫 `[^240]`）；`--renumber-footnotes` 先全書重編 `[^1]`、`[^2]`……
- 標號全書唯一：同一原印號再出現（每頁由 ① 起，或者同一版兩條 ①），後出嗰條改用 `[^1-2]`、`[^1-3]`……，引用同定義同一個 ID。
- 逐頁配對：同號嘅註號同註數目一樣，就按閱讀次序一個對一個；配唔上嘅照原印留低，記入 `finish/footnotes.json` 同 `doubts.md`（`note-unmatched`），唔補造另一半。
- 跨頁淨係配明顯係同一條嘅：前頁最尾未配嘅註號對下頁註文開頭同號嘅註，而且全書上一個註號正係佢減一（所以 ① 唔會跨頁配），列喺 `footnotes.json` 嘅 `crossPage`。
- 第 3 階段縫埋咗嘅跨頁表格（`tableStitch`）唔另外當一頁配：搬咗去首頁嗰幾行嘅註號同佢哋原頁頁底嘅註，淨係上面逐頁同跨頁兩條規則配得上先配，配唔上照原印留低。（2026-11-15 撤回「嗰幾頁當一頁配對」：喺評測嘅跨頁表格 case，佢 12 條註淨係轉到 2 條，評測又將 `[^n]:` 定義搬去全文最尾，兩條定義跳到其餘 10 條照原印嘅註後面，主指標 #2 CER 差咗 1.0 分，雖然 #7 註腳配對升咗；評測定 #2 做主指標，所以唔做。）
- 定義預設留喺頁底原位；正文跨頁接落去（boundary `join`）嗰陣，嗰頁嘅頁底註放喺接好嗰段之後，按原印次序；`--relocate-footnotes` 先喺移除 page marker 嗰陣將每條定義搬去引用所在 block 之後。

## 修書（repair_book.py）

`finish_book.py` 第 2 步自己叫，agent 唔使直接跑。唔問模型，淨用 CPU：

1. 驗輸入（封存頁、拼書稿、render 對 manifest），唔啱 exit 2；
2. 由本書學 book model（`book-model.json`：書眉、卷次、頁碼、目錄頁、引擎 B 代標點嘅字、句末符號），每樣有閘，閘唔過記 `gate-failed`，頁碼跳號記 `leaves-missing`；
3. 修書前數一次〈修書計數〉；
4. 按次序跑各階段（每個都有 `--no-…` 開關，見〈開關〉）：furniture、marks、brackets、contents、title-page、levels、paragraphs、joins、runs、forms、script、words；
5. 寫新目錄（`--output`），每個改動記入 `repair-log.jsonl`（頁、改前改後、規則、證據），兩個改動撞埋同一段記 `conflict`；log 喺封存頁重播要逐 byte 一樣（唔係就 exit 5）；再數一次計數，`repair-report.json` 講做咗乜。

規則搵到但定唔到、又關乎印咗嘅字嘅位，記疑點唔改；淨係版面嘅（分段、標題級數）照舊。Exit：`0` 做完；`2` 輸入驗唔過；`3` 輸出目錄已經有（`--resume` 先可以沿用）；`4` `boundaries.json` 唔合定稿契約；`5` 重播唔一致。

## 一個 command 做完（finish_book.py）

Phase 3 封晒全書之後，`finish_book.py BOOK` 一條命令做五步，每步都可以重跑：current 嘅照用，停咗嘅由停低嘅位繼續。

1. **拼書**：manifest 要嘅每頁都要封好（`complete`、封條完整、對住 manifest 嘅 render），未封 exit 3；封條壞咗、manifest 冇嘅封存頁、或者有完整性 v2 紀錄但驗唔過，exit 2。喺 `.assembled.partial/` 砌好、驗過先改名做 `assembled/`；對唔上而家封存頁嘅舊 `assembled/` 搬去 `assembled.stale-<時間>/`，唔刪。任何輸入喺 `assembled/` 入面就 exit 2。
2. **修書**：`repair_book.py BOOK --output BOOK/repaired`（單引擎跳過）。
3. **定稿**：照修書寫嘅 `boundaries.json` 移除 page marker，寫 `BOOK/final.md`，再跑 final audit（`audit_ocr_markdown.py --mode final`）。Audit 嘅錯誤係疑點（`final-audit`），唔係失敗：兩條路線都照交付，RESULT 行數住幾多個。
4. **疑問清單**：`BOOK/doubts.md` 按類同頁列晒定唔到嘅嘢：要查資料嘅專名、phase 3 同修書嘅疑點、修書之後仲有嘅計數、final audit 嘅錯誤；最可能要改字嘅排先。每類頭十項展開，其餘摺埋；書眉嘅雜訊摺喺最尾。`finish/doubts.json` 有齊每項。
5. **清走**：PDF 隔籬嘅檔抄好之後，跑 `clean_book.py` 嘅預設清理（render 留低）。單引擎、`--keep-work`、`--no-deliver` 或者抄唔到就唔清。

`final.md` 同 `doubts.md` 寫咗就係用戶嘅：再跑嗰陣，佢哋唔係上次寫嗰份（用戶改過、剔過），就搬去 `final.edited-<時間>.md`／`doubts.edited-<時間>.md`，唔蓋，RESULT 行講明。同一本書一次淨跑一個 `finish_book.py`：第二個喺寫任何嘢之前就 exit 2；收到 SIGTERM／SIGINT 即刻 exit 4，做咗嘅唔會冇。Exit：`0` 做完；`1` 一步驗唔過（log 喺 `finish/finish-book.log`），再跑都係咁；`2` 參數或者輸入錯；`3` 有頁未封；`4` 俾人停咗。

Stdout 淨係一行 `[finish] RESULT exit=N ...` 加檔案路徑；每步輸出、`[finish] step K of 5` 行同 `WARNING` 行喺 `BOOK/finish/finish-book.log`。`WARNING` 係要轉告用戶嘅嘢（抄唔到、改過嘅檔搬咗去邊、清理停咗）。

## 清走工作檔（clean_book.py）

- **第 5 步（`finish_book.py` 自己做）**：兩份交付檔都抄到 PDF 隔籬先做（`--keep-work`、`--no-deliver`、單引擎或者抄唔到就唔做）。刪流程可以由 render 再切嘅圖（封存頁 folder 入面 phase 3 嘅裁圖，淨係認名：`page-NNNN-adjudication-NN.png`、`-chart-NN.png`、`-table-unread-NN.png`、`page-NNNN-stitch-NN.png`，其他圖係用戶嘅，唔郁；`repaired/crops/`、`book-profile-crops/`）、過時嘅 `assembled.stale-*/`、流程留低嘅暫存檔同草稿（`BOOK/draft.md`，同 PDF 隔籬流程寫、冇改過嘅 `X-draft.md`）；留低文字狀態（封存頁、底稿、`repaired/`、`assembled/`、profile、manifest、`finish/`、`final.md`、`doubts.md`）、log、`backup-*/` 同任何唔係流程寫嘅嘢，同 render，方便原地修書：修書會逐頁驗 render 對 manifest，而由 PDF 再 render 淨係同一個 pdftoppm 先出同一啲 bytes。`--clean-renders` 連 render 都刪；自己跑 `clean_book.py BOOK`（第 5 步未有之前封好嘅書，或者用戶想刪 render）預設連 render 都刪，加 `--keep-renders` 先留。RESULT 行尾：清咗係 `; freed M MB`，刪到一半停咗係 `; cleanup stopped`（WARNING 行講點清埋），冇做係 `; work kept`；清唔到係 WARNING，唔當失敗。實測（du）：609 頁嘅書 635 MB 入面約 460 MB 會刪走，其中 132 MB 係 render。
- **render 刪咗之後**：再跑 `finish_book.py` 會 exit 2，嗰行寫好成條 command `survey_pdf.py PDF MANIFEST_DIR --renders-only`（`PDF` 係 manifest 記住嘅 `source_pdf`，`MANIFEST_DIR` 係 manifest 所在嘅 folder，通常係 `BOOK`）：淨係補返冇咗嘅 render，逐頁對 manifest 嘅 `render_sha256`，唔改 manifest 同 survey report；pdftoppm 出唔返同一啲 bytes 就 exit 2，講明兩個 pdftoppm 版本，本書冇得原地修。
- **`clean_book.py BOOK --all`（交付之後最後一步）**：成個 `BOOK` 連未改過嘅 `X-draft.md` 一齊刪，PDF 隔籬嘅 `X.md`、`X-doubts.md`、`X-圖/` 留低（用戶 2026-10-03 決定：交付檔就係本書，唔再原地修書）。
- **拒絕（exit 3，乜都冇刪）**：上次 `finish_book.py` 唔係 exit 0；PDF 搵唔到或者 hash 唔啱（掃描圖資料夾開嘅書冇 PDF，一定係呢個：見〈三次交付同本書目錄〉）；有 `finish_book.py` 或者其他程序仲用緊本書（`wait_for_run.py` 同 `tail -f` 唔計）；PDF 隔籬嘅交付檔唔喺度。`--all` 另外：`X.md`、`X-doubts.md` 或者 `X-圖/` 嘅圖用戶改過或者刪咗；PDF 或者交付檔喺 `BOOK` 入面；`BOOK` 有 `*.edited-*.md`、`backup-*/` 或者 `.git`；`final.md` 之後有頁重新封過。照講畀用戶知，唔好自己刪。
- 刪到一半停咗（exit 1）：照嗰行講嘅再跑同一條命令，已刪嘅列晒。`BOOK/cleaned.json` 記刪咗乜、留低乜、慳返幾多、點續（停咗一半都寫，加 `stopped`）；stdout 最尾 `[clean] RESULT exit=N DONE|STOPPED|REFUSED ...`。`--dry-run` 淨係列。

## 圖：淨係留圖片（--extract-figures）

預設唔抽圖：圖表、地圖、相入面印咗嘅字照轉寫（評測規則：圖表文字都要轉）。用戶要求先加 `finish_book.py --extract-figures`（`--single-engine` 唔接受）；加咗，第 3 步將每幅圖由 render 裁出嚟留做圖片，唔問模型、唔使 GPU（`scripts/_figures.py`）：

- **揀邊啲**：引擎 A 頁 JSON 嘅 `regions`（佢冇讀到字嘅版面區域）入面標 `chart` 或者 `image` 嘅（`header_image`、`footer_image`、`seal` 唔要），裁出嚟會重疊嘅併做一幅，細過頁面 2% 嘅唔要（條碼、花邊）。冇 `regions` 嘅舊底稿冇圖；manifest 略過嘅頁唔睇；phase 3 因為引擎 A 讀到循環而擱埋一邊嘅 block（`engineALoopsDropped`）唔留圖：封存頁淨係記 block 號，冇記框。
- **影印嘅原件**（要有引擎 B 底稿）：喺**簡體字書**（兩個引擎全部草稿嘅 `simplified_script`）入面，引擎 A 一個 block（唔計頁碼同標題）嘅繁體字多過簡體字，而引擎 B 喺嗰度冇讀，或者都讀成繁體，就當係影印嘅原件。引擎 B 讀咗未：成頁引擎 A 嘅行照閱讀次序同引擎 B 嘅字對齊一次（繁簡、異體當一樣），對得返一半或以上就係讀咗。碰到佢嘅引擎 A 區域同引擎 B 框併入去；同一頁幾個原件中間冇印刷字隔開就併做一幅。繁體字書唔做呢樣。
- **引擎 B 框住嘅圖**（要有引擎 B 底稿）：引擎 B 草稿入面淨係得 `(x,y),(x,y)` 嘅一行係佢畫嘅框（0–999 格）。一個框有頁面 2% 或以上、唔碰引擎 A 任何 chart／image 區域，又冇引擎 A 讀到嘅印刷字喺入面（有漢字或者假名，或者兩個字母以上嘅西文字；`display_formula` 唔算），就當一幅圖；碰埋嘅框併做一幅。由格嘅一角去到另一角嘅框（`(0,0),(999,999)`）唔算：佢冇講圖喺邊。
- **點裁、擺喺邊**：四邊各加頁面 1%，但唔過埋圖外面嘅字行，由 render 裁做無損 PNG，放 `BOOK/figures/page-NNNN-K.png`（`K` 係嗰頁第幾幅）；冇 render 就冇圖片。`final.md` 喺圖說（或者頁尾）嗰段之後加一行 `![](figures/page-NNNN-K.png)`，前後空行，冇 alt 字。圖行加喺修好嘅書嘅副本 `finish/combined-figures.md`，去 page marker 同 final audit（`--annotated-source`）都讀佢；`finish/figures.json` 逐幅記頁、框、錨同 `source`（`regions`、`plate`、`grounding`）。
- **原件嘅字唔入正文**：影印原件同引擎 B 框住嘅圖，框入面引擎 A 嘅字（原件 block，同引擎 B 冇讀嘅行）唔再入正文；引擎 B 讀咗嘅行（圖說）照留。修好嘅書嗰頁逐行同引擎 A 嘅行成頁對齊一次（`_figures.take_out`），一行對落要攞走嘅行嘅字多過其他行就攞走，圖行放喺第一行攞走嘅位。攞走嘅每一行原文照錄喺 `doubts.md` 嗰幅圖嘅一項（`figure-kept`；`finish/figures.json` 嘅 `removed`），清走之後 `X-doubts.md` 都仲有：冇一個字靜靜咁冇咗。攞走嘅行同上下頁接埋（`join`）嘅分頁改做分段，寫喺 `finish/boundaries.json`；phase 3、修書同 lint 對攞走咗嘅字嘅疑問照列，註明已經連圖攞走。
- **交付**：圖片抄去 PDF 隔籬嘅 `X-圖/`，`X.md` 嘅圖行改做 `![](<X-圖/page-NNNN-K.png>)`（角括號：`X` 有空格都得；`%`、`#`、`?`、`<`、`>` 寫做 `%25` 等），`X-doubts.md` 嘅連結一樣。`delivered.json` 逐幅記 SHA-256，規矩同 `X.md` 一樣：流程寫過嘅就換；用戶改過嘅先搬去 `page-NNNN-K.edited-<時間>.png`；唔係流程寫嘅唔郁。`clean_book.py` 預設清理唔郁 `BOOK/figures/`（`figures/` 嘅圖唔係抄去 `X-圖/` 嗰版就拒絕），`--all` 留低 `X-圖/`；`X-圖/` 入面有圖俾用戶改過或者冇咗，`--all` 拒絕。
- **限制**：淨係引擎 A 搵到嘅區域、簡體字書嘅影印原件同引擎 B 框住嘅圖先有；引擎 A 當字讀咗嘅圖（例如讀成 text 嘅樹、繁體字書入面嘅原件）冇圖片；圖入面嘅字（地名、人名、數值）冇轉寫；簡體書入面用繁體排印、兩個引擎都讀成繁體嘅印刷字（引文、腳註）都會當原件變圖，字攞走、逐行列喺 `doubts.md`。要對住原書睇。

## 疑點（doubts.md）

每個疑點記入封存頁 JSON 嘅 `doubts`（`{kind, detail}`），`auditorClean` 即係嗰頁冇疑點；run report 嘅 `doubtPages` 按類數頁。`finish_book.py` 將佢哋按類同頁碼寫入 `doubts.md`，連 detail（位置、兩個引擎讀咗乜、流程寫咗乜）。**做法一律係**：用戶對住 detail 講嘅頁同位置睇原書，要改就改 `X.md`（或者叫 agent 照 `SKILL.md` 第九節改）；流程唔會因為疑點自動刪字或者補字，agent 亦唔自己睇圖改。下面淨寫唔同嘅做法。

**要查資料嘅專名**：`research-front-matter`（題名頁、題字頁嘅專名：題字者、書名、印章；由右至左印嘅題名頁亦喺度）、`research-glyph`（淨靠字形定唔到嘅人名、地名、官職）：有網就用文字搜尋，唔改 `X.md`。

**字**：

- `TEXT-LOST`、`table-characters-lost`：兩個引擎都讀到嘅字唔見咗（表格入面嗰啲另計）。結構步寫嘅表少咗佢收到嘅表格字，亦記做 `table-characters-lost`，個表照收；正文少字先退返。
- `reading-dropped`：合併完，一個引擎讀到嘅三個字或以上冇咗（偏好、裁決答案或者次序衝突揀咗另一邊）。
- `same-reading-written-differently`：同一個讀法全書寫成幾樣，最少一處可能錯。
- `conflict`：兩個修書改動撞埋同一段，淨做咗先嗰個。
- `MARK-STANDIN`：可能係引擎 B 代標點嘅字；`MARK-FIGURE-LOST`：引擎 A 讀到嘅數字唔見咗。
- `run-one-engine`：數字、拉丁字、假名或者「□」淨係一個引擎讀到，冇寫；`run-engines-differ`：兩個引擎同一位讀到唔同嘅；`run-no-place`：兩個都讀到但擺唔落。
- `arabic-figure-unwritten`：引擎 A 讀到嘅數字（或者括號入面嘅符號）定咗要寫但寫唔落佢個位。
- `HEAD-NUMBER-LOST`、`TOC-NUMBER-LOST`：標題、目錄項冇咗編號或者頁碼。
- `TEXT-TRADITIONAL`：簡體書入面淨繁體先有嘅字，可能讀錯，亦可能真係咁印（例如人名）。
- `FURN-HEAD`、`FURN-SUFFIX`、`FURN-FOLIO`：正文可能仲有書眉、卷次後綴、頁碼；`FURN-ANSWER`：書眉位嘅裁決答案；`HEAD-FURNITURE`：標題其實係書眉或者頁碼；`ANSWER-PROSE`：裁決答案似一段話。
- `nothing-unconfirmed`：裁決話冇字，但裁圖證實唔到，留咗引擎讀法。
- `insertion-unjudged`：一兩個字淨係一個引擎讀到，裁決預算用完冇問，留咗；`insertion-partial`：裁決讀到嘅字少過引擎，留咗引擎讀法。
- `deletion-refused`：裁決答案會刪走一個引擎讀到嘅三個字或以上，冇收（淨係回覆截斷咗嘅引擎 B 讀到嘅字唔計；答案係截斷嗰邊讀法嘅一部分，就淨係同答案有字相同嘅另一個引擎讀法先留）。
- `neighbour-copy`：裁決答案正係隔籬兩個字之內已有嘅字（睇咗隔籬），冇寫；或者淨一個引擎讀到嘅一段緊貼一模一樣嘅一段（可能讀咗兩次）：要睇印咗一次定兩次。
- `answer-script-converted`：簡體書入面裁決用繁體答，寫咗簡體。
- `extra-copy`：一段字出現次數多過兩個引擎各自讀到嘅，可能放多咗一次，亦可能兩個引擎各漏一處。
- `moved-placed-once`：兩個引擎喺唔同位置讀到嘅同一段，淨放咗一次：核次序同份數。
- `unsupported-reading`：一段淨一個引擎讀到、自己重複嘅字丟咗。
- `non-cjk-text-not-transcribed`：冇漢字嘅一頁（西文、數字、算式），引擎讀到字但轉錄係空。要補就對嗰頁 `--overwrite --pages N` 重做或者手補。
- `engine-draft-missing`：嗰頁缺引擎 A 嘅底稿（或者加咗 `--allow-missing-engine-b` 而缺引擎 B 嘅），淨用另一個轉錄；`single-engine-review`：成本書得一個引擎。補返底稿之後嗰頁報 blocked，要 `--overwrite --pages N`。
- `engine-a-loop-dropped`：引擎 A 一段讀法係循環，嗰度淨用引擎 B。
- `engine-b-truncated`：引擎 B 嗰頁回覆截斷咗。佢開始不停重複之前讀到嘅字照當第二份讀法合併（重複嗰段唔合併，疑點講喺第幾個字截），之後嘅字淨係引擎 A 讀到；淨係佢讀到嘅字，裁決話冇印或者讀少啲都照收（D16 唔保護）；裁決答案係佢讀法嘅一部分，而引擎 A 喺嗰度嘅讀法同答案冇一個字相同，引擎 A 嘅讀法都唔留，照寫答案（`cutOffReadingAnswered`，成頁搵唔返嗰段字就有 `reading-dropped` 疑點）；截完剩唔夠十個字先唔合併，嗰頁由頁圖轉錄。可以對嗰頁加大 `--max-tokens` 重新 OCR，再 `--overwrite` 重新校對。
- `content-fidelity-unresolved`：（`--content-fidelity on`）清點、補讀或者覆核定唔到，嗰頁照封。
- `sealed-report`：舊版封存嘅頁，有未分類嘅疑問。

**標點**：`punctuation-partial`（標點可能漏咗）、`punctuation-count-mismatch`（保留嘅標點數同 census 差過界線）、`doubled-count-mismatch`（雙圈數唔一致）、`punctuation-unread`（有幾個字之間嘅標點冇讀過）、`punctuation-left-bare`（頁上有標點，嗰段一個都冇加）、`punctuation-whole-page`（成頁一次過加，冇逐段核）、`MARK-UNBALANCED`（括號或者引號唔成對）、`MARK-REVERSED`（倒轉咗嘅「）（」）、`MARK-SHAPE`（括號形狀可疑）、`retry-marks-unverified`（重試過嘅標點段，第一次答案嘅符號寫唔返）、`heading-mark-unread`（標題入面兩個引擎都冇讀到嘅標點）、`run-mark-dropped`（寫數字嗰陣刪咗或者換咗兩個引擎都冇讀到嘅標點）、`note-markers-unverified`（引擎讀到嘅註號頁面冇或者數值唔同）、`note-unmatched`（註號同註唔係一個對一個，照原印留低）。

**分頁、標題同目錄**：`unit-not-in-scan`（目錄有、正文搵唔到嘅篇）、`leaves-missing`（頁碼跳咗：用戶對 PDF 睇係咪真係缺頁；agent 唔好自己補）、`HEAD-FRAGMENT`（頁頂標題可能係上一頁未完嘅句）、`TOC-UNMATCHED`（目錄項喺正文搵唔到）、`heading-kind-unknown`（認唔到係乜嘅標題，級數照舊）、`JOIN-UNRESOLVED`（分頁位冇 label）。

**表格**：

- `table-missing`、`table-disputed`、`table-text-left-out`：表格唔見咗、版面有爭議、有字冇寫入。
- `table-edge-text`：表前後嘅字：可能係標題、附註，亦可能讀多咗。
- `table-marks-unread`：表入面印咗嘅符號冇一步讀過。
- `table-grids-misaligned`：兩個引擎嘅格仔唔對行，定唔到跟邊個。
- `table-unread-confirmed`：重建嘅表寫咗兩個引擎都冇讀到、淨裁決讀到嘅字（headerCharacters）。
- `table-orientation-turned`：結構步自己寫嘅表，引擎 B 嘅閱讀次序同分行定到要轉，就轉咗（D23 `structure-table-oriented`）。但兩個數都係引擎 B 一次讀法：佢逐條橫行讀直排表、一行讀幾格，寫啱咗嘅表都會數成要轉；頁面上冇其他嘢核得到（合計唔算：佢印喺欄腳定自成一欄，對得上嘅軸唔同），`tableOrientation` 會寫明淨係憑引擎 B。每次轉都有呢個疑點，要對住圖睇有冇轉錯。
- `table-orientation-undecided`：方向定唔到，照重建咗嘅樣留（結構步自己寫嘅表：引擎 B 嘅次序同分行定唔到同一個轉法，就照答案留）；`cell-direction-undecided`：一格字嘅讀向定唔到，照引擎讀出嚟嘅樣留。兩次睇圖答案唔一致都係呢個疑點：兩個引擎讀橫排格一律由左至右，唔可以做第三票；直排書入面由右至左印嘅橫排表頭最常中，要對住圖核。
- `table-note-marker-unverified`：格入面嘅註號係引擎 A 讀嘅（大過二十嘅圈碼成日讀錯）。引擎用 LaTeX 包住嘅註號（`$ ^{⑲} $`、`$ \textcircled{9} $`、`$ ^{203} $`）喺頁面寫返淨註號：圈碼照讀，阿拉伯數字寫成上標數字（`²⁰³`），註腳步驟同修書照認得佢係同一個註號；疑點後面會列出寫咗乜。似算式嘅（`m$^{2}$`、數字後面嘅阿拉伯數字上標）唔郁。
- `table-rebuild-refused`：重建唔收，用退返嘅版本；`table-rebuilt-unvouched`：重建嘅表冇第二個讀法證實。
- `table-continuation-merged`：跨頁表併咗做一張，對住 `page-NNNN-stitch-01.png` 核接口；`table-continuation-undecided`：似續表但定唔到，冇併；`table-continuation-unaligned`：欄數唔同、對唔到格，冇併；`table-continuation-cell-joined`：被頁切斷嘅一格字駁返埋；`table-continuation-blocked`：其中一頁嘅表冇寫成表，接唔到：修好嗰頁再跑一次 stitch。

**圖**：`figure-kept`（`--extract-figures`：淨留圖片，入面嘅字冇轉寫）。

**格式同步驟**：`formatter-rejected`、`structure-rejected`（格式化或者結構步被拒，嗰頁用退返嘅版本）、`final-audit`（`final.md` 嘅結構檢查錯誤：逐項對住 `final.md` 改）、`AUDIT-ANNOTATED`（拼書稿嘅結構檢查錯誤）、`single-engine-boundary`（單引擎：同前一頁嘅接駁冇核實，留咗分段）、`gate-failed`（本書量唔到，一類修書規則冇做：唔使做嘢）、`detector-unmatched`（自動檢查見到問題，但冇規則處理）。

## 用戶決定嘅規則（decisionChanges）

有啲規則係用戶揀咗先開（D1、D2……，同有名嘅規則）；而家一律開。每改一處字都記入封存頁 JSON 嘅 `decisionChanges`：`decision`、`rule`、位置（`block`、`contextBefore`／`contextAfter`、`mergedOffset`，成頁 formatter 做嘅頁記 `pageOffset`）、兩個引擎嘅讀法、`after`（而家寫咗乜）同 `evidence`；run report 嘅 `decisionChanges` 按決定數埋。佢哋唔係疑點，用戶想知某處點解咁寫先睇。

- `D1`（`mark-not-character`）：一個引擎讀成字、本書底稿顯示係代標點嘅字形，當標點，唔入字。
- `D2`（`moved-placed-once`）：兩個引擎用唔同次序讀到嘅同一段，淨放一次（引擎 A 嘅位置）。
- `D3`（`insertion-to-crop`）：一兩個字淨係一個引擎讀到，裁圖問。
- `D4`（`dot-leader`、`dot-leader-rewritten`）：點串一律寫「……」；`D5`（`bullet`）：保留項目符號。
- `D15`（`furniture-insertion`）：淨一個引擎讀到嘅本書家具（書眉、頁碼）唔入字。
- `D16`（`answer-deletion-guard`、`answer-accepted-reading-nearby`、`answer-accepted-cut-off-reading`、`answer-copies-neighbour`）：裁決答案會刪走一個引擎三個字或以上就唔收；嗰段字喺附近已經有就收；淨係回覆截斷咗嘅引擎 B 讀到嘅字唔保護，裁決話冇印或者讀少啲都照收（兩個引擎都讀到嘅字照保護；答案係截斷讀法嘅一部分，就淨係同答案有字相同嘅完整讀法先保護，冇就照寫答案，記 `cutOffReadingAnswered` 同 `unsharedWholeReadings`；成頁搵唔返嗰段字就有 `reading-dropped` 疑點）；答案抄隔籬就唔收。
- `D20`（`rebuild-lock-both-readings`）：重建表格准用兩個引擎讀到嘅字。
- `D21`（`nomination-witness-marks-left-out`）：淨靠拎走代標點字嘅證人先提名而收咗嘅表。
- `D22`（`rebuild-region`）：重建連埋同一個表拆咗出去嘅 block。
- `D23`（`rebuilt-table-oriented`、`structure-table-oriented`）：重建嘅表問方向之後轉咗；結構步自己寫嘅表，引擎 B 嘅閱讀次序同分行都定到佢要轉（例如直排表照印寫成一個欄位一行），就轉咗做閱讀次序，每次都有 `table-orientation-turned` 疑點。
- `D24`（`verdict-propagated`）：本頁判過係錯讀嘅字，喺其他位都問。
- `headerCharacters`（`table-unread-confirmed`）：重建嘅表最多兩個位寫兩個引擎都冇讀到、裁決喺裁圖讀到同一個嘅字（`--no-unread-table-characters` 關）。
- `keepArabicNumerals`（`arabic-figure-kept`）：引擎 A 讀到、而佢係嗰處印刷唯一讀法嘅數字寫入（`--no-keep-arabic-numerals` 關）。
- `learnedHeads`（`learned-head-reading`、`learned-head-block`）：按位置學到嘅書眉唔入字（`--no-learned-heads` 關）。
- `loopDetector`（`reasoning-loop`）：模型推理逐字重複三次就放棄嗰級、升下一級（`--no-loop-detector` 關）。
- `neighbourGuard`（`neighbour-copy`）：裁決答隔籬嘅字唔寫（`--no-neighbour-guard` 關）。
- `textLoss`（`nothing-answer-unconfirmed`、`lopsided-split`、`table-edge-text`、`note-marker-both-engines`、`note-marker-restored`）：防漏字：裁決答「無」證實唔到就留讀法；兩個讀法差太遠就兩個都留；表邊嘅字寫喺表前後；兩個引擎喺同一位讀到嘅圈號註號（①②），標點步漏咗就寫入，連埋交畀結構步；結構步漏咗嘅圈號註號寫返原位；註頭嘅圈號俾結構步擺咗去上一行尾或者自己一行，有證據係註頭（同一個號喺頁內另有註號，或者嗰行係註文 block 嘅字：引擎 A 標做註腳、或者喺頁碼嗰條邊）先搬返去註文行首（`evidence.moved`），冇證據就照結構步擺嘅位，淨係補返漏咗嘅。
- `contentFidelity`（`content-recovery`、`coverage-review`）：完整性 v2 覆核改嘅字，`evidence.adopted` 講有冇採用。

## 開關

新書唔使加任何開關；以下係續跑或者用戶要求先用。

`proofread_pages.py`：

- `--no-keep-arabic-numerals`、`--no-loop-detector`、`--no-neighbour-guard`、`--no-learned-heads`：關 round-4 修正（預設開）；淨係續跑嗰陣 `ERROR resume with --no-…` 叫先加。
- `--no-unread-table-characters`：關 headerCharacters：重建嘅表寫咗冇引擎讀到嘅字就唔收。
- `--same-print-marks`（預設關）：agreedMarks 唔收嘅 block，如果兩個引擎喺每個空位讀到同一樣嘅印刷字（數字、拉丁字、註號），就照用引擎 A 嘅標點，唔問模型；比較時唔計註號，因為兩個引擎寫同讀錯註號都唔同，而註號另外還原；封條記 `samePrintMarks: "on"`，開咗嘅頁同關咗嘅頁互相唔算 current；呢啲 block 喺 `censusNotAsked` 記做 `engines-agree-print`。
- `--unmarked-page-sample`（預設關）：無標點嘅頁少問啲。成頁兩個引擎各自讀到嘅標點都唔多過兩個，而且引擎 A 讀成淨係得字（冇標點、數字、拉丁字、註號）嘅 block 多過三個，就先問最長嗰三個 block 嘅標點題，連同頁上其他 block 一齊問；三個都答冇標點，其餘嗰啲 block 照原文寫、唔問（`censusNotAsked` 記 `page-unmarked-sample`）；有一個答有標點，或者答案被拒，其餘就喺下一個 wave 照常問。Profile 見到雙圈或者旁點嘅書唔用。封條記 `unmarkedPageSample: "on"`，開咗嘅頁同關咗嘅頁互相唔算 current。適合無標點嘅古書；有標點嘅書引擎多數讀到標點，呢個開關唔會生效。
- `--content-fidelity on`：完整性 v2（預設 `off`）；`--table-orientation as-printed`：保留物理格網，唔轉直排表（預設 `auto`）。
- `--effort-policy KIND=RUNG,...`（環境變數 `PROOFREAD_EFFORT_POLICY`；預設冇，淨係 `--backend http` 用）：量度用嘅開關，新書唔使加。指定邊類題第一次用邊一級 effort 去問，例如 `census=low,format=low`；`RUNG` 係 `xhigh`、`medium`、`low` 或者 `none`，`KIND` 係 `--help` 列嘅題型（寫錯會即刻 exit 2，一頁都唔問）。`--reasoning-effort` 照舊係所有題嘅最高一級，policy 只可以調低；冇列嘅題型照用 `--reasoning-effort`，重試（`punctuate-retry`、`format-retry` 等）係另一個題型，所以唔列就即係重試用返最高一級。答案撞 `max_tokens` 照舊淨係向下一級。設咗就記入每頁封條嘅 `effortPolicy`（同 `reasoningEffort` 並排）同 run report，run report 嘅每類題多一個 `efforts`（每一級問咗幾多次）；同 effort 一樣唔入 provenance，續跑唔會因為佢唔同而重做已封嘅頁。`book_profile.py` 有同一個開關（佢問嘅題型係 `notation`）。邊類題調低唔會掉字、掉標點未量度過，要逐類對住 golden 用 `tests/run_regression.py` 比較先好用喺正式跑書。
- `--single-engine`、`--draft2-directory`、`--profile`、`--variant-rulings`：輸入；`--allow-missing-engine-b`：缺引擎 B 底稿嘅頁淨用引擎 A 轉錄（預設唔封，當 failed）；`--pages`、`--first-page`、`--last-page`、`--overwrite`：揀頁同重做；`--backend`、`--endpoint`、`--model`、`--packets-dir`、`--max-bundles`、`--max-items-per-bundle`：校對 transport；`--workers`、`--page-concurrency`、`--max-inflight`、`--max-adjudications`、`--reasoning-effort`、`--max-tokens`、`--temperature`、`--timeout`：並行同模型請求。

`book_profile.py`：`--no-learned-heads`（唔學書眉）、`--no-doubled-notation`、`--force-calibrate-engines`（短檔都校準引擎）、`--probe-pages`、`--pages`、`--max-crops`、`--reuse-engines`（同一批頁、同一個模型就沿用上次嘅引擎量度）、`--no-inflight-cap`、`--workers`。

`repair_book.py`（`finish_book.py` 叫，agent 唔使直接用；每個關一個階段）：`--no-furniture`（書眉、卷次、頁碼、頁邊漏字）、`--no-marks`（引擎 B 代標點字、直排括號、標題符號）、`--no-brackets`（編者括號〔〕）、`--no-contents`（目錄同篇）、`--no-title-page`（由右至左印嘅題名頁）、`--no-levels`（標題級數）、`--no-paragraphs`（頁內分段）、`--no-joins`（分頁位 label）、`--no-runs`（兩個引擎都讀到但頁面冇嘅數字、拉丁字、假名、□、註號同標點）、`--no-forms`（中文入面嘅半形標點轉全形、冇引擎讀到嘅著重號）、`--no-script`（簡體書嘅繁體裁決答案）、`--no-words`（同一讀法幾種寫法）。另有 `--output`、`--resume`、`--pages`、`--workers` 同輸入路徑（`--assembled`、`--page-sources`、`--proofread`、`--renders`、`--ocr-a`、`--ocr-b`、`--profile`、`--page-manifest`）。

`finish_book.py`、`clean_book.py` 嘅開關見 `SKILL.md` 第七節。

## 修書計數（_book_lint.py）

修書前後各數一次，`repair-report.json` 記；修書之後仲有嘅寫入 `doubts.md`。Book model 嘅閘唔過嘅計數唔量（講明）。

- `FURN-HEAD`、`FURN-SUFFIX`、`FURN-FOLIO`、`FURN-ANSWER`：正文留咗書眉、卷次後綴、頁碼，或者書眉位嘅裁決答案。
- `MARK-STANDIN`、`MARK-REVERSED`、`MARK-UNBALANCED`、`MARK-SHAPE`、`MARK-FIGURE-LOST`：代標點字、倒轉括號、唔成對、形狀可疑、引擎 A 嘅數字冇咗。
- `HEAD-FURNITURE`、`HEAD-LEVELS`、`HEAD-NUMBER-LOST`：標題係家具、標題級數分佈（淨係報，唔算錯）、標題冇咗編號。
- `TOC-NUMBER-LOST`、`TOC-UNMATCHED`：目錄項冇咗編號或者頁碼、喺正文搵唔到。
- `HEAD-FRAGMENT`、`JOIN-UNRESOLVED`：頁頂標題可能係上頁半句、分頁位冇 label。
- `AUDIT-ANNOTATED`、`AUDIT-FINAL`：拼書稿、`final.md` 嘅結構檢查錯誤。
- `ANSWER-PROSE`：裁決答案似一段話。
- `TEXT-LOST`：兩個引擎都讀到嘅一段字冇咗（一段計一次）。
- `TEXT-TRADITIONAL`：簡體書入面淨繁體先有嘅字。

## agent 規矩嘅實測根據

`SKILL.md` 叫 agent 做或者唔做嘅嘢，好多係量出嚟嘅：

- **B 套唔好自己覆核**：每張讀入對話嘅圖都塞入 Qwen Code 嘅 context，冇得清走。實測（Qwen Code 跑 30 頁）：新舊兩版 skill 都喺約 59 分鐘封晒 30 頁，之後 agent 自己切裁圖、用 `zoom_image`／`read_file` 讀入對話、自己裁字，新版做咗 1 個鐘、舊版 2 個鐘，最後 Qwen Code 以 `Context is too large to send safely after automatic compression … hard limit: 177000 … COMPRESSION_FAILED_OUTPUT_TRUNCATED` 退出（exit 1）：兩次都冇拼到書，冇 `final.md`。
- **圖要少**：實測一本六百幾頁嘅書，agent 認版式睇咗 40 幅頁圖、行咗 33 輪、用咗 13 分鐘，GPU 幾乎閒置，而版式睇 5 頁已經一樣清楚。Qwen Code 對話一有 20 幅圖就將舊圖換做文字引用，由嗰輪起 prefix cache 冇再中過，每輪由幾秒變 24–76 秒。
- **GPU 交接唔准放背景**：實測（2026-09-23，Qwen Code）：agent 將交接放咗背景，下一輪即刻撞正 server 停咗（`ECONNREFUSED`），重試 7 次、約 76 秒之後退出，退出時仲殺埋交接（成個 process group 先 SIGTERM、200 毫秒後 SIGKILL），server 冇開返。
- **等嗰陣淨係等、對話保持短**：agent 自己同呢啲步驟用緊同一個 server。實測（Qwen Code，一本書）：99 分鐘嘅 book profile 期間 agent 行咗 84 輪，每輪大約 11 萬 prompt token，大半冇用到 prefix cache，加埋 930 萬 token，估計食咗 server 大約三成時間；phase 3 開 2 條 lane，agent 喺旁邊行咗 73 輪嗰次每個鐘得 7 頁，冇 agent 嗰次每個鐘 17–21 頁（server 嘅排隊冇記錄，差距係咪全部因為 agent 未證實）。另外 agent 每行一輪，server 都要喺同一個 KV pool 預留佢成個對話再加佢嘅輸出上限：實測一本六百幾頁嘅書，agent 對話大嗰陣每輪 8 條 lane 都要停晒等佢，phase 3 約八分一嘅時間蝕咗喺度。
- **`finish_book.py` 一律放背景**：修書唔問模型，實測四本書每本 15 至 166 秒，加埋其他步都可能過 harness 時限，前景 call 一到時限就俾人殺咗。
- **`--max-inflight` 等於 server 嘅 lane 數**（預設係 `env.sh` 嘅 `PROOFREAD_MAX_INFLIGHT`，冇就跟 `--workers`）：實測同一本書 2 條 lane 每鐘約 21 頁，8 條 lane 每鐘約 60 頁。
