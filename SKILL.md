---
name: jyut-ocr-proofread
description: 將歷史中文 PDF（或者掃描圖資料夾）忠實轉成 Markdown：render、兩個本機 OCR 引擎（HunyuanOCR、PaddleOCR-VL）、book profile、逐頁校對、修書、交付，每步一個 script。校對預設由當前 agent 答 packet 題，亦可以交本機模型（http）。保留異體字、舊標點、註號、表格同頁面結構。Use when asked to OCR, transcribe, 校對, proofread or clean a historical Chinese PDF, scanned page images or OCR Markdown.
---

# Jyut OCR 校對

呢份係跑書嘅流程表：字由兩個 OCR 引擎同流程定，校對者淨係答流程問嘅題。你按次序跑 command、等、交付，喺寫明嘅位做決定。

## 一、開工先定邊個校對

兩套規矩，開工嗰陣揀一套，成本書用到尾：

- **A. 當前 agent 校對（預設）**：`--backend packet`。要睇圖嘅題目寫成 bundle，腳本 exit `10`；你睇圖作答，照六交答案。平台開到 subagent 就開工嗰陣定 `N`（你同時開到幾多個答題者），第 3、4 步加 `--max-bundles N`：題目分夠 `N` 份，一次過派晒；冇 subagent 就唔使加。
- **B. 本機模型校對**：`--backend http`（`book_profile.py`、`proofread_pages.py` 每次都加），腳本經 `PROOFREAD_ENDPOINT`、`PROOFREAD_MODEL` 直接問模型，圖唔入你嘅 context；env.sh 冇 `PROOFREAD_ENDPOINT` 就話畀用戶知，唔好自己估；冇 `PROOFREAD_MAX_INFLIGHT`（server 一次跑幾多個請求）就開工前問用戶，唔好估。用戶要求就用；**你本身就係嗰個本機模型就一定要用**：`JYUT_OCR_AGENT_IS_LOCAL_MODEL=1`（每個 call 照三加），或者 Qwen Code 嘅 `QWEN_CODE_MODEL` 等於 `PROOFREAD_MODEL` 而 endpoint 喺本機，腳本會自己認出，預設 http、拒絕 packet。
- **B 套唔准自己覆核**：由開工到交付之後，你**唔准**開頁圖或者裁圖、用 `zoom_image` 放大、用 `read_file` 讀圖，唔准讀 sealed `.md`／`.json`、run report 或者成段 log 嚟搵錯，唔准自己裁定字或者手改書，亦唔准叫 subagent 代你做。淨係跑 script、讀佢哋最尾嘅 RESULT 行。每幅讀入對話嘅圖都留喺 context：實測 Qwen Code 咁做撞 `hard limit: 177000` 退出，冇 `final.md`。

## 二、規矩（兩套都守）

- 以 PDF 圖像為最終依據：保留原有異體字、舊字形同舊式標點（頁面印乜字形就寫乜字形），唔好靜靜正規化，亦唔好過度古化；唔好憑空補字；原書嘅 `[]`、`〈〉`、`□` 呢類符號唔可以當 residual 一律刪；既有轉錄（連你自己上一版）唔係 ground truth，只係底稿。
- 輸出：只刪書眉、頁碼、版心／書口、印章、索書號、條碼同館藏印；正文數字、註號、圖表入面嘅字同表外說明照轉。印出嘅 `*` 寫 `\*`，雙圈句號寫 `。。`。註：引用 `[^n]` 留喺原位，定義預設留喺頁底，同一原印號再用就寫 `[^n-2]`，得一半就留一半。表：橫排表照原行列；直排表將由右至左嘅直欄變成由上至下嘅行，唔按內容重排；冇表頭或者有合併格用 HTML `<table>`；跨頁續表合併。交付稿冇頁碼分隔。
- **唔可以話「已逐頁視覺校對」「已逐頁同 PDF 對照」**：流程淨係喺佢問嘅位睇圖。定唔到嘅位喺 `doubts.md`，冇列出嚟嘅字唔代表一定啱。
- 唔停低問人：疑點由流程問校對者，異體由規則揀（異體亦唔使人裁），final audit 嘅錯誤亦當疑點照交付；定唔到嘅寫入 `doubts.md`，成個 run 冇一步等人。題多、要等好耐、要用好多 token 都唔係停低問用戶嘅理由：一直做到交付。
- 引擎冇裝好，runner 出 ERROR 講缺乜：照講畀用戶知，唔好改用其他引擎或者其他 OCR 工具。
- 唔好成份讀 `references/` 入面嘅檔：照十淨係讀嗰一節。

