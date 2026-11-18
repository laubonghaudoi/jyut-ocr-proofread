# 更新摘要

## Unreleased — 幾個 PDF 一齊做：`ocr_batch.py`（issue #5）

- 新 script `scripts/ocr_batch.py`，三條 command 做晒一批書：`ocr_batch.py A.pdf B.pdf ...` 開批（每個 PDF 一個普通嘅 `BOOK`，render 喺背景做，即刻返回）；`--ocr BATCH` 一個 call 逐本讀晒所有書；`--round BATCH` 逐輪行每本書嘅 profile、校對、定稿同清走，直到全部交付。
- 點解：逐本做嘅話，本機模型每本書（前景分輪就每一輪）都要停、開一次，agent 亦要等完一本書嘅 OCR 先可以答另一本嘅題。實測一本 639 頁嘅書 OCR 66 分鐘、校對連定稿 39 分鐘，後者唔使 GPU。而家 OCR 成批一個窗口；一本書讀完即刻校對，同下一本嘅 OCR 重疊；所有書嘅題目夾埋一輪派。
- `--round` 一個 call 最多等 540 秒（shell call 嘅 600 秒上限之內），淨係印幾行；佢開嘅步驟喺背景行、唔跟 call 結束。exit 10 有題等答（`BATCH/plans/round-NN/worker-KK.txt`，每個答題者一個檔）、75 未有題、4 OCR 嗰個 call 完咗但仲有書未讀、0 全部交付、1 有書失敗。同一時間最多 `--max-workers` 個答題者、每個大約 `--items-per-worker` 條題（預設 20 個、10 條），未派嘅等有位再派：唔會因為書多而令一個答題者一次過攞幾百題。實測每個答題者大約 38 條題嗰陣每條題 8.5 萬 input token，10 條或以下係 6.4 萬。
- 一本書失敗（render、OCR 或者任何一步）淨係嗰本標 `failed` 連原因，其他書照做；`--retry-failed` 再試。背景步驟俾人殺咗，下一輪自己再開。答題者唔使等齊先派下一批：每一份派出去都記低，答案檔寫齊就當做完，有位就補派（主 agent 開晒答題者就即刻再跑 `--round`，冇位佢自己等）；同一包唔會派兩次。8 本書實測，等齊 20 個先派嗰陣每分鐘得 27 條題，慢過 OCR 出題。答題者寫齊答案之後仲佔位 30 秒（`--settle`），等佢返回咗先補派，唔會撞平台嘅同時上限；答題者全部冇咗（失敗、斷線、換 session）就加一次 `--answerers-gone`。冇人答嘅 bundle 派 4 次就停。
- `run_ocr_phase.py` 識得 `ocr_batch.py --ocr BATCH`：按批嘅路線估顯存同啟動時間，所以成批可以包喺一個交接窗口（`--background` 或者前景分輪）。
- 冇改任何單本書嘅 script：每本書行嘅就係 `SKILL.md` 四嘅 command，`BOOK` 入面冇批嘅檔。`BATCH/timeline.jsonl` 記每步嘅開始、結束同秒數，`BATCH/batch-status.json` 記每本書去到邊。
- `SKILL.md` 四加〈幾個 PDF 一齊做〉、八加一批書點續；`references/long-book-workflow.md` 加同名一節。
- 測試 `tests/test_ocr_batch.py`（33 個，離線）：真嘅 `ocr_batch.py`、`packet_dispatch.py`、PacketClient 同 `apply_packet_answers.py`，每本書嘅步驟用守住同一個契約嘅替身。涵蓋：兩本書由 PDF 到交付而且同一輪答題者服務兩本書；第一本校對嗰陣第二本仲讀緊；OCR、render、校對失敗各自唔影響另一本；時間預算用完喺兩本書之間停；一輪唔超過平台嘅答題者數；冇寫答案嘅答題者嗰幾包下一輪再派；http 後端等 OCR 完、一次一本、唔問人；單引擎路線；交接識得批嘅 command。
- 實機驗證（2026-11-18，真引擎，細嘅睇圖模型做答題者）：由真書切出嚟嘅兩個 PDF（8 頁同 6 頁）。開批之後 render 各 2 秒；一個交接 call 逐本讀晒（68 秒同 70 秒；本機模型當時冇開，所以冇嘢要停）；第一本一讀完就出 profile 題，第二本仲讀緊；7 輪共 124 條題，一輪最多 5 個答題者；兩本都交付去 PDF 隔籬，`BOOK` 自動清走。答題者每條題平均 6.4 萬 input token。

## Unreleased — GPU 交接：唔係本機模型嘅 agent 可以一個窗口做晒成個 OCR（issue #3）

- `run_ocr_phase.py --background`：畀「唔係用嗰部本機模型諗嘢」嘅 agent（雲端 agent）用。呢個 call 冇時間預算，agent 將佢放背景、用 `wait_for_run.py` 等；成本書一個窗口，本機模型淨係停一次、開一次。以前一律要前景分輪（`OCR_PHASE_MAX_SECONDS`，通常 540 秒），每一輪結束都開返模型、下一輪再停：script 自己記低嘅實測係一本 609 頁嘅書，兩個引擎合共 7.5 分鐘用喺分段度；一本 639 頁嘅書今次分咗 9 輪。
- 點解前景分輪仍然係預設：agent 本身用緊嗰部本機模型嘅話，指令一返回佢就要個模型諗下一步，所以每輪都要開返；放背景會令 agent 自己斷線（2026-09-23 實測）。所以 `--background` 喺呢種 agent 會被拒絕（exit 2，乜都冇停），三個訊號任何一個中都算：`JYUT_OCR_AGENT_IS_LOCAL_MODEL=1`；Qwen Code 行緊校對用嗰個模型；行呢個 call 嘅 process（shell、agent）有連線去模型 server。
- 安全網冇變：看門程序照樣喺 release 之前啟動，個 call 俾人殺咗就開返模型。`SKILL.md` 四.2、五同 `references/local-ocr.md`〈GPU 交接〉加咗呢條路同邊個唔准用；背景嗰個 call 俾 harness 殺咗就改返前景分輪。
- 測試（假嘅停、開模型指令同假引擎）：背景 call 喺預算遠細過所需嗰陣一次做晒、模型只停開一次；本機模型 agent 被拒絕而且乜都冇停；祖先 process 有連線就當係本機模型 agent；一本細書經背景交接由頭行到尾，`wait_for_run.py` 由 RUNNING 等到 DONE。
- 實機驗證（2026-11-18，本機模型開住，雲端 agent 行）：一本 50 頁嘅書，機器設定嘅預算係 540 秒，背景交接 511 秒一個窗口做晒；模型服務自己嘅紀錄顯示佢只停咗一次（00:09:48）、開返一次（00:18:14），期間 stand-in 喺佢個 port 答 503；兩個引擎 50 頁都封好，完咗健康檢查通過。同一個工作量用前景分輪要兩輪。

## Unreleased — HunyuanOCR：server 唔再佔住系統內存；首次安裝按張卡定 slot 數（issue #3）

- `run_hunyuanocr.py` 啟動 llama-server 嗰陣加 `--cache-ram 0 --ctx-checkpoints 0`。Server 預設會留 8 GiB prompt 緩存同每個 slot 32 個 context checkpoint，OCR 每頁都係獨立請求，完全用唔著，但照樣佔系統內存：一個 32 slot 嘅 server 去到 13.6 GB，令部機內存用盡。實測（同一本書，顯存同時間不變）：4 個 slot 讀 40 頁，server 常駐內存峰值由 10.7 GB 跌到 2.6 GB，40 頁底稿文字一樣；16 個 slot 讀 96 頁係 4.4 GB。兩個選項各自要 server 嘅 `--help` 有列先會加，舊版 llama-server 照樣起到。
- 安裝器：`env.sh` 未有 `OCR_HUNYUAN_SLOTS` 嗰陣，唔再一律寫 1，改為按張卡嘅總顯存定：每個 slot 約 1.5 GiB、另留 2 GiB、最多 16 個（8 GiB 卡 1 個、12 GiB 4 個、16 GiB 7 個、24 GiB 12 個、32 GiB 或以上 16 個）；CPU、Metal 或者讀唔到顯存仍然係 1 個。用呢個數試跑過唔到，就改返 1 個再試一次。已經寫咗數嘅機器唔郁，試跑失敗亦唔會自動改佢。
- 點解最多 16 個：32 GiB 卡實測 16 個 slot 幾乎全程都滿、GPU 使用率中位數 100%，一本 639 頁嘅書 17 分鐘；32 個 slot 冇快到。