## 三、本書目錄同交付檔

- 首次用先跑 `./install.sh --env-only --check`（即係 `scripts/install.py`）；未就緒照 `references/install.md` 處理。macOS 只支援原生 Apple Silicon，首次安裝先問用戶：`--engine apple-vision`（純 Vision，唔下載 HF 模型）或者 `--engine hunyuan-paddle`（下載兩個引擎）。舊名稱須明確重選；唔代用戶揀，唔自動轉換舊書。長嘅下載先背景跑 `--prepare-only`，做完再前景跑 `./install.sh --env-only` 試跑；純 Vision 冇模型下載。
- 每個 shell call 都係新 shell，開頭寫：`. "${XDG_CONFIG_HOME:-$HOME/.config}/jyut-ocr/env.sh"; cd SKILL_DIR`（呢份檔所在目錄）。你本身就係本機模型（一 B）就寫 `. "${XDG_CONFIG_HOME:-$HOME/.config}/jyut-ocr/env.sh"; export JYUT_OCR_AGENT_IS_LOCAL_MODEL=1; cd SKILL_DIR`，Qwen Code 都照加：上一個 call export 嘅唔會留低。下面 `BOOK`、`PDF` 換做實際路徑（有空格就加引號）。
- `BOOK` 開喺 `"${JYUT_OCR_WORK_ROOT:-$HOME/jyut-ocr-books}/<名>"`，**唔好開喺 PDF 隔籬**（render 成幾 GB，PDF 嗰個資料夾可能係用戶嘅 git repo）。名用冇聲調嘅粵拼或者簡單英文，唔好用普通話拼音。
- 用戶睇 PDF 隔籬嘅 `X-draft.md`（未校對草稿）、`X.md`（定稿）、`X-doubts.md`（疑問清單），加咗 `--extract-figures` 先有 `X-圖/`：腳本自己抄，抄唔到嗰行會講原因。由掃描圖資料夾開嘅書冇 PDF：唔抄，用戶睇 `BOOK/draft.md`、`BOOK/final.md`、`BOOK/doubts.md`（抽圖就加 `BOOK/figures/`），`BOOK` 唔清走。

## 四、點跑：一步一個 command

用戶一次過畀兩個或以上嘅 PDF：唔好逐本行下面六步，照本節尾〈幾個 PDF 一齊做〉。

1. **Render**（前景，純 CPU）：`"$JYUT_OCR_PYTHON" scripts/survey_pdf.py PDF BOOK`（第二個參數係 `BOOK`，唔好畀佢 `BOOK/renders`；現成掃描圖：`"$JYUT_OCR_PYTHON" scripts/survey_pdf.py BOOK --images-dir DIR`）。A 套可以逐個睇 `BOOK/survey-report.json` 嘅 `blank_pages` 全頁一眼，真係空白先喺 `page-manifest.json` 嗰頁加 `"marker_expected": false`；B 套唔睇，照正常頁跑。
2. **OCR**：`env.sh` 有 `OCR_PHASE_RELEASE_CMD`（本機模型佔住 GPU）就照五嘅交接前景跑 `"$JYUT_OCR_PYTHON" scripts/run_ocr_phase.py -- "$JYUT_OCR_PYTHON" scripts/ocr_book.py BOOK > BOOK/ocr.log 2>&1; echo "exit=$?"; grep -F '[ocr-book] DRAFT' BOOK/ocr.log; tail -3 BOOK/ocr.log`（你唔係用呢部本機模型諗嘢，例如雲端 agent：改為背景跑 `"$JYUT_OCR_PYTHON" scripts/run_ocr_phase.py --background -- "$JYUT_OCR_PYTHON" scripts/ocr_book.py BOOK > BOOK/ocr.log 2>&1`，用 `wait_for_run.py BOOK BOOK/ocr.log` 等，成本書淨係停、開本機模型一次，見五）；冇就背景跑 `"$JYUT_OCR_PYTHON" scripts/ocr_book.py BOOK > BOOK/ocr.log 2>&1`，用 `wait_for_run.py BOOK BOOK/ocr.log` 等。佢依次做引擎 B（HunyuanOCR）、草稿 `BOOK/draft.md`、引擎 A（PaddleOCR-VL）同引擎 B 補讀。草稿一出（唔等引擎 A），log 有 `[ocr-book] DRAFT 路徑` 行，`wait_for_run.py` 嗰行尾亦有 `; DRAFT 路徑`：第一次見到就用一行話畀用戶知草稿喺邊，唔使等佢回覆；冇呢行（寫唔到）亦唔阻流程。exit 0 之後再跑 `"$JYUT_OCR_PYTHON" scripts/build_draft.py BOOK`（補讀改咗引擎 B 嘅頁）。
3. **Book profile**（長步驟；`<=4` 頁嘅短檔唔使；B 套加 `--backend http`，冇 `PROOFREAD_MAX_INFLIGHT` 先再加 `--workers N`；A 套有 subagent 就加 `--max-bundles N`）：`"$JYUT_OCR_PYTHON" scripts/book_profile.py BOOK/renders BOOK/ocr-paddle BOOK/ocr-hunyuan BOOK/book-profile.json > BOOK/profile.log 2>&1`，用 `wait_for_run.py BOOK/book-profile.json BOOK/profile.log` 等。
4. **逐頁校對**（長步驟）：`"$JYUT_OCR_PYTHON" scripts/proofread_pages.py BOOK/renders BOOK/ocr-paddle BOOK/proofread --draft2-directory BOOK/ocr-hunyuan > BOOK/proofread.log 2>&1`，用 `wait_for_run.py BOOK/proofread BOOK/proofread.log --progress BOOK` 等。B 套加 `--backend http`（冇 `PROOFREAD_MAX_INFLIGHT` 先再加 `--max-inflight N`）。A 套：有 subagent 就加 `--max-bundles N`；呢本書未有封好嘅頁（`BOOK/proofread` 入面冇 `page-*.json`）就再加 `--same-print-marks --unmarked-page-sample`（兩個引擎一致嘅位同無標點嘅頁少問啲標點題，見 `references/long-book-workflow.md`〈開關〉），已經有封好嘅頁就照返之前嗰條 command，唔好中途加減。用戶要求先加 `--content-fidelity on`（每頁多三條整頁睇圖題，見 `references/content-fidelity.md`）。全部頁封好，佢自己接跨頁表格。缺引擎 B 底稿嘅頁唔封（exit 1，WARNING 列出頁碼）：等第 2 步做完再跑同一條。
5. **定稿**（一律放背景，唔好前景跑）：`"$JYUT_OCR_PYTHON" scripts/finish_book.py BOOK > BOOK/finish.log 2>&1`，用 `wait_for_run.py BOOK BOOK/finish.log` 等；exit 碼照七。
6. **交付同清走**：exit 0 之後，有 `WARNING` 先 `grep -n WARNING BOOK/finish/finish-book.log`；跟住 `"$JYUT_OCR_PYTHON" scripts/clean_book.py BOOK --all`（成個 `BOOK` 刪走，淨係留 PDF 隔籬嘅交付檔），再照九交付。掃描圖資料夾開嘅書唔跑佢（冇 PDF，佢 exit 3：交付檔淨係喺 `BOOK`）。

**標點融合**（量度或用戶指定先加）：第 4 步可加 `--punctuation-mode auto|split|fused`，兩套都支援。預設 `auto` 保持 A 套一次答「數標點＋插標點」、B 套分兩次問；`split` 明確拆開，`fused` 一次問晒。融合答案過唔到格式或點算檢查，就返去分開問；改字、標點數量、雙圈同導引點照原本守門，唔因為融合而放寬。兩次實驗要用全新輸出目錄；模式改咗唔可以續用另一模式嘅封頁。詳見 `references/long-book-workflow.md`〈逐頁校對（proofread_pages.py）〉。

**純 Apple Vision**：第 2 步直接跑 `"$JYUT_OCR_PYTHON" scripts/ocr_book.py BOOK --engine apple-vision`，唔包 `run_ocr_phase.py`，唔加 OCR 嘅 `--single-engine`，冇 HF 模型／Hunyuan 補讀／未校對草稿。跳過第 3 步；第 4 步用 `"$JYUT_OCR_PYTHON" scripts/proofread_pages.py BOOK/renders BOOK/ocr-vision BOOK/proofread --single-engine`；第 5 步用 `"$JYUT_OCR_PYTHON" scripts/finish_book.py BOOK --single-engine --ocr-a BOOK/ocr-vision`。B 套校對加 `--backend http`；工作檔保留，唔跑 `--all`。