## Unreleased — 橫排書嘅書眉按位置學，唔使逐頁問

- 問題：book profile 以前淨係識「同一行字喺一半嘅頁出現」嘅書眉，啱舊書每葉版心都印同一個書名。橫排嘅現代書一邊頁印書名、另一邊印章名，冇一行過半，所以乜都學唔到，逐頁校對就每頁問一題書眉。實測一本 639 頁嘅橫排書：第一輪 830 條裁決題，606 條（606 頁每頁一條）係淨係引擎 A 讀到嘅頁頂書眉；書名喺 638 頁入面嘅 304 頁，差少少先夠一半。
- 而家 `learn_heads()`（`book_profile.py` 寫入 `layout.learnedHeads`，冇 profile 記錄嗰陣 `proofread_pages.py` 自己學）多一種量法 `placed_heads()`：頁頂嗰行（成頁最高、上面隔籬冇其他字、版面標 `header` 嘅短行）喺單數或者雙數頁有一半以上企喺同一個高度，嗰度就係書眉帶；喺帶入面出現三頁或以上嘅字串就係書眉。記喺 `learnedHeads.placed`（`stems`、`bands`）。
- 按位置學到嘅書眉淨係喺帶入面先丟：block 要一半高度喺帶入面、字號同書眉一樣；淨係引擎 A 讀到嘅書眉亦要喺帶入面嘅 block 先唔問就丟（`learned-head-reading`），兩個引擎都讀到嘅成個 block 丟（`learned-head-block`）。章首頁印喺下面嘅章題（就算字號一樣、版面標咗 `header`）、書名頁嘅書名、正文重複嘅表名都照留；淨係引擎 B 讀到嘅冇位置可對，照舊問。
- `layout.runningHeads` 冇改：嗰度係唔理位置淨睇字嘅，章名同時係章首頁嘅標題，唔可以放入去。按重複量到嘅書眉同以前一樣；按位置乜都學唔到嘅書（包括冇書眉嘅書），`learnedHeads` 冇 `placed` 呢個 key，成份記錄逐個 byte 冇變。
- 喺另外 14 本書嘅引擎 A 底稿上重算（只讀、冇問模型）：13 本嘅記錄同以前逐個 byte 一樣；一本 63 頁、頁頂橫印刊名同篇名嘅舊期刊，以前按重複一個書眉都量唔到，而家學到 4 個（都係真書眉），未用真模型重跑驗證。
- 離線重放嗰本書（抄一份底稿、用測試嘅假模型跑真嘅 merge，冇問模型）：學到 22 個書眉，603 頁有書眉帶；606 條入面 600 條唔使問，第一輪裁決題由 830 條跌到 230 條。有改動嘅 605 頁，每頁少咗嘅啱啱好係頁頂書眉嗰行；17 個章首頁（連結語）嘅標題全部留低。仲要問嘅 6 條：4 條係淨係出現一兩頁嘅書眉，2 條係讀法連埋正文第一個字（照舊問，書眉之後成個 block 丟）。
- 已經有 profile 嘅書要重跑 `book_profile.py` 先會學到；profile 同 `learnedHeads` 嘅 hash 入咗 packet 題嘅 request id 同封條，所以重量之後嗰本書之前答過嘅校對題同封咗嘅頁都要重做。
- 新測試：`tests/check_learned_heads.py` 加一本虛構嘅 26 頁橫排書（書名／章名隔頁交替、章首標題、重複表名、引擎 B 獨讀、讀錯一個字）同兩本學唔到嘢嘅書。

## Unreleased — 成套測試由六分幾鐘減到一分鐘內

- `tests/run_all.py` 幾個檔一齊跑：`--jobs N`，預設係部機嘅核心數、最多 8 個；`--jobs 1` 同以前一樣逐個跑。實測（16 核心）成套由 380 秒減到 52 秒，`--jobs 16` 係 42 秒，連跑三次結果一樣；測試數目冇變（755 個 unit test、54 個 check）。
- 時間本來集中喺幾個檔，唔係因為檔多：96 個檔入面 72 個加埋得 42 秒，`test_handover.py` 一個就佔 130 秒（佢好多測試要等真實嘅秒數）。呢個檔而家逐個 test class 分開跑（`SPLIT_BY_CLASS`），class 清單由 unittest 自己嘅 loader 攞，唔會漏咗淨係繼承測試嘅 class。
- 兩個一向唔過嘅測試修好：`test_setup_dual_engine.py` 有一個測試會去睇部機 home 目錄有冇 Hunyuan 權重，裝咗權重嘅機先會唔過；`check_wait_for_run.py` 用 `--effort` 做「錯嘅選項」，但有咗 `--effort-policy` 之後 argparse 當佢係縮寫。
- 部機 `env.sh` 嘅設定唔再漏入測試：`env.sh.example` 列嘅名同 `PROOFREAD_*` 一律唔帶入去。之前喺 source 咗 `env.sh` 嘅 shell 跑，會因為引擎名同裝置設定有五個檔唔過。
- 刪走 `tests/check_review_launcher.py`：佢 13 個測試全部喺 `tests/test_review_launcher.py` 入面有（後者仲多兩個）。

## Unreleased — PaddleOCR-VL 幾個 worker：同一張卡同時讀幾頁（issue #3）

- `run_paddleocr_vl.py --workers N|auto`（`OCR_PADDLE_WORKERS`）：幾個 PaddleOCR-VL worker 由同一條隊攞頁，一頁嘅底稿同邊個 worker 讀佢無關。預設 `auto`：空閒顯存放得落兩份 14 GiB（另留 2 GiB）就開兩個，否則一個；CPU、MLX 後端、或者每個 worker 分唔夠 8 頁都係一個。`OCR_PADDLE_WORKERS=1` 即係以前嘅行為。
- 幾個 worker 嗰陣每個有顯存上限（`FLAGS_gpu_memory_limit_mb`），因為 Paddle 嘅配置器有幾多攞幾多：一個 worker 讀一本書，實測由 12 GB 升到 27 GB。一個 worker 照舊冇上限。
- 一個 worker 死咗，佢嗰頁交畀另一個重讀；全部都死晒，就由一個冇上限嘅 worker 讀餘下嘅頁，所以開多個 worker 唔會令單 worker 讀到嘅頁失敗。交接嘅顯存估算跟住明確嘅 worker 數計；`auto` 只要求一個 worker 嘅顯存，唔會為咗開第二個而停本機模型。
- 實測（一張 32 GB 卡）：現代橫排書 120 頁，一個 worker 503 秒、兩個 321 秒（1.57 倍）；舊書 50 頁，334 秒對 202 秒（1.65 倍）。兩批頁嘅文字同座標框，兩個 worker 同一個 worker 逐頁一樣。GPU 使用率由 53% 升到 93%，顯存峰值 28 至 30 GB，所以 `auto` 最多開兩個。一本 639 頁嘅現代書，OCR 實測 66 分鐘，當中 Paddle 佔 47 分鐘。
- HunyuanOCR 冇改：同一本書實測 16 個 slot 幾乎全程都滿，GPU 使用率中位數 100%，86% 時間係生成文字。32 個 slot 冇快到，而且 `llama-server` 多食好多系統內存（實測 13.6 GB，部機因此內存用盡）。所以唔開多份 Hunyuan，亦唔同 Paddle 同時跑：佢哋爭嘅係同一張已經滿載嘅卡。
- 額外 worker 嘅 process 由 runner 嘅主線程啟動：worker 跟父 process 一齊收工嗰個訊號係跟啟動佢嘅線程，由短命線程啟動嘅 worker 會跟住嗰條線程一齊死。新測試用假 Paddle 蓋住分頁、底稿一致、worker 死亡、時間預算同 worker 數計算。

## Unreleased — 通用標點融合：packet／HTTP 共用拆分及融合開關