**Hunyuan＋Paddle**：第 2 步可明確加 `--engine hunyuan-paddle`，其餘按上面雙引擎步驟。Linux／WSL2 保留單引擎例外：第 2 步用 `ocr_book.py BOOK --engine hunyuan`（有模型交接就包 `run_ocr_phase.py`）；跳過第 3 步，第 4、5 步按純 Vision 單引擎命令，但底稿目錄用 `BOOK/ocr-hunyuan`。macOS 唔接受 Hunyuan-only。

**幾個 PDF 一齊做**（`ocr_batch.py`）：所有書嘅 OCR 喺一個 call 入面逐本讀（有交接就係一個窗口，本機模型淨係停、開一次）；一本書讀完就即刻校對，唔等其餘嘅書；所有書嘅題目夾埋一輪派。三條 command，依次係「開批」「OCR」「逐輪」，`BATCH` 係開批嗰條印嘅路徑：

- **開批**（前景，即刻返回；render 佢自己喺背景做）：`"$JYUT_OCR_PYTHON" scripts/ocr_batch.py A.pdf B.pdf ...`。每個 PDF 一個普通嘅 `BOOK`，開喺 `JYUT_OCR_WORK_ROOT` 之下，名佢自己起。佢印 `[batch] BATCH 路徑`，同埋後面兩條寫成嘅 command（`NEXT 1`、`NEXT 2`）：照抄嚟用。
- **OCR**（一個 call 讀晒全部書）：照 `NEXT 1`。冇 `OCR_PHASE_RELEASE_CMD` 就背景跑 `"$JYUT_OCR_PYTHON" scripts/ocr_batch.py --ocr BATCH > BATCH/ocr.log 2>&1`；有就照五包 `run_ocr_phase.py`：你唔係用嗰部本機模型諗嘢就加 `--background` 放背景，你係（一 B）就前景分輪，exit 75 再叫。放咗背景就唔使等佢：即刻去逐輪嗰條。
- **逐輪**（前景，tool call timeout 開到最大；一個 call 最多等 540 秒，淨係印幾行）：`"$JYUT_OCR_PYTHON" scripts/ocr_batch.py --round BATCH --answered-by MODEL`（`MODEL` 照六第 4 步；佢當你同時開到 20 個答題者，平台開唔到咁多先加 `--max-workers N`；B 套改為 `--round BATCH --backend http`，全部書 OCR 完先開始校對）。佢自己行每本書嘅第 3 至 6 步：步驟喺背景行，唔使你開、唔使 `wait_for_run.py`；派工同交答案都係佢做，唔使照六跑 `packet_dispatch.py`。照最尾 `[batch] RESULT exit=N`：
  - **10** 有題要派：`ANSWERERS K` 行講幾多個、喺邊個目錄。每個 `worker-KK.txt` 開一個答題者，**一次過開晒**，prompt 淨係一句 `Read <嗰個檔嘅完整路徑> and do exactly what it says.`；**開晒就即刻再跑同一條，唔使等佢哋返嚟**（亦唔好中途停佢哋）：位滿咗佢自己會等，邊個答題者寫好答案佢就補派新嘅檔，唔會將同一包派兩次。冇 subagent：你自己就係唯一嘅答題者，逐個檔順序照做，全部寫好先再跑同一條。
  - **75** 未有新題要派（仲讀緊書、行緊步驟，或者答題者未答完）：即刻再跑同一條，description 寫第幾次；
  - 開答題者嗰陣平台話已經滿（concurrent limit），或者個別答題者失敗：唔使理嗰個檔，照舊再跑同一條，佢嗰份遲啲會再派。**全部**答題者都冇咗（平台通知佢哋 failed，例如斷線）：下一次加 `--answerers-gone`（一次就夠），未答嘅即刻再派；
  - **4** 仲有書未讀，但 OCR 嗰個 call 已經完咗：嗰行抄咗佢最尾嘅 RESULT，照五嘅 exit 碼處理，再開 OCR 嗰條；
  - **0** 全部書交付晒：每本嘅 `[finish] RESULT` 行、交付檔同 `[clean] RESULT` 行都印咗，照九逐本講；
  - **1** 做完但有書失敗：`failed:` 行講邊本、點解，其餘嘅書照樣交付咗。照實話畀用戶知；原因改好先加 `--retry-failed` 再跑（OCR 失敗嘅仲要再開 OCR 嗰條）；
  - **2** 參數錯。
- 一批書嘅題可以過萬條，照做：題多、要幾個鐘都唔係停低問用戶嘅理由。你個 session 嘅 token 預算唔夠做晒，就做到差唔多用完先停，講明停喺邊：之後任何 session 跑返逐輪嗰條就接得返，乜都唔使重做；唔好因此自己改用本機模型或者淨係交草稿。
- 用戶想快啲、叫你搵多個 session 幫手：另一個 session 跑同一條逐輪 command，加 `--caller 名`（每個 session 自己一個名，原本嗰個唔使加），兩邊各自有 `--max-workers` 個位，同一包唔會派畀兩邊。冇人叫就唔好自己開。
- 邊本書去到邊：`"$JYUT_OCR_PYTHON" scripts/ocr_batch.py --status BATCH`。交付咗嘅書佢自己清走 `BOOK`（即第 6 步；加 `--keep-work` 就留），失敗嘅書留低。單引擎路線：開批嗰條加 `--engine apple-vision` 或者 `--engine hunyuan`，佢自己用上面兩段嘅單引擎 command。細節見 `references/long-book-workflow.md`〈幾個 PDF 一齊做（ocr_batch.py）〉。

## 五、長步驟同 GPU 交接

- 長步驟放背景（Qwen Code 嘅 `is_background`），輸出寫落新 log；跟住前景叫 `"$JYUT_OCR_PYTHON" scripts/wait_for_run.py OUTPUT LOG`，tool call timeout 開到最大（Qwen Code `600000`）。佢唔問模型，一次最多等 570 秒，淨係印一行；`env.sh` 有 `OCR_PHASE_UNLIMITED_SHELL=1`（見下面）就唔設 timeout，加 `--timeout 43200`，一個 call 等到做完。等嗰陣淨係等：唔好 tail log、唔好讀已封存嘅頁。對話保持短，唔好將 log、JSON 成段貼入對話：B 套你每行一輪都同校對搶同一個 server。`wait_for_run.py` 嘅 exit：
  - **exit 0**：做完：嗰行講有 `WARNING` 就先 `grep -n WARNING LOG`，處理咗先去下一步；
  - **exit 75**：仲跑緊：即刻再叫同一個 command；
  - **exit 10**：等 packet 答案：A 套照六。B 套即係嗰步行咗 packet（漏咗 `--backend http`，或者腳本冇認出你係本機模型），**唔准答 bundle**：照三嘅開頭、加 `--backend http` 再跑同一步；
  - **exit 11**：等答案兼有頁 failed／blocked：A 套照六答，再按嗰行列嘅頁同原因處理；B 套同 exit 10；
  - **exit 1**：做完但有失敗：嗰行列出 failed／blocked 頁同原因；A 套再睇 `tail -20 LOG`，B 套淨係讀嗰行、將頁碼同原因話畀用戶知；唔好淨係重跑；嗰行講 `did not start` 就係 command 開唔到工，改好 command 先再開；
  - **exit 4**：未做完就停咗，或者 GPU 忙：重跑嗰步同一條 command（封咗嘅頁會留）；
  - **exit 2**：`wait_for_run.py` 自己嘅參數或者路徑錯：檢查兩個路徑，唔好因此重跑嗰步。