- `proofread_pages.py` 加 `--punctuation-mode auto|split|fused`；`auto` 保留 packet 融合、HTTP 拆分嘅原本預設，兩條路線都可以明確選擇。共用融合 prompt、解析及格式檢查，唔綁定模型或者 agent 平台；舊 packet prompt、request ID 同答案格式保持可重播。
- 缺格式、符號數讀唔到或超出上限就回退獨立 census／punctuate；字元、標點數量、雙圈、導引點、重對齊及重試守門不變。HTTP 題型 `census+punctuate` 支援既有 effort policy；重試仍然係獨立題型。
- 每段、每頁及 run report 記錄融合嘗試、首次成功、回退及原因；封頁以實際模式判相容，模式轉咗唔可以混用封頁。舊封頁按記錄嘅 transport 推斷；未記 transport 嘅舊封頁只保留 `auto` 相容。
- 新合成離線測試覆蓋兩種 backend、格式回退、無標點、改字、計數、雙圈、導引點、重對齊、effort、歷史答案重播及封頁模式。呢啲測試驗證程式契約；實際速度同品質要由固定案例嘅對照實驗另外報告，唔因此更改通用預設。

## 2026-11-17：精簡 macOS 路線

- macOS 只支援原生 Apple Silicon，移除 Intel Mac 同 Vision＋Hunyuan 混合路線。
- 安裝同跑書統一 `--engine apple-vision`／`--engine hunyuan-paddle`；Linux／WSL2 保留 Hunyuan-only。移除跑書嘅 `--engines` 同公開路線名 `auto`、`vision`、逗號組合；舊設定須明確重選，新路線用新工作目錄。
- 純 Vision 唔下載 HF 模型，唔啟動其他 OCR 模型或做 GPU 交接；保留座標修正、單引擎校對、續跑及封條。雙引擎保留 MLX 加速。
- 以下舊版本紀錄及已封存評測保持原樣，唔代表退役路線仍然可用。

## Unreleased — 新書預設用兩個慳題開關；答題者數目寫入步驟；刪走舊禁令（issue #6）

- `SKILL.md` 一、四：A 套平台開到 subagent，就開工嗰陣定 `N`，第 3、4 步加 `--max-bundles N`；未有封好頁嘅書，第 4 步再加 `--same-print-marks --unmarked-page-sample`。已經有封好頁嘅書照返之前嗰條 command，唔中途加減（開咗同關咗嘅封頁互相唔算 current）。B 套未量度過，唔加。
- 根據（28 案 50 頁，現成 OCR 底稿，只做校對）：一個強模型做主 agent，照派工單每輪開最多 24 個同模型嘅答題者，兩個開關全開：520 題、11 輪、由頭到尾 33.6 分鐘（腳本自己 168 秒），CER 寬容 14.13%、表格結構 90.2、表格內容 85.6。同配置嘅細模型係 14.88%、89.7、82.9；冇開關嘅細模型基線 14.79%。即係開關冇令強模型或者細模型跌準確度。
- 同一次實測帶出、已經寫入六：主 agent 幾次未等齊答題者就行下一輪，多咗兩輪空轉，所以講明等齊先行、唔好中途停答題者；平台唔畀 agent 睇到 API model ID，`--answered-by` 就寫平台顯示嗰個名。尾三輪得 9 條重試同排版題，用咗 8.3 分鐘（全程四分一）：尾巴仍然係最大嘅剩餘時間。
- 刪走「Tesseract／Tessdata 硬性禁用」（`SKILL.md` 二、`references/local-ocr.md`）：流程已經寫死，agent 冇機會揀 OCR 工具。留返「引擎冇裝好就照 ERROR 講畀用戶知，唔好換引擎或者其他 OCR 工具」。

## Unreleased — macOS MLX 同 Vision 雙引擎優化（issue #4）