- 重複叫同一條 command，每次 tool call 嘅 description 寫唔同嘅字（例如第幾輪）：Qwen Code 見到連續 5 個一模一樣嘅 tool call 會當死循環。Qwen Code 退出會殺晒背景 shell：等到做完先好結束。
- **GPU 交接**（`run_ocr_phase.py`）：GPU 交接一定要喺前景行（唔准 `is_background`、`&`、`nohup`），一個 tool call 淨係放一個，timeout 開到最大；唔好將輸出 pipe 去 `tail`／`head`，先寫 log 再 `echo "exit=$?"`。照最尾 `[ocr-phase] RESULT exit=N`：**0** 做完；**75** 預算用完、模型開返咗：即刻再叫，用 RESULT 行尾 `next call:` 嗰條（加咗 `--round N+1`），照舊寫 log；**76** GPU 忙（另一份工、另一個交接，或者模型其他 client 仲有請求）：等一陣再照 `next call:` 叫，RESULT 行講點解；次次都係同一個 proxy／gateway 擋住就叫用戶設 `OCR_PHASE_CLIENT_IGNORE`；**1** runner 失敗、停咗模型都唔夠顯存（`GPU FULL`：ERROR 下面逐個列咗 GPU 上面嘅程序同釋放佢嘅命令，照抄畀用戶，**唔好自己殺或者停**），或者預算太細、載入模型都唔夠時間：讀 ERROR，照講畀用戶知；**2** 設定錯，乜都冇跑；**3** 模型冇開返（上次交接留低冇開、今次先開都開唔返，亦係 3，一頁都冇跑）：即刻話畀用戶知。交接淨係停 call 開始嗰陣開緊嘅本機模型，亦淨係開返佢：用戶自己停咗嘅模型照舊停住（RESULT 行尾 `[gpu: model not up, left as it was]`），GPU 得閒就直接跑；唔好自己開返佢。GPU 上面其他程序一律唔殺、唔停。
- **`--background`（你唔係本機模型先用）**：交接前景分輪，係因為 agent 本身用緊嗰部本機模型：指令一返回佢就要個模型諗下一步，所以每輪都要開返。你嘅模型喺第二度（雲端 agent）就冇呢個需要：`run_ocr_phase.py --background -- …` 放背景，冇時間預算，用 `wait_for_run.py` 等，一個窗口做晒，本機模型淨係停一次、開一次；個 call 中途死咗，看門程序照樣開返模型。你本身就係本機模型（一 B）**唔准用**，script 亦會拒絕（exit 2，乜都冇停）。背景嗰個 call 俾 harness 殺咗（`wait_for_run.py` exit 4）：唔好再用 `--background`，改返前景分輪。
- 交接停模型之前會等其他 client 做完，閒置嘅 session 喺窗口入面收到 503 加 `Retry-After`。背景跑緊嘅時候唔好開任何 `run_ocr_phase.py`。`OCR_PHASE_UNLIMITED_SHELL=1` 淨係用戶喺 `env.sh` 設（佢拆咗 shell 時限、試過先設）；你唔好自己設或者 export。有呢個設定：交接 call 唔好設 `timeout`，一個 call 做晒；咁樣都俾 harness 殺咗，即係設定冇生效：唔好再試，話畀用戶知，之後照有時限做。冇呢個設定：tool call timeout 照開到最大，`--max-seconds 0` 會被拒絕（要 `OCR_PHASE_UNLIMITED_SHELL=1` 講明）。

## 六、答 packet 題（A 套）

題目等緊答（exit `10` 或者 `11`）就行呢個循環，直到嗰步 exit `0`：

1. **攞派工單**：`"$JYUT_OCR_PYTHON" scripts/packet_dispatch.py PACKETS_DIR`，stdout 最尾嗰行 `[packets]` 有寫成條 command（PACKETS_DIR 係 `[packets] PENDING` 行嗰個 wave 目錄嘅上一層）。幾本書同時停咗，就將佢哋嘅 packets 目錄一次過列晒，一輪答題者服務晒所有書。佢印出 JSON：`assignments` 每份係一個答題者嘅工作，連一段現成嘅 `prompt`。
2. **派工**：平台有 subagent 就每份 assignment 開一個，`prompt` 原文照貼，**一次過開晒**，唔好逐個等。平台同時開到幾多個就加 `--max-workers N`（預設 24）；想每個答題者少啲題、快啲答完就調細 `--items-per-worker`（預設 10）。一個 wave 最多分到幾多份，由行步驟嗰陣嘅 `--max-bundles N` 決定（四；冇加就係 8）。開咗就等齊佢哋返嚟：未返晒唔好行第 4 步，亦唔好中途停佢哋（早咗行只會多一輪）。答題者淨係讀 `references/packet-worker.md` 同自己嗰幾包，唔載入技能、唔跑 script。預設用返你同一個模型；用戶指定先用其他模型。
3. **冇 subagent**：自己照 `references/packet-worker.md` 逐包答：每題睇佢嘅 `image_path`（裁圖或者整頁），照 `system`、`user_text` 嘅格式答，寫入 `expected_answers_file`：`{"answers": [{"request_id": "...", "answer": "..."}]}`，每個 `request_id` 一次；冇量度到嘅用量唔好寫。每題都要答，睇唔清唔係停低嘅理由：照嗰題自己嘅指示答（裁決題影唔到個位就寫 `定案：睇唔到`）。
4. **收工**：答題者返晒之後再跑第 1 步嗰條 command。仲有 `assignments` 就再派（有人冇寫齊）；冇就加 `--apply --answered-by` 你確實嘅答題 model ID 再跑一次，佢會交晒答案。 `--model` 只係 packet 標籤，唔會揀答題 agent；要指定模型及 effort，喺實際啟動 agent 嗰度設，並保留啟動記錄。唔可以靠標籤聲稱用咗某個模型；未量度到嘅 provider tokens 留空。
5. **重跑同一條步驟 command**。又 exit `10` 就返去第 1 步；一本書通常三四輪。