- Apple Silicon macOS 14+ 嘅 Paddle 預設用獨立、hash-locked `mlx-vlm==0.8.0` 做 Metal 識別，CPU 保留版面分析；沿用原裝未量化權重。明確 native／CPU 設定保留，舊 auto-CPU 可遷移。正式 runner 管服務啟動、模型 readiness、GPU 隊列、限時同父程序死亡清理，失敗唔會靜默 fallback。
- 新增 `--engine vision,hunyuan`／`ocr_book.py --engines vision,hunyuan`：Hunyuan B → 草稿 → Vision A → 補讀。Vision 統一左上角座標、確定次序同 TXT／JSON 契約，保留原始 observations／line IDs，家具候選唔會自動授權刪字；Hunyuan 表格候選經聯合裁圖或全頁圖處理，包含數字表。
- 下游封條記語義指紋，換引擎／後端或 block 文字／座標／label／次序會 stale；未變嘅 legacy native 封頁照收。macOS 可回收記憶體估計統一用 free + inactive + speculative，唔重複加 purgeable；順序引擎取峰值需求，保留手動預算同 Hunyuan slots。
- CER 唔作啟用門檻，通用 Paddle 記憶體預算保持。評測成品統一存喺 benchmark repo，效能量度同驗收紀錄見 [issue #4 結案回覆](https://github.com/laubonghaudoi/jyut-ocr-proofread/issues/4#issuecomment-6952908371)。

## Unreleased — 派工單：答 packet 題可以一次過開多個答題者（issue #6）

- **新 script `packet_dispatch.py PACKETS_DIR [...]`**：一步停低等答案（exit `10`／`11`）就跑佢。佢讀最新嗰個 wave，將未答齊嘅 bundle 分成幾份 assignment，每份有一段現成嘅 `prompt`，主 agent 每份開一個 subagent、原文照貼就得。`--max-workers`（預設 24）係平台同時開到幾多個，`--items-per-worker`（預設 10）係每個答題者大約幾多題。答題者返晒之後再跑一次：仲有未齊嘅包就再列出嚟；齊晒就加 `--apply --answered-by MODEL` 交晒答案，然後重跑同一條步驟 command。
- **幾本書一齊派**：一次過列幾個 packets 目錄，佢哋嘅 bundle 放埋一齊分，一個答題者可以服務幾本書。
- **`references/packet-worker.md`**：答題者淨係讀呢一頁，唔載入技能、唔跑 script、唔計 hash。
- **`SKILL.md` 六改寫成一個循環**：攞派工單 → 一次過開晒 subagent → 再跑派工單交答案 → 重跑步驟。冇 subagent 嘅平台自己照同一頁逐包答。答 packet bundle 嘅並行上限由「預設最多 8 個、由 2 個開始」改成「有幾多份 assignment 就開幾多個，唔超過平台嘅 slot」；分段 writer 同 auditor 嘅上限照舊。
- `proofread_pages.py` 同 `book_profile.py` 停低等答案嗰陣，`[packets]` 多一行寫成條派工 command。
- 點解：之前每包開一個會載入成個技能嘅 worker，一個大模型每題要約 245 秒，長 session 係約 35 秒；而一個細模型嘅答題者淨係攞自己嗰幾包同一頁指示，127 條題每題 6.4 秒、6.2 萬輸入 token，一案一個長 session 係 7.6 秒、9 萬。
- 成本書實測（一本 26 頁嘅直排舊書，現成 OCR 底稿，`--max-bundles 20`，主 agent 照 `SKILL.md` 六派工，答題者係一個細模型）：7 個 wave 共 564 題，由第一步到全部頁封好 22 分鐘；69 個答題者嘅工作時間加埋 103 分鐘。頭三個 wave（511 題）用咗 9 分鐘，尾四個 wave 得 53 條重試同排版題，但每個 wave 都要等齊先行下一個，所以仲用咗 9 分鐘。
- 實測帶出嘅兩點已寫入：一個 wave 分幾多包係行步驟嗰陣定（`--max-bundles`，預設 8），想開多過 8 個答題者要嗰陣加；答題者見到「已定」嘅文字（排版題、標點題）係照抄再加結構，唔係逐字重讀成頁，`references/packet-worker.md` 講明。未講明之前，一個答題者成批排版題唔肯答。
- 新測試 `tests/test_packet_dispatch.py`。

## Unreleased — `--unmarked-page-sample`：無標點嘅頁抽樣問；裁決答「睇唔到」就用成段大圖再問（issue #6）

- **`--unmarked-page-sample`（預設關）**：成頁兩個引擎各自讀到嘅標點都唔多過兩個，而且引擎 A 讀成淨係得字嘅 block 多過三個，就先問最長三個 block 嘅標點題；三個都答冇標點，其餘 block 照原文寫、唔問（`censusNotAsked` 記 `page-unmarked-sample`），否則其餘 block 喺下一個 wave 照常問。Profile 見到雙圈或者旁點嘅書唔用。封條記 `unmarkedPageSample: "on"`，入 provenance。
- 點解以頁為單位：有標點嘅頁上面，兩個引擎都冇讀到標點嘅 block 唔代表真係冇標點。Benchmark 28 案入面，唯一一本無標點古書以外有 62 個噉嘅 block，校對喺其中 15 個補咗標點。
- 離線重播（benchmark 28 案，用三輪已封存嘅答案）：14 頁有抽樣，9 至 10 頁成頁放行，少問 174 至 177 條標點題，約佔全部題目四分一，幾乎全部嚟自嗰本無標點古書；4 至 5 頁嘅抽樣見到標點，其餘 block 遲一個 wave 先問。放行嘅 block 入面，一輪答案寫過 13 個標點，另外兩輪係 0 個；嗰本書 8,760 字嘅人工答案總共得 6 個標點。
- **裁決答「睇唔到」就再問一次**（`widened_answer`）：估位裁出嚟嘅圖（glyph、band）答「睇唔到」，就用嗰個位所在成個 block 嘅圖再問一次，題目講明。第二次嘅答案作準；仲係睇唔到就保留引擎讀法，紀錄 `unseen`。紀錄多一個 `widened`（第一次嘅答案、大圖檔名 `page-NNNN-adjudication-wider-NN.png`）。成個 block、表格、整頁同跨 block 邊界嘅裁圖唔會再問。
- 實測（一個細模型，九個 benchmark 案）：66 條裁決答案有 9 條答「睇唔到」，其中 6 條係估位裁圖，第二個 wave 出咗大圖重問；5 條有答，1 條仍然睇唔到。可以對人工答案嘅兩條都答錯，同引擎 A 嘅讀法一樣，即係細模型喺大圖上冇改善；大圖本身睇得清、搵到個位。
- **成段、成個表格、成頁嘅裁圖會講明**（`WHOLE_CROP_NOTES`）：呢類裁圖冇裁走任何部份，爭議位一定喺入面，題目而家講明，叫答題者照上文下文搵返個位。原因係加咗「睇唔到」之後，細模型喺成個表格嘅圖上好易咁答：一本書嘅表格 29 條裁決題有 14 條答「睇唔到」，加咗呢句之後得返 2 條；可以對人工答案嘅位，答啱數由 1 條返到 4 條（未有「睇唔到」之前係 5 條）。
- 全集實測（benchmark 28 案 50 頁，一個細模型答題，`--same-print-marks` 同 `--unmarked-page-sample` 都開，未有上面嗰句提示）：對比舊 code 冇開關嘅一輪，題目 730 → 501，其中標點題 466 → 234；校對時間同輸出 token 各少約兩成。七項指標喺逐次波動之內（CER 14.79% → 14.88%，不計標點 14.82% → 15.06%，表格結構 83.0 → 89.7）。無標點古書嗰案由 261 條題跌到 54 條。逐個裁決位對人工答案（101 個可以對）：寫啱嘅由 65 個變 72 個。
- 新檢查喺 `tests/check_agreed_marks.py`（抽樣）同 `tests/check_adjudication.py`（再問一次）。

## Unreleased — 裁決題：裁圖跨欄、影唔到就答「睇唔到」、每題都要答（issue #6 實測帶出）

- **裁圖跨欄**（`crop_glyph` 嘅 `across`，逐頁校對嘅裁決題用）：直排 block 入面，估計喺欄尾四個字以內嘅爭議位，裁圖而家連埋下一欄嘅頭；喺欄頭四個字以內就連埋上一欄嘅尾。幾段由右至左並排，中間隔一條紙色空白，題目會講明裁圖係拼出嚟（`COLUMN_BREAK_NOTE`）。原因：每欄字數係由墨跡長度估出嚟，喺欄尾差一兩個字就落咗隔籬欄，而裁圖以前唔跨欄，爭議字唔喺圖上，答題者只能估。實例：一個 548 字、15 欄嘅 block，爭議字印喺下一欄第二個字，裁圖喺上一欄尾就完咗。
- 影響範圍（benchmark 28 案第一波，重出題目對比）：168 條裁決題有 16 條變成拼圖，其餘 152 張裁圖逐 byte 一樣，其他題型嘅圖全部一樣。Book profile 嘅異體字裁圖同 coverage 盲讀冇傳 `across`，照舊。橫排（轉向量度）嘅裁圖唔拼。
- **裁圖影唔到爭議位**：裁決 system prompt 叫答題者寫 `定案：睇唔到`，唔好靠上下文估。呢類答案（連模型自己寫嘅「無法判定」「無法由圖判定」等，`UNSEEN_VERDICTS`）唔當裁決：保留 fallback 引擎嘅讀法，紀錄係 `adjudicator-invalid` 加 `unseen: true`。以前呢啲字長過兩邊讀法兩個字以上先會作廢，所以兩個字或以上嘅爭議，「無法判定」會當成讀法寫入正文。
- **裁決答案最尾兩行**：system prompt 講明先 `定案：<字>`，最後一行 `RESEARCH: yes/no`；題目唔再話定案嗰行係「最後」。以前兩邊各自話自己嗰行係最後一行。
- **每題都要答**：bundle 嘅 `answer_format_instructions`、`SKILL.md` 六、`references/subagent-orchestration.md` 同 Codex packet worker 講明，圖睇唔清或者定唔到，唔係留空一題或者成包唔交嘅理由：照嗰題自己講嘅做法答，冇講就寫最有根據嘅讀法。實測一個細模型答題，40 個案次有 5 個因為「唔肯估」成包唔答。
- 已封嘅頁唔受影響。裁決題嘅 prompt 改咗，所以未封嘅頁入面，已答而未用到嘅裁決答案對唔返新題目 ID，會再問一次。
- 新檢查喺 `tests/check_crops.py`（跨欄）同 `tests/check_adjudication.py`（睇唔到、最尾兩行）。

## Unreleased — `--same-print-marks`：兩個引擎印刷字一致嘅 block 唔問標點（issue #6）

- `proofread_pages.py` 加 `--same-print-marks`（預設關）：規則 agreedMarks 因為空位有其他印刷字（數字、拉丁字、註號）而唔收嘅 block，如果兩個引擎喺每個空位讀到嘅印刷字一樣，就照用引擎 A 嘅標點，唔出 census／punctuate 題。比較時唔計註號：兩個引擎寫法同讀錯都唔同，而註號另外還原。
- 記錄：封條記 `samePrintMarks: "on"`，入 provenance，開咗同關咗嘅頁互相唔算 current；放行嘅 block 喺 `censusNotAsked` 記 `engines-agree-print`。
- 實測（`--backend packet`，Claude Haiku 5.5 答題，benchmark 28 案 50 頁）：一本現代橫排書嘅 8 案（基線同開關各跑兩次）第一次問嘅標點題 59 → 36，校對時間同輸出 token 各少約 24%，純正文頁少約 45%。直排書同無標點古書基本上冇 block 放行，全集問題 730 → 709。七項指標都喺逐次波動之內（CER 14.79% → 15.00%，不計標點 14.82% → 14.85%）。
- 新檢查喺 `tests/check_agreed_marks.py`。

## Unreleased — `--effort-policy`：按題型指定第一次問用邊一級 effort（量度用）

- `proofread_pages.py` 同 `book_profile.py` 加 `--effort-policy KIND=RUNG,...`（`PROOFREAD_EFFORT_POLICY`），淨係 `--backend http` 用，預設冇：唔設就同以前一模一樣，每條題都由 `--reasoning-effort` 嗰級問起。設咗之後，列出嘅題型（例如 `census=low`）第一次就用嗰級；`--reasoning-effort` 仍然係上限，冇列嘅題型同所有重試照用上限，撞 `max_tokens` 照舊向下一級。寫錯題型或者級別即刻 exit 2。
- 記錄：每頁封條多 `effortPolicy`（只喺有設嗰陣），run report 亦有；run report 每類題多一個 `efforts`，數每一級問咗幾多次。同 `reasoningEffort` 一樣只係記錄，唔入 provenance、唔入 packet 題目 ID，所以唔會令已封嘅頁過期。
- 修正 run report 漏咗合計每頁 `calls.*.efforts`：逐題型、逐級相加，仍然只計今次完成嘅頁；續跑略過嘅頁唔重計，舊記錄冇 effort 資料就唔推算。
- 呢個係 issue #2 第 2 步嘅機制，**未有建議值**：邊類題可以調低而唔掉字、掉標點，要喺有本機模型嘅機器逐類對 golden 量度先知。
- 新檢查 `tests/check_effort_policy.py`（本機 stub server，包括由頭跑一次 `proofread_pages.py`）。

## Unreleased — macOS 首次安裝先問用戶揀引擎

- Mac 有內置嘅 Apple Vision，所以 macOS 首次安裝（冇傳 `--engine`，`env.sh` 又未有上次揀嘅結果）喺下載任何嘢之前先問：`--engine auto` 由 Hugging Face 下載 HunyuanOCR 同 PaddleOCR-VL（Apple Silicon 約 5.7 GB），定係 `--engine vision` 淨用 Apple Vision（唔下載模型，約 0.2 GB）。喺 terminal 跑會列出兩個選擇等你答 `1` 或者 `2`；冇 terminal（agent 嘅 shell、pipe）就停低，exit `2`，`ACTION REQUIRED` 行講明兩個 `--engine` 同各自要幾多磁碟，agent 問咗用戶先加 `--engine` 重跑，唔代用戶揀。`--check` 未揀之前報同一段。
- 揀過一次記入 `env.sh`，之後唔再問；Linux／WSL2 照舊唔問、兩個都裝；`--engine hunyuan` 照舊係明確覆蓋。
- `SKILL.md` 三講明 agent 見到嗰行要問用戶；四〈單引擎〉補返淨用 Apple Vision 嗰陣第 2 步跑 `run_apple_vision_ocr.py`。`references/local-ocr.md` 嗰條 Vision command 原本漏咗 runner 一定要嘅頁範圍，照抄會 exit 2：兩處都寫返 `--first-page 1 --last-page N`。
- 實機驗證（Apple M5，macOS，2026-11-16，由清空狀態開始）：`--engine vision` 32 秒裝好、佔 27 MB；`--engine auto` 下載同核驗約 9 分鐘、試跑 32 秒、佔 5.8 GB（包括約 1 GB uv cache）；兩條路線 `--check` 都報 `ready`，唔傳 `--engine` 重跑唔再問。

## Unreleased — GPU 交接：模型本身停咗就唔郁；顯存唔夠就講邊個佔住

- 停咗模型之後顯存仍然唔夠（exit 1，RESULT 行 `GPU FULL`）：照舊即刻開返模型，另外逐個列 GPU 上面嘅 compute 程序（pid、使用者、程序名、MiB、systemd unit）同釋放佢嘅命令（`systemctl --user stop UNIT`、`kill PID`）。技能乜都唔殺、唔停，由用戶決定。
- 修正：call 開始嗰陣模型本身冇開（例如用戶 `systemctl --user stop` 咗），以前交接照樣行一次停模型，最尾仲會開返佢（`OCR_PHASE_SWAP=always`、GPU 俾其他程序佔住、或者讀唔到顯存都會中；安裝程式嘅 GPU 測試行同一條路）。而家先睇模型開咗未（health 網址答到、個 port 有嘢 listen 緊，或者 GPU 上面有佢嘅程序）：冇開就唔停、唔開、冇暫代服務、冇 watchdog，GPU 夠位就直接跑，唔夠位就 exit 1 `GPU FULL`，列出佔住嘅程序。`status.json` 寫 `left-down`，下一次 call 唔會當係交接留低而開返佢。RESULT 行尾 `[gpu: …]`、`status.json` 同 `timings.json` 嘅 `lastWindow` 記低模型本來開咗未、有冇停過同開返。冇 `OCR_PHASE_HEALTH_URL` 就分唔到，照舊。時間預算、exit 75 同 `--round` 照舊。

## Unreleased — 唯一通用技能：併入 qwen 版、雙引擎安裝、GPU 交接

- 併入 jyut-ocr-qwen：搬入佢嘅回歸測試（54 個 check）同 `run_ocr_phase.py`、`wait_for_run.py` 等工具，`tests/run_all.py` 一個入口跑晒 unit test 同 check。qwen 版封好嘅頁同舊底稿照收，唔使重新校對。
- 安裝程式預設裝兩個引擎：HunyuanOCR 用 llama.cpp b10969 官方 CUDA 版，PaddleOCR-VL 用鎖定嘅 wheel 同權重；有 GPU 就用 GPU，冇先轉 CPU；Python 3.12 由 uv 提供。改 `env.sh` 嘅值會將舊行留做註解，唔改檔案權限。唔支援原生 Windows，用 WSL2。
- agent 本身係本地模型時做 GPU 交接：等用緊模型嘅 client 做完先停模型；停咗期間由暫代服務回 503 叫 client 等；任何退出都還原模型（watchdog、lease）；GPU 忙或者時間唔夠就用 exit 75／76 叫 agent 遲啲再跑。
- 校對預設由當前 agent 答，亦可以經 HTTP 交本地模型（`--backend http`）。完整性 v2 改做開關，預設關。封頁照記 script hash，但唔用嚟判封頁失效。
- 刪走五個 release 工具。
- 按 jyut-ocr-bench 嘅標準答案修 phase 3：截斷咗嘅 Hunyuan 讀數切走重複迴圈之後照合併；結構步自己寫嘅直排表按引擎 B 嘅閱讀次序轉向，每次轉都出疑點；被拒格網嗰張表入面兩個引擎都讀到嘅標點，唔再畀標點鎖拒絕；黏住括號嘅圈碼註號唔再整唔見；表格格仔入面用 LaTeX 包住嘅註號寫返淨註號。
- 驗證：全套測試（35 個 unit test 檔共 658 個測試，加 54 個 check）通過。jyut-ocr-bench 28 案（評分器 samples-4.0.0）：本地 qwen3.8-27b 實測上一版，CER 寬容異體字計標點 15.98%，qwen 版 18.25%；本版用同一批模型答案重放係 14.42%。

## Unreleased — 完整性 v2 與可靠 packet 續跑

- 統一全頁區域與保留 features 契約；家具留喺區域證據，表格支援完整標籤或原圖格網與候選格位映射。疑點按 ID 逐項覆核，原圖、inventory、候選同契約版本共同綁定；v1 僅供歷史讀取。
- inventory／review 各最多一次格式修正，附原回答雜湊、錯誤代碼及欄位。未解視覺疑點維持未完成；被拒表格保留候選與原因，唔以純文字回退冒充成品。
- packet wave 不可改寫、答案原子套用、相同答案可重播、衝突拒絕；用量按請求／回覆身份去重，未知值保留 null。並行預設上限 8，使用者可指定 16，受平台容量約束。
- 程式與合成頁驗收同真實識別準確率分開；今次修復唔宣稱 CER 改善。

## Unreleased — 通用技能與跨平台安裝

- 將唯一技能來源移到 repo 根目錄，保留 `SKILL.md`、`scripts/`、`references/` 同可選 metadata；移除 Codex plugin／marketplace 包裝。更新現行文件、相對引用、安裝入口及測試，保留既有內容完整性修正。
- 新增 `install.sh` 統一入口，分開技能安裝、可選 custom-agent adapters 同 OCR 環境初始化；區分 Gemini CLI／Antigravity 位置，支援指定技能集合目錄、只讀檢查、備份及舊版 manifest 復原。
- 固定 Hunyuan BF16 模型／projector 同 llama.cpp commit；Linux／WSL2 支援 CUDA／CPU，macOS 可選 Vision 或 Hunyuan Metal／CPU。獨立 venv、下載校驗及實際單頁 OCR smoke 決定就緒狀態，新環境預設一個 slot，保留已有配置。
- 核心按讀圖、檔案、腳本同可選子 agent 能力選流程；普通 worker／順序執行毋須安裝特定角色。校對明確使用 packet，外部 HTTP 服務須顯式選擇；同一 writer 重讀唔當成獨立審核，人工標註介面係可選整合。
- 新增真實單引擎嘅免 profile packet 校對及 `finish_book.py --single-engine --ocr-a` 定稿入口；唔偽造第二底稿，唔跑依賴雙引擎嘅 repair，跨頁接續疑點同工作證據保留。
- 跨平台支援、fake-runtime 測試、真實引擎 smoke 同各 agent 技能發現分開驗證；唔以安裝／搬遷成功宣稱 CER 改善。

## Unreleased — 2026-11-12

- 表格方向改用原頁與格位證據，檢查右側標題格；`auto` 代表邏輯紀錄方向，`as-printed` 只保留物理格網。未知軸向／紀錄先後唔再按合計或者語意猜定，單行／單列表頭亦須有圖像依據。
- 加完整頁圖區域普查、排版前內容補讀及排版後 coverage 覆核，涵蓋兩底稿同漏、零漢字頁、數字／外文、算式、原印項號與註號。補讀綁定當前候選及原圖；未解覆蓋問題唔封成完成頁。
- 跨頁表格發現夾住嘅註文、續表題名及未知正文，嚴格檢查同表證據、相鄰頁、方向政策及精確表頭；保留註文與題名，模糊歸屬留待核圖，同欄數本身唔授權拼接。
- 共用受限數字註號 parser，識別數字上標、全形數字及巢狀圓圈包裝，保留原號；數學式、普通列項、損壞包裝同 HTML 表內未能安全配對嘅註號唔自動升格成 Markdown 腳註。
- 新增合成正反例同內容完整性驗證指引；程式測試、實書核圖與同條件識別錯誤率比較分開報告，唔將測試通過當成準確率改善。
- 修正測試發現嘅 Hunyuan broker 原有啟動競態：首個子程序釋放 owner lock、但未退出時唔再重複啟動，保留原程序退出碼及日誌錯誤。

## Unreleased — 2026-11-11

- 新增 checkpoint-replay 批次問答傳輸層（`_packet_client.py` 同 `apply_packet_answers.py`）：`book_profile.py` 同 `proofread_pages.py` 預設採用 `--backend packet`（`EXIT_PACKETS_PENDING = 10`），將雙底稿異字裁決、融合式 `census+punctuate` 標點切片、表格重建同整頁結構排版自動去重打包成 `wave-NN/bundle-01..KK.json`，交由 Antigravity／Codex／Claude Code 嘅並行 subagent 批次作答；重跑時 `0ms` 重播並通過所有確定性校驗（`verify_characters`、標點計數、表格字元校驗），同時保留 `--backend http` 兼容本機 OpenAI 介面端點。
- 補齊雙底稿確定性管線工具鏈：`survey_pdf.py`（新增 `--images-dir` 純圖片目錄模式同 `source_kind: "images-dir"` provenance 驗證）、`run_hunyuanocr_second_look.py`（接上 `SharedHunyuanClient` 同 `GPUQueue`，寫入 `schemaVersion: 2` 封條並為 `< 3` 頁短樣本過濾外邊界頁碼）、`book_profile.py`（新增 `<= 4` 頁短篇零校準快速路徑、單一區塊 `survey_notation` 去重與雙頁書眉偵測）、`proofread_pages.py`（新增 `--table-orientation {auto,as-printed}` 與 `U+16FE6 -> 。。` 規範化）、`stitch_tables.py`（新增無損 `--output-dir` 同尾頁留空邊界支援）、`repair_book.py`（確定性跨頁修復，包含 `U+16FE6 -> 。。` 同散文／管道表內原印 `* -> \*` 轉義，保護 HTML `<table>` 同多項 `* ` 清單）以及 `finish_book.py`／`clean_book.py`。
- 收緊腳註與圖版忠實保留預設：`_footnotes.py` 預設保留原印數值編號（如 `[^240]`，`--renumber-footnotes` 為 opt-in），支援單頁部分配對同跨 1 頁孤立註號回溯；`finalize_page_markers.py` 預設將腳註留喺頁底原位（`--relocate-footnotes` 為 opt-in）；`audit_ocr_markdown.py` 識別已有定義嘅行內 `[^label]` 腳註引用，唔再誤報 `low-confidence-bracket`；`finish_book.py` 預設關閉 `_figures.py` 裁圖刪字（`--extract-figures` 為 opt-in）。
- `install_local_adapters.py` 升級至 schema v3，新增 `gemini-skill`（`~/.gemini/config/skills/jyut-ocr-proofread`），連同 Codex／Claude 共 7 個本機目標支援安裝、備份、回滾同 `--check` 驗收；單元測試擴充至 214 個，並加 `test_generic_text.py` 防止特定語料或評測書名滲入通用技能。

## Unreleased — 2026-10-25

- 補底稿→校對稿差異覆核同人工回饋指引：分開可見讀法退步與可證實因果、字位疑點與整句／同名提示、actionable closure 與未定字形。疑點交接綁定版本及精確出現位置，錯框後重核；保留無需修改嘅對照，唔新增實書字詞替換或聲稱跨材料準確率提升。
- Hunyuan／Paddle owned runners 預設使用同一個每用戶 FIFO GPU queue，以穩定 guard 同有 kernel lock 嘅順序 tickets 協調跨 session 載入。Lease 只覆蓋 OCR 批次，子程序退出先交棒；繼承 descriptor 防止 parent 突然消失時提早釋放，Linux parent-death 設定亦補檢查同競爭窗口。
- 加 queue directory／resource／wait-timeout 設定、等待狀態同 report；排隊超時／載入前 deadline 到期記 deferred，冇其他錯誤時 exit 75，cache-only resume 同外部 endpoint 唔入 owned queue。排隊唔接管 NInfer 或其他未參與程式。
- Hunyuan 新增預設共用 broker：私有 Unix socket、自動啟動／同設定發現、session 輪流分派、全局 slot 上限、單 client 取消隔離、最後 client 離開後卸載。其他 backend 等候時停止新 membership，現有批次完成先 FIFO 交棒；可退回 dedicated 模式。
- 新增 `--server-slots`／`--workers`，每 slot 保留相同 context，並封入底稿設定身份；request 固定 seed／關閉 prompt cache，並行輸出仍須核圖。Hunyuan backend 改由 fresh interpreter 安裝 parent-death signal，避免多線程 broker 使用 fork preexec callback。
- 加獨立多程序共用、首 client 被殺、失敗／截斷、deadline／啟動競爭、Paddle 交棒、16 client 上限與 parent-death 測試；本機 GPU 實測另保存，唔將單次吞吐或正常完成當成全書準確率。
- 借鑑 OCR pipeline 嘅通用失效機制，收窄文類詞表、語法黐段、套語拆 heading、金額單位同家具刪除嘅固定假設；引擎一致／通順只作線索，容許兩候選皆非，裁圖缺字同重複字串要返完整頁圖判斷。
- 補逐書標點／版式觀察、標點漏／增及數字／外文／註號核對；普通網格與 HTML 合併格分流，保留複合 list item，明確既有 deterministic auditor 嘅格式覆蓋限制。
- 將完整 release sequence 搬到專用 reference，釐清 basic 逐頁記錄同多 agent／封存要求；測試規則須有正反例，冇跨材料驗證唔宣稱準確率普遍提升。

- 明確智能校對／最終內容審核層跟隨調用技能嘅當前 agent session；OCR backend 只提供底稿，唔綁定本機 Qwen、NInfer、GPU 型號或個人路徑，方便跨 Codex／Claude Code 同不同電腦重用。
- Ubuntu／Linux 預設以 HunyuanOCR 產生逐頁底稿，PaddleOCR-VL 按疑點／版面需要選頁補讀；保留 macOS Apple Vision、agent 逐頁視覺校對、分段寫稿同獨立覆核。
- 從本機 OCR pipeline 移植獨立 runners，保存原始模型結果、render／tool／profile 身份同 JSON／TXT 完成記錄；加強 Hunyuan 路徑設定、截斷回覆拒收同續跑設定核對。
- 補本機模型路徑、單 GPU 順序執行、共享服務 ownership、各引擎座標／confidence 語義同部分頁第二底稿嘅 coverage 指引；runner 唔會自動接管 NInfer。

## Unreleased — 2026-08-25

- 將長書 LLM 並行整理成平台中立 role contract：主 agent 先封互斥 scan assignments，segment writer 只寫自己輸出並封 SHA 後 STOP-WRITE；另一個 read-only visual auditor 逐頁核原圖同 segment interfaces，任何 audit 後 patch 都要重新 seal 同重核受影響頁／相鄰頁。
- 新增 Codex／Claude Code custom-agent adapters。Codex 刻意省略 `model`／`model_reasoning_effort`，由 explicit spawn override、`[agents]` default、parent value 依次解析；Claude 明寫 `model: inherit`，同時保留平台級／per-invocation override 嘅較高優先權。通用 skill 唔 hard-code 平台 model ID，LLM children 同時數量亦唔超過 runtime slots 同八個兩者中較小者。
- 新增 `run_apple_vision_ocr.py` 同 `apple_vision_ocr.swift`：最多八個本機 Vision process、連續或選頁 selector、逐頁 staging／atomic promotion、hash-bound resume、partial-failure report 同顯式 overwrite；Apple Vision 只係 draft，亦無任何 Tesseract fallback。
- 新增 recoverable local installer：Codex／Claude skill 同兩個 Claude agents 用 exact symlink；因目前 Codex custom-agent sensitive read 拒絕 leaf symlink，兩個 Codex TOML 用 SHA-256／mode 封存嘅 regular-file projection。所有被替換 object 先移入 timestamped backup，manifest 封 absolute paths、regular-file hashes／modes、directory modes、nested symlink link text 同 projection bytes，source／target／backup overlap、安裝失敗 rollback、restore drift 全部 fail closed。
- 補 selector／worker cap／resume／partial failure／adapter model policy／link-copy drift、backup、rollback 同 restore regression；macOS 真 Vision performance 只作 opt-in smoke／benchmark，唔用易波動時間門檻代替正確性。Codex auditor 保留 forward-compatible `sandbox_mode = "read-only"` 宣告，但已記錄目前版本唔套用 role-level sandbox override，只有 effective read-only parent／child 先可當成硬只讀 audit。

## 0.2.5 — 2026-08-06

- 將星號題名／題注整理成 optional source-apparatus gate：`audit_title_notes.py` 按 configurable main heading level 比對 `*`／`**` marker arity，列 missing／orphan，同時封 target／boundary、generator toolchain hash 同 invocation profile；逐 heading ownership 仍由人核圖。
- Cross-page note audit 將所有 note→running-body transition 預設列為 actionable，刪除任何特定語言或來源句式嘅自動 exception；只有 project-local transition review exact seal 目前 target、boundary、前後文字、獨立 reviewer 同 visual evidence 先接受。
- 新增 `build_notation_inventory.py`，逐 scan 記錄通用 residual symbols，並封目前 target／authority、可選 literal-only inventory profile、generator toolchain hash 同 invocation profile；metadata、code、單行 HTML comments 同 Markdown links 唔當來源 notation，星號題名註只喺明確啟用時記錄。
- 新增 canonical `ocr-review-amendment` v1 schema同 `validate_review_evidence.py`：唯一 pass status、safe relative paths、current content／render／evidence hashes、非空而相異嘅 before／after rows、writer／verifier independence、changed＋immediate-neighbor coverage同零 unresolved全部 fail closed；before／after 嘅 baseline/current 語義關係仍由 reviewer 證明。
- 新增 `plan_release_rebuild.py`：重算 active included artifacts，找 direct hash mismatch／可由 discovery 重建嘅 missing，再沿 reverse dependency graph 傳播 transitive stale，輸出 dependency-first rebuild order；scope 外 missing path 直接 invalid，JSON output 亦拒絕 plan、declared artifacts 同工具嘅 hard-link／symlink／case alias；exit 0／1／2 分別代表 clean／stale／invalid。
- 補充跨冊 byte-identical evidence import、projection→amendment→current chain、typed dependency方向，以及 annotated／final audit CLI profile；final mode唔再誤傳 annotated-only expected-count／boundary options。
- 補 generator snapshot／typed dependency 規則，避免 planner 因工具未入 plan 而將舊 schema report 誤當 current；JSON report path 亦拒絕 target／authority／boundary 嘅 symlink、hard-link 同 case alias。
- 新增 profile routing 同核心更新門檻：單一材料嘅書名、scan、專名、符號同來源句式留喺 project contracts；核心只吸收跨材料重現或格式無關嘅資料遺失／fail-open／provenance 問題。Regression tests 全用 synthetic fixtures，實書只作外部 forward test。

## 0.2.4 — 2026-08-05

- 長目錄 reconciliation 新增明確 `reconciliation_scope` 同分冊／分卷 partitions；合訂本只交付其中一冊時，範圍外目錄項仍要逐項記錄 disposition。只要 active closure 出現 TOC reconciliation 專用 evidence type 就強制要 scope，唔可以靠省略欄位繞過。
- 新增 typed release evidence lifecycle：sidecar 明分 `active`、`historical`、`superseded`，active artifact 唔可以係 provisional／failing；dependency graph 要無環，並用 canonical subset hash 避免 ledger 互相封完整 hash 形成循環。
- 新增 `assemble_ocr_segments.py`，按 sealed page manifest、ordered segment SHA-256、連續 scan ranges 同 LF-only separator 重建 combined annotated；逐段同整體 markers 都要 exact 對上 `marker_expected`，dry-run 可 byte-for-byte replay 現有輸出。
- 新增 `validate_release_evidence.py`，驗證 `release-plan.json` 對所有 discovery 命中嘅 release artifacts 做 `classification: include`，或者 `exclude` 加理由；同時驗 dependency closure、singleton roles、supersession、typed scope evidence、partitions 同 canonical subset hashes。
- Finalizer receipt 升級至 schema v2，強制唯一 `release-plan` role並自動封存 plan-order active closure；output／receipt 唔可覆寫任何 classified evidence或新命中 pre-final globs。Final audit 會重驗目前 plan、全部 classified hashes同 exact closure；legacy v1 receipt只可作遷移診斷，唔可再封關。
- Annotated audit 會喺提供 page manifest 時即時核對 `marker_expected`，唔再等 final receipt 先發現 marker policy 錯配；notation policy 可逐類 inventory 全形方括號、六角括號、角括號、缺字方框同明示存疑符號。
- `audit_ocr_markdown.py` 新增 opt-in `--json-report`，輸出穩定嘅 machine-readable errors、warnings、accepted exceptions、issue counts 同 exit status，方便 release plan 封存及重播。
- 測試由 35 個增至 69 個，新增 assembly／release-plan／receipt v2、schema spoof、path alias、TOCTOU、legacy migration 同 adversarial closure regressions。

## 0.2.3 — 2026-08-05

- 長目錄先按每個 TOC scan 建立末端印刷頁碼 inventory，保存黐入題名／日期嘅 recovery，同時逐頁獨立核對全部引用；唔再用 parser 輸出或總項數代替目錄完整性。
- 將目錄條目、目錄分組同正文結構分成獨立 ledger：只比較有明確納入／排除理由嘅 TOC-eligible headings，並以正文分隔頁、running header 同首項互證分組起點。
- `source-verified-*` 新增獨立 challenge gate：除純註號等 routine apparatus 外，題名、日期、人名、官職、數字、機構等實質異文，以及所有頁碼差異／異序，都要由另一 reviewer 同時核對 TOC 同正文頁圖。
- 頁碼差異用 `page-exact`、`fixed-ocr-reference`、`source-printed-ref-differs-from-body-start` 等細分類，保留原目錄頁碼、正文實際起頁同鄰頁證據；唔再壓成普通文字異文或靜靜改正來源。
- Independent review 後嘅 late patch 要重新核對受影響頁，或者用包含具體 before/after、scan/render hash、獨立 verifier 同 evidence hash嘅 amendment ledger封存；before/after內容關係由 reviewer同 evidence證明，所有 release decision sidecars都要加入 assembly receipt upstream。
- `audit_ocr_markdown.py` 同 `finalize_page_markers.py` 新增 opt-in `--summary-only`，長書封關時可隱藏逐頁 context，同時保留 deterministic issue counts、exit code、write／receipt 摘要；預設 verbose 行為不變。
- 測試由 33 個增至 35 個，新增兩個 summary-only regression。

## 0.2.2 — 2026-08-03

- Boundary contract 新增反向保護：open list item 後嘅縮排續文或 nested item 若誤標 `join`，annotated audit 同 finalizer 都會拒絕，避免 `.strip()` 吞走 `list-cont`／`list-new` 語義後輸出 hard break 或黐爛子項；receipt 同時保留經視覺確認嘅 paragraph／structural list break，唔放寬無證據嘅 loose-list audit。
- Final audit 使用嘅 notation policy、source-exception ledger 同 page manifest 必須由 assembly receipt 封存；另傳未記錄 interpretation 檔會報 `assembly-audit-input-unsealed`。
- 新增 `--page-manifest`／`--source-pdf` provenance gate，逐次重算目前 PDF SHA-256、完整 `1..N` scan sequence 同每個 render hash；帶 receipt 時亦會對賬 sealed annotated markers，刻意略去頁必須以 sealed `marker_expected: false` 聲明。
- Review evidence 改成單向狀態：sidecar 重建唔可以將 `independent-checked` 降級，並要求 reviewer 同 writer 分開、覆核 range 同 evidence ledger／report hash 可追溯。
- 長目錄對賬要求逐項 disposition；TOC／正文數量相同唔代表文字完全一致，圈號省略、跨頁日期、題名／頁碼／排序真差異要各自按圖保留，唔可以留低未關閉嘅泛稱 `review`。
- 測試由 27 個增至 33 個，新增 list-cont／nested list 誤標、獨立 lists 結構邊界、unsealed final-audit input、PDF/render provenance 同 sealed marker coverage regression。

## 0.2.1 — 2026-08-03

- 將長書實作經驗整理成可選 release gate：先讀來源凡例、收齊所有 writer／auditor、凍結 segment hash，最後一個 patch 後重新 assemble、finalize 同完整 audit。
- 新增 `notation-policy.json` 支援；凡例證明 ASCII `[ ]` 係來源編者訂正時，final audit 唔再將佢無條件當 OCR confidence residual。
- 新增 stack-aware 全形 delimiter audit，同 duplicate-safe exact line／column／symbol `source-exceptions.json`；原書本身未閉或錯序標點要保存 scan page、整行文字同核圖說明，失效 exception 會報錯。
- Finalizer 新增 SHA-256 assembly receipt，同 ordered `--upstream` dependencies；final audit 可以偵測 late segment／manifest／annotated patch同 receipt 後 final 修改，並由目前 annotated + boundary contract byte-for-byte 重建 final，唔靠 mtime。
- 結構 audit 擴充至 tight-list item／continuation 空行、Markdown table 欄數、space-before-tab、missing final newline 同 EOF 多餘空行。
- 新增高信心 correction propagation、同名／相似平行篇章互校同 multi-pass OCR disagreement 工作法；全部只產生逐頁核圖候選，絕不自動同步或投票定稿。
- 收緊一次性 front matter／正文 apparatus 判斷：全書轉錄通常保留扉頁、英文題名、CIP／版權、凡例、圖說同來源資料，唔同重複頁面家具混為一談。
- 測試由 14 個增至 27 個，覆蓋 hash freshness、hard-link／case alias 防護、notation policy、精確來源例外、delimiter nesting、nested list、table 欄數、code fence 同完整 EOF whitespace。

## 0.2.0 — 2026-08-03

- 單一長篇 PDF 可以按連續頁段分派，但每個 writer 只寫獨立 segment；主 agent 獨佔最終 Markdown、assemble、頁段交界同 final audit。
- 新增 long-book workflow，分開記錄 PDF scan page 同 printed page，覆蓋 spread、旋轉、空白、完整／局部重複、缺失印刷頁同 page provenance。
- 明確規定原 PDF 真缺頁時嘅同版復原流程：先排除漏 OCR／重複／render 缺檔，再以可靠同版來源交叉核實，保存 evidence manifest，披露未能視覺核實嘅標點同行款。
- Apple/macOS Vision 可批量建立底稿，但直排閱讀次序、淡字、表格、註腳同跨頁內容仍須逐頁對 PDF。
- page marker 新增 `start`／`join`／`paragraph`／`structural`／`list-cont`／`list-new` 顯式 contract；finalizer 無完整 JSON manifest 或另一個 output path 會拒絕寫入，唔靠空行猜邊界，亦唔覆寫 annotated 底稿。
- final pass 新增結構 invariants：條號連續、人名或項目實數、A–J 類枚舉、表格列數及總數。
- 新增純 Python stdlib `audit_ocr_markdown.py` 同 `finalize_page_markers.py`，用 deterministic 檢查代替反覆即席腳本。
- 補充 untracked 新檔令 `git diff --check -- TARGET.md` 可能成為 no-op 嘅盲點，要求另做 trailing-whitespace audit。
- 更新 skill description、UI metadata、plugin metadata、README 同詳細 reference 導航。

## 0.1.2

今版將 `jyut-ocr` repo 入面近期香港中文 OCR 校對守則整理成可重用 Codex plugin skill，重點係令 agent 做長文校對、缺 OCR 補漏、表格整理同 final pass 時有一致嘅流程。

### 2026-06-16 同步摘要

本 repo 版本已同本機已安裝嘅 `jyut-ocr-proofread` 0.1.2 skill 對齊；主 `SKILL.md`、plugin metadata、詳細規則 reference 同常見陷阱 reference 都保持一致。呢次文件同步將最近新增同收緊嘅工作約束整理成以下幾類：

- Skill metadata 維持穩定 ID `jyut-ocr-proofread`，方便 Codex 以 skill 名稱載入；目標 repo 如有 `AGENTS.md` 或其他本地指示，仍然要先讀並優先跟本地指示。
- 多份互不重疊 Markdown/PDF pair 時，可以用並行 subagent 分工；主 agent 要先列出檔案、PDF 頁數或頁段、page marker 狀態、表格/尾頁/缺 OCR/跳頁風險，並避免多人同時改同一份 Markdown。
- 主 agent 要 review subagent 輸出、處理衝突同遺漏，並統一做完成前驗證；未逐頁視覺對照 PDF，唔可以話全文已完成校對。
- OCR 來源次序收緊：缺 OCR、補漏 OCR 或 page marker 跳頁時，即刻用 PDF render 圖像做視覺轉寫，唔為任何 OCR 服務停低。
- GJ.cool 只作背景或極短時間輔助：有 token、API 正常、未爆 quota，而且 5 秒 timeout 內即時返到，先用作底稿再核 PDF；timeout、quota、auth 失敗、服務不可用就直接繼續視覺識別。
- 如果 GJ.cool API 回覆 `wait for ... seconds`，本輪 OCR/校對任務餘下部分即刻停止自發重試 GJ.cool；之後即使用其他 token 或 IP 可能重置用量，都唔主動再試，除非用戶另開要求測 API/token。
- Apple/macOS OCR、Gemini、Tesseract、OCR JSON 同外部資料都只係提示；PDF 圖像仍係最終依據，外部研究唔可以覆蓋 PDF 原文。
- 深入改文前要查 page continuity：page marker 是否順序、有冇覆蓋最後非空白頁；無 marker 就用 render 圖或 contact sheet 建立 section-to-page map。
- 缺 OCR、跳頁或 residual scan 乾淨但內容唔連續時，要先當成內容缺漏處理，找本地底稿或直接從 PDF 圖像補回，唔可以只做格式 pass 就當完成。
- Markdown 結構規則更具體：page comments 只作臨時定位，完成前要刪；普通正文應一段一行；heading、bullet list、table、跨頁續文同 list spacing 要按 PDF 版面整理。
- 表格、票據、圖、淡字空白頁、尾頁印章等都要 render 放大檢查；直排表格要按版面理解欄列，必要時轉成 Markdown table，唔可以照 OCR 行序硬排。
- 常見高信心錯字 reference 擴充到官職、人名、地名、套語、數字符號、條文清單、heading、舊式標點、尾頁視覺陷阱同合法拉丁詞，供長文同 final pass 使用。
- 完成前驗證固定化：掃低信心括號、page comments、OCR notice、拉丁殘留、常見頁面家具同高信心錯字；刪 page comments 後檢查 soft wrap；最後跑 `git diff --check`。
- 最終回覆要交代 PDF 頁數、page continuity、是否逐頁視覺對照、有冇補 OCR、有冇疑難字、有冇殘留 brackets/page comments/OCR notices、舊標點/合法拉丁詞處理、一段一行檢查，以及 `git diff --check` 結果。

Bundled references 亦保留長文、表格、尾頁、章程、速記錄、講義合訂同常見高信心錯字陷阱，供 final pass 或高風險頁段再讀。