- 平台唔畀你睇到自己嘅 model ID：`--answered-by` 寫平台顯示嗰個模型名，唔好估。
- 答錯咗：`apply_packet_answers.py` 嗰條 command（派工單 `apply` 有寫）加 `--amend --reason "..."`；已封嘅頁要 `--overwrite --pages N` 重做先用新答案。
- 唔准手改 `answers.jsonl` 或者封存頁；bundle 冇問嘅頁唔使自己覆核。每種題點答、限流點處理見 `references/subagent-orchestration.md`。

## 七、`finish_book.py` 同 `clean_book.py` 嘅 exit 碼

- **exit 0**：做完，照第 6 步；
- **exit 3**：有頁未封：照嗰行講嘅頁跑第 4 步（加 `--pages`，同埋呢本書要加嘅 `--no-…`），再跑第 5 步；
- **exit 4**：俾人停咗：再跑同一條；
- **exit 1**：有一步失敗：`tail -20 BOOK/finish/finish-book.log`，照實話畀用戶知；唔好淨係重跑，亦唔好自己拼書、改封存頁、切裁圖或者手寫 `final.md` 嚟補；
- **exit 2**：參數或者輸入錯（封存頁封完改過、引擎掉轉咗等），或者同一本書已經有一個 `finish_book.py` 跑緊（第二個乜都唔寫就 exit 2：用 `wait_for_run.py` 等返第一個）。

開關（新書唔使加）：`--proofread`、`--renders`、`--ocr-a`、`--ocr-b`、`--profile`、`--page-manifest`（輸入唔喺慣用位置先用）、`--single-engine`、`--workers`、`--extract-figures`、`--renumber-footnotes`、`--relocate-footnotes`（用戶要求先加）、`--no-deliver`、`--keep-work`、`--clean-renders`。

`clean_book.py BOOK --all`：0 做完；1 刪到一半停咗：再跑；3 拒絕、乜都冇刪（例如用戶改過 `X.md`，`BOOK` 有 backup、`*.edited-*.md` 或者 `.git`）：照講畀用戶知，唔好自己刪，亦唔好為咗清走而改 `X.md`；2 參數錯。另有 `--dry-run`、`--keep-renders`。

## 八、續跑（斷咗、新 session）

1. 搵返 `BOOK`：`JYUT_OCR_WORK_ROOT` 入面 `page-manifest.json` 嘅 `"source_pdf"` 係嗰個 PDF。**唔好重跑 `survey_pdf.py`**：佢會重寫 manifest。
2. 由上而下第一個啱嘅：有 `BOOK/finish/` → 第 5 步；有 `BOOK/proofread/` 或者 `book-profile.json` → 第 4 步；有 `BOOK/ocr-hunyuan/` 或者淨係 manifest → 第 2 步（照四.2 揀：有 `OCR_PHASE_RELEASE_CMD` 就一樣經五嘅交接前景跑同一條 `run_ocr_phase.py` command，唔好直接跑 `ocr_book.py`）。每個 script 都跳過已經封好嘅頁；A 套未答嘅 wave 照答。
3. 舊版技能封好嘅頁同 OCR 底稿照收，唔重做（限未變嘅原生 Paddle／Hunyuan 輸入）：run report 標 `legacy`。Vision／MLX 要符合新版座標、版面同引擎身份封條；舊 Vision 原始座標唔當新版版面用。JSON 版面或者 backend 變咗會拒絕續用，照 ERROR 處理指定頁，唔自動覆蓋。
4. 第 4 步一開始 exit 2，`ERROR` 以 `resume with --no-…` 開頭：照嗰行加齊，之後呢本書每次都加；唔好 `--overwrite` 成本，唔好重跑 `book_profile.py`。
5. 一批書（四〈幾個 PDF 一齊做〉）：`BATCH` 喺 `JYUT_OCR_WORK_ROOT` 嘅 `batches/` 入面；同一堆 PDF 再跑開批嗰條亦會認返同一個 `BATCH`，乜都唔重做。跟住直接跑逐輪嗰條（新 session 接手就第一次加 `--answerers-gone`：上一個 session 開嘅答題者已經冇咗）：佢講每本書去到邊；exit 4 就再開 OCR 嗰條。

## 九、同用戶講嘢

- **交付要短**：`X.md`、`X-doubts.md` 喺邊（冇抄到就講 WARNING 嘅原因；掃描圖書係 `BOOK/final.md`、`BOOK/doubts.md`，講埋 `BOOK` 留低咗）、入書幾多頁、幾多項疑問喺幾多頁（`finish_book.py` 嗰行有齊）、清走咗幾多 MB；有圖（`--extract-figures`）就講 `X-圖/` 喺邊、幾多幅，圖入面嘅字冇轉寫，連圖攞走咗嘅字（例如影印原件嘅轉寫）`X-doubts.md` 逐行列住；講明冇逐頁視覺核對（唔可以話有），定唔到嘅位喺 `X-doubts.md`，要對住原書核。有 `WARNING` 就照講（例如改過嘅 `final.md` 搬咗去 `final.edited-<時間>.md`）；PDF 真係缺頁就講明，唔好自己補。唔好將 `final.md`、`doubts.md` 或者 log 成段讀入對話。交付檔唔好 `git add` 或者 commit。
- 校對期間嘅進度稿 `BOOK/proofreading.md`：用戶問先講，或者最多每個鐘講一次。
- 交付之後有網：`X-doubts.md`〈要查資料嘅專名〉嘅專名淨係用文字搜尋（唔好放大頁圖睇），查到就一兩句話畀用戶知，唔改 `X.md`。
- 用戶叫你改一個字：喺 `X.md` 全書搜同形同一字之差，逐處列畀用戶核，唔做全域替換；改完淨係跑 `"$JYUT_OCR_PYTHON" scripts/audit_ocr_markdown.py X.md --mode final --summary-only`，唔好為咗核而再跑 `finish_book.py`。

## 十、要細節先睇 reference（淨係讀嗰節）

用 `grep -n '^##' FILE` 搵行號，由嗰節標題讀到下一個同級或者更高級嘅標題。

- `references/long-book-workflow.md`：〈三次交付同本書目錄〉、〈OCR 階段（ocr_book.py）〉、〈Book profile 同異體〉、〈逐頁校對（proofread_pages.py）〉、〈封條同續跑〉、〈一個 command 做完（finish_book.py）〉、〈清走工作檔（clean_book.py）〉、〈幾個 PDF 一齊做（ocr_batch.py）〉、〈圖：淨係留圖片（--extract-figures）〉、〈疑點（doubts.md）〉、〈用戶決定嘅規則（decisionChanges）〉、〈開關〉、〈修書計數（_book_lint.py）〉、〈agent 規矩嘅實測根據〉。用戶問某類疑點、某個開關點解，或者 exit 碼唔喺上面，先讀。
- `references/local-ocr.md`：〈GPU 交接（本機模型佔住 GPU）〉、〈GPU 交接期間嘅其他 client〉、〈模型同裝置〉、〈輸出、續跑同核圖〉、〈等背景步驟跑完（`wait_for_run.py`）〉：交接、底稿 blocked 或者截斷嗰陣讀。
- `references/install.md`：安裝、平台、本機校對模型。
- `references/subagent-orchestration.md`：A 套 bundle 每種題點答、subagent 分工。
- `references/packet-worker.md`：答題者（subagent）淨係讀呢一頁；你自己答包都係照佢。
- `references/content-fidelity.md`：開 `--content-fidelity on` 之前讀。
- `references/jyut-ocr-detailed-rules.md`、`references/common-ocr-traps.md`：版面、表格同常見錯字；A 套答題、用戶覆核用。
- `references/structured-workflow.md`：冇 OCR 引擎、或者用戶要人手分段轉錄、structured 定稿先用（B 套唔用）。
- `references/human-review-feedback.md`、`references/manual-review-tool.md`：用戶要人工覆核、標註工具先讀。
- `references/developer-notes.md`：改 skill 嘅人先讀。
