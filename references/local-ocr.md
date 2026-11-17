# 本機 OCR 底稿同 packet 校對

## macOS 快速路線

Apple Silicon macOS 14+ 安裝 `--engine auto` 時，Paddle 預設由 CPU 做 PP-DocLayoutV3 版面分析，原裝、未量化嘅 PaddleOCR-VL-1.6 權重交獨立 `mlx-vlm==0.8.0` 服務用 Metal 識別。Linux 同舊環境直接調 runner 而冇新設定，仍然行 native。安裝器嘅 `--paddle-vl-backend auto|native|mlx-vlm-server` 記住要求；明確 `--device cpu` 或 `native` 唔會轉 MLX。

`JYUT_OCR_PADDLE_VL_BACKEND` 係安裝要求，`OCR_PADDLE_VL_BACKEND` 係實際後端。`PADDLEOCR_PYTHON` 負責版面 pipeline，`MLX_VLM_PYTHON` 係獨立 MLX venv（runner 可用 `--mlx-python` 覆蓋）。`OCR_PADDLE_DEVICE=cpu` 只講 Paddle 嗰邊，唔代表 MLX 用 CPU。直接執行可用 `run_paddleocr_vl.py --vl-backend mlx-vlm-server` 或 `--vl-backend native`。

MLX 服務由 runner 自己開喺隨機 loopback port，入咗 GPU 隊先載入權重；等自己個程序完成指定模型預載先接頁。版面 pipeline 一次只送一個識別請求，唔開多 worker。服務失敗會明確報錯，唔會暗中改用 CPU。正常完成、限時、訊號中斷同父程序被殺，都先清理 worker／服務再放隊列；全部頁已 current 就唔開服務。依賴版本同 hash 喺 `runtime/mlx-requirements.txt`。

另一條橫排快速路線係 `./install.sh --engine vision,hunyuan`，再用 `ocr_book.py BOOK --engines vision,hunyuan`：依次跑 Hunyuan B、交未校對草稿、Vision A、Hunyuan 補讀。底稿放 `ocr-vision/` 同 `ocr-hunyuan/`，選擇記喺 `reports/ocr-engines.json`，定稿會按呢個記錄揀 A，唔會畀舊 `ocr-paddle/` 蓋過。`--engine vision` 仍然係原有單引擎選項。Vision 識別語言同糾字設定保持原樣；直排同高 CER 唔係執行閘。

Vision 原始 observations 同 line IDs 保留；供下游用嘅 box 統一係左上角原點、相對頁面嘅 `[x,y,width,height]`，TXT 同分組區塊次序一致。邊緣位置只係家具候選，唔會單憑位置授權刪字。Vision 冇原生表格格網：Hunyuan 嘅 HTML／Markdown 表只提出候選，可靠對應完整 A 區塊先用聯合裁圖重建；否則明確交全頁圖處理，包含純數字表。補讀仍然負責漏讀，唔冒充表格結構驗證。

新版封條另外綁定所用引擎、後端、版面契約、區塊文字、box、label 同次序；改時間或搬檔案位置唔會令頁過期。新 Vision／MLX 底稿必須符合呢個契約；未變嘅舊 native 底稿同封頁繼續接受。明確換後端／引擎會報 stale，要按既有 `--overwrite` 流程重跑，唔會自動覆寫舊書。

macOS 記憶體用 `vm_stat` 嘅 free + inactive + speculative 作可回收估計，唔額外加 purgeable。Paddle 暫保留 12,288 MiB 預算（MLX VLM 同 CPU layout 同時存活）；順序引擎用最大值，Vision 路線唔計 Paddle。手動 memory override 同 Hunyuan slots 保留。評測成品統一存喺 benchmark repo，效能量度同驗收紀錄見 [issue #4 結案回覆](https://github.com/laubonghaudoi/jyut-ocr-proofread/issues/4#issuecomment-6952908371)。

安裝器預設兩個引擎都裝：**引擎 B，HunyuanOCR**（GGUF，經 llama.cpp）同**引擎 A，PaddleOCR-VL**（Intel Mac 裝唔到 Paddle，淨係 Hunyuan，Apple Vision 可以做提示）；`--engine hunyuan` 先係淨裝 Hunyuan。macOS 首次安裝先問用戶：下載呢兩個引擎（`--engine auto`），定係淨用內置嘅 Apple Vision（`--engine vision`，唔下載模型，行單引擎流程），見 `install.md`〈macOS：先揀引擎〉。所有輸出都只係底稿：讀法由校對者定，校對者係當前 agent（packet）或者本機模型（http），見 `SKILL.md` 第一節；揀 OCR 引擎唔會改校對模型。

首次使用先按 [install.md](install.md) 配置環境，再跑 `./install.sh --env-only --check`。以下命令從技能根目錄執行，使用設定嘅 `JYUT_OCR_PYTHON`。

## 兩個引擎（預設）同單引擎

- 預設兩個引擎讀全書：`ocr_book.py BOOK` 依次跑引擎 B、草稿、引擎 A 同引擎 B 補讀（見 `long-book-workflow.md`〈OCR 階段（ocr_book.py）〉），唔使揀，亦唔使問用戶。
- `--single-engine` 係明確例外：淨係得 Hunyuan 嘅機（Intel Mac，或者用戶話淨裝 Hunyuan）。之後 `proofread_pages.py --single-engine`、`finish_book.py --single-engine --ocr-a` 一路用；唔將同一份底稿複製成假第二引擎。
- 兩引擎分歧只係問題嘅位；一致亦可能一齊錯。唔自動投票、唔按多數字形全域替換。
- 安裝／初始化負責下載及核驗模型；跑書期間引擎缺失、裝置暫時用唔到或者單頁失敗，runner 出 ERROR 講原因：照講畀用戶知，唔無限重試，唔自行換模型，亦唔 fallback 去 Tesseract。

## 模型同裝置

喺 skill 目錄執行 runner。Hunyuan 優先用安裝器已核實嘅 `llama-server`、GGUF 模型同 mmproj；Paddle 用裝好 Paddle（GPU 版 `paddlepaddle-gpu` 或者 CPU 版 `paddlepaddle`）同 `paddleocr` 嘅獨立 Python 環境，worker 要支援 `PaddleOCRVL(pipeline_version="v1.6", device=..., vl_rec_model_dir=..., layout_detection_model_dir=...)`。權重唔包喺 skill，亦唔因為上游模型卡更新就自動換版本。

Paddle runner 唔會畀 PaddleX 第一次用先下載權重：嗰陣下載會喺 GPU 隊列同 worker 600 秒啟動限時入面進行，網慢就永遠下載唔完；GPU 交接嗰陣仲會白白停咗本地模型。權重由兩個本機資料夾讀：`PADDLE_VL_MODEL_DIR`（`--vl-model-dir`）同 `PADDLE_LAYOUT_MODEL_DIR`（`--layout-model-dir`），即係安裝器下載、核驗好再寫入 `env.sh` 嘅固定版本；冇設定就用 PaddleX 自己快取（`PADDLE_PDX_CACHE_HOME`，預設 `~/.paddlex`）入面已經有嘅一份。資料夾唔存在、`inference.yml` 寫嘅唔係該 pipeline 版本嘅模型，或者冇權重檔，runner 喺排隊之前就 exit `2`，叫你跑安裝器（`./install.sh`）再載入 `env.sh`；全書已經 current 嘅續跑唔使權重。有本機資料夾，worker 亦跳過 PaddleX 對模型網站嘅連線檢查。現有安裝用另一個相容 pipeline 時，明確傳 `--pipeline-version`，連埋嗰個版本嘅模型資料夾：v1.6／v1.5 用 PaddleOCR-VL-1.6／1.5 同 PP-DocLayoutV3，v1 用 PaddleOCR-VL 同 PP-DocLayoutV2。更換權重後更新 `--model-revision`／`PADDLEOCR_MODEL_REVISION`，令舊底稿唔會被錯當成同一模型嘅 current output；呢個 revision 只係自訂標籤，唔等於權重 hash。

可以沿用已核實嘅 `${XDG_CONFIG_HOME:-$HOME/.config}/jyut-ocr/env.sh` 路徑同並行設定，或者傳 CLI 參數；唔覆寫整份機器設定。已有呢份設定時，各 session 啟動前都載入同一份，連 `OCR_HUNYUAN_DEVICE`／`OCR_HUNYUAN_MODE`／`OCR_HUNYUAN_SLOTS` 一併沿用，唔只抄模型路徑而另揀 slots，否則會分成不同批次。唔好將 venv Python symlink resolve 成系統 Python。

| 設定 | CLI | 用途 |
|---|---|---|
| `JYUT_OCR_PYTHON` | 啟動 interpreter | 通用腳本用嘅獨立 venv Python |
| `JYUT_OCR_ENGINE` | 安裝器 `--engine` | 引擎要求：`auto`（兩個引擎）或者明確覆蓋 `hunyuan`／`vision` |
| `JYUT_OCR_ENGINES` | （安裝器寫） | 實際裝咗嘅引擎，例如 `hunyuan,paddle` |
| `JYUT_OCR_DEVICE` | 安裝器 `--device` | 裝置要求：`auto` 或者明確揀嘅 `cpu`／`cuda`／`metal` |
| `OCR_HUNYUAN_DEVICE` | `--device` | `auto`／`cpu`／`cuda`／`metal`；封入共用服務身份；續跑只記錄、唔比較 |
| `HUNYUAN_MODEL` | `--model` | Hunyuan GGUF |
| `HUNYUAN_MMPROJ` | `--mmproj` | 同版本視覺 projector |
| `HUNYUAN_LLAMA_SERVER` | `--llama-server` | 支援該模型嘅 llama.cpp server |
| `PADDLEOCR_PYTHON` | `--python` | Paddle venv 嘅 Python |
| `OCR_PADDLE_DEVICE` | `--device` | Paddle 嘅 `gpu:N`／`cpu`；冇設定就 Paddle 係 CUDA 版又見到卡用 `gpu:0`，否則 `cpu`；續跑只記錄、唔比較 |
| `PADDLE_VL_MODEL_DIR` | `--vl-model-dir` | 安裝器固定版本嘅 PaddleOCR-VL 權重資料夾 |
| `PADDLE_LAYOUT_MODEL_DIR` | `--layout-model-dir` | 同一 pipeline 嘅版面偵測（PP-DocLayout）權重資料夾 |
| `OCR_CPU_RESOURCE` | `--cpu-resource` | Paddle 用 CPU 嗰陣嘅隊列 resource，預設 `local-cpu` |
| `OCR_HUNYUAN_MODE` | `--server-mode` | 預設 `shared`；`dedicated` 獨佔批次 |
| `OCR_HUNYUAN_SLOTS` | `--server-slots` | 所有 Hunyuan clients 合共嘅並行上限 |

安裝器嘅 `--device auto` 會檢測環境，將結果寫入 `OCR_HUNYUAN_DEVICE`（`cpu`／`cuda`／`metal`）同 `OCR_PADDLE_DEVICE`（`gpu:0`／`cpu`），但記住嘅要求仍然係 `auto`，下次安裝會重新檢測；淨係明確 `--device cpu` 先一直用 CPU。直接調 runner 嘅 `--device auto` 則沿用 runtime 自行選裝置，唔係重新檢測硬件；日常使用應載入已配置嘅 `OCR_HUNYUAN_DEVICE`。CPU 會同時停用 decoder、projector 同 KV cache GPU offload。外部 OCR endpoint 嘅裝置由服務擁有人管理，runner 只接受 `--device auto`，並記錄為外部未受控裝置。

Paddle 嘅裝置一定係明確嘅 `gpu:N` 或者 `cpu`（`OCR_PADDLE_DEVICE`／`--device`，由安裝器寫入）。冇設定就照 PaddleX 自己嘅預設揀：Paddle 係 CUDA 版又見到卡就用 `gpu:0`，否則用 `cpu`，但揀咗乜都會記低。揀咗 GPU，runner 喺排隊之前先問 Paddle 係咪 CUDA 版、見唔見到嗰張卡；見唔到就 exit `2`，叫你重跑安裝器，或者刻意用 CPU 就設 `OCR_PADDLE_DEVICE=cpu`。PaddleX 本身只會出個警告就靜靜轉去 CPU，而 CPU 用 float32、GPU 用 bfloat16，兩者讀出嚟嘅底稿可能唔同，所以呢度唔准轉。Worker 載入前會再查一次，亦開咗 PaddleX 嘅 `PADDLE_PDX_DISABLE_DEVICE_FALLBACK`；載入後發覺模型其實喺 CPU，嗰批頁就當 failed，唔封底稿。用 CPU 嗰陣 worker 睇唔到任何 GPU（清空 `CUDA_VISIBLE_DEVICES`），因為佢冇攞 GPU 隊。實際裝置、dtype、Paddle 套件名同版本（`paddlepaddle-gpu` 或 `paddlepaddle`）、權重資料夾同隊列 resource，都記入每頁底稿嘅 `ocrProfile.execution` 同 run report 嘅 `execution`；只係記錄，唔用嚟判過期，所以同一本書 GPU 做咗嘅頁，之後改用 CPU 續跑照用。

張卡由本機模型（例如 agent 自己用緊嘅 LLM 服務）佔住嘅機，OCR 經 `run_ocr_phase.py` 交接：佢停模型、跑 OCR、再開返模型，詳見下面「GPU 交接」。冇設定交接就唔好停別人嘅服務；唔以 GPU utilization 暫時為 0 當成可以停服務。

由各 session 嘅主 agent 啟動 bundled runner，runner 自動加入同一個本機 Hunyuan 服務。GPU 上只載入一份 Hunyuan 模型；服務按 session 輪流分派頁面，保持有限並行。Paddle 用 GPU 嗰陣仍然按 FIFO 取得獨佔 GPU 位。LLM 子 agent 唔自行啟停 OCR 模型或共享推理服務。

## 跨 session 共用 Hunyuan 同 GPU 排隊

- Hunyuan 預設 `--server-mode shared`。同一電腦、同一 OS 使用者、相同 queue／resource、模型與 runtime／profile 身份嘅 sessions，自動連同一個由技能管理嘅 broker；唔需要手動開 server。Codex、Claude、Gemini 等本機 agent 只要實際載入呢份技能並調用同一版本 runner，就用同一個機制；唔代表各平台已自動發現技能。
- 每個 CLI 所選 scan 批次係一個 client。每個 session 有獨立 render／output／report；唔同 writer 唔共用輸出檔。全數可續用嘅批次唔啟動 broker。Client 喺 raw OCR 完成就離開，GPT／Claude／Gemini 校對期間唔持有 GPU membership。
- Broker 持有一個 GPU lease，模型只載入一次，所有 client 合共最多 `--server-slots` 個請求。每請求處理一張頁圖；16 個 sessions 唔等於每個都獨佔 16 個 GPU 位。`--workers` 控制單個 client 嘅未完成頁數，預設等於 server slots，亦唔可超過佢；每個 session 輪流獲分派，唔以一本書嘅所有頁塞滿無界隊列。
- 新環境通用預設係 1 slot，現有明確並行設定繼續保留；本機按實測用 `OCR_HUNYUAN_SLOTS` 或 `--server-slots` 設定。`--ctx-size` 係**每個 slot**嘅 context，總 context 係兩者相乘；唔縮細每請求容量換取並行。並行數唔係顯存自動偵測，其他電腦要按實際容量／吞吐選值。Client worker 數可以唔同；模型、runtime、有效 device、context、server slots 或 request profile 唔同就分成不同服務，依 FIFO 順序用 GPU。
- 關閉／取消一個 client，只移除佢嘅待處理頁同連線，其他 session 繼續。已送入 backend 嘅請求可能跑到完成或 timeout。最後一個 client 離開後，約 0.5 秒無新 client 就關閉自建 server，等模型程序退出後交棒；唔等 agent 校對全書。Broker 獨立於第一個 client，唔會因第一個 session 被關閉而停晒其他書。
- Broker／backend 異常退出時，未完成頁唔封成成功，runner 報失敗供續跑；唔無限重試錯誤。Linux backend 經獨立 launcher 安裝 parent-death signal，並繼承 GPU lease descriptor；broker 突然消失時，未退出嘅 backend 仍會阻止下一份模型提早載入。Broker 唔係無人值守嘅全書工作管理器：client 結束後未提交嘅頁，要由 agent 續跑。
- Paddle 或另一個設定嘅 backend 已排隊時，目前 Hunyuan broker 停止接受新 client，完成現有 clients 嘅批次後釋放 GPU；新到者等下一輪。長書想更早交棒可以用 page selectors 切批。`--server-mode dedicated --server-slots 1 --workers 1` 可用舊有逐批獨佔模式，同樣參與 FIFO；外部 `--endpoint` 仍由其擁有人管理，唔加入／卸載呢個共用服務。
- 共用資源隊列預設 `${XDG_STATE_HOME:-$HOME/.local/state}/jyut-ocr/gpu-queue`、resource `local-gpu`。Hunyuan 用 CPU 仍沿用此隊列以限制資源併發，唔會繞過共用／獨佔協調。Paddle 用 CPU 就改排自己嘅 resource `local-cpu`（`OCR_CPU_RESOURCE`／`--cpu-resource`）：同一時間只跑一個 CPU Paddle，但唔會佔住 GPU 隊幾個鐘，阻住其他 session 用 GPU 嘅 Hunyuan。可用 `OCR_GPU_QUEUE_DIR`／`--gpu-queue-dir` 同 `OCR_GPU_RESOURCE`／`--gpu-resource` 改。只有不同實體 GPU 先分 resource；唔以書名、session、port 或安裝路徑分隊列。Resource 唔會自行揀 GPU，裝置配置要一致。通訊 socket 同服務 logs 喺同使用者私有嘅 `jyut-ocr-UID/`：放喺 `XDG_RUNTIME_DIR`，冇就 `TMPDIR`，再冇就 `/tmp`（路徑長過 Unix socket 上限就跳去下一個；runner 同佢開嘅 broker 用同一個環境，所以會搵到同一個位）。冇其他 runner 用緊嗰陣，新 runner 會清走已經冇 broker、超過一日冇郁過嘅舊 socket、logs 同空資料夾。run report 記 broker／server PID、設定身份同 log 位置。
- 預設一直等 GPU；`--gpu-queue-timeout SECONDS` 同 `OCR_PHASE_DEADLINE` 可限制等候／啟動。Shared 模式 `--gpu-queue-timeout 0` 只加入已 ready 嘅服務，唔冷啟動；到期未處理頁記 `deferred`，冇其他 failed／blocked 時 exit 75，可以同一命令續跑。唔反覆開新 runner 代替等候。
- 呢個機制只協調同一設定嘅本技能 runners；不同 OS 使用者、容器、舊版 scripts 同其他程式唔會自動加入。本機模型服務唔係隊列成員，要喺 `env.sh` 用 `OCR_PHASE_RELEASE_CMD` 交代，經交接窗口協調（見下面「GPU 交接」）。唔因排到位就停別人服務或假設顯存足夠。有工作運行時唔刪 queue、socket 或 lock 檔。

## GPU 交接（本機模型佔住 GPU）

適用於 agent 本身就係本機模型（例如 Qwen Code 用本機 server 行緊嘅模型），或者部機有本機 LLM 服務長期佔住 GPU。喺 `env.sh` 設定：

| 設定 | 用途 |
|---|---|
| `OCR_PHASE_RELEASE_CMD` | 停本機模型嘅命令（例如 `systemctl --user stop 服務名`）。設咗即係「本機模型擁有 GPU」 |
| `OCR_PHASE_RESTORE_CMD` | 開返模型嘅命令，要可以重複執行 |
| `OCR_PHASE_HEALTH_URL` | 開返之後輪詢嘅網址；佢嘅 host:port 亦係搵其他 client 同開暫代服務嘅位置，同埋判斷 call 開始嗰陣模型開咗未（見下面「窗口點運作」）。冇呢個設定就分唔到，照舊按顯存決定 |
| `OCR_PHASE_READY_PROBE` | 預設冇。有啲服務 health 網址答咗、模型其實未載入完，未答得請求：設 `1` 就喺 health 之後再問 `PROOFREAD_ENDPOINT` 一個 1 token 嘅 chat completion（有 `PROOFREAD_MODEL` 就用佢），答到 2xx 先當開返；亦可以直接寫 chat completions 網址。同 health 共用 `--health-timeout`，時間計入預算；時限內都答唔到就當開唔返（exit 3，watchdog 照樣問）。預留俾還原嘅時間只計同一個設定下量到嘅還原（`timings.json` 嘅 `restoreProbe` 記住係邊個網址；冇就係淨係 health）：開咗、閂咗或者改咗呢個設定，之前量到嘅唔再用，由預設 60 秒重新量，唔使自己刪 `timings.json`。值唔啱（唔係 `0`／`1`／http(s) 網址，或者設 `1` 但冇 `PROOFREAD_ENDPOINT`）就 exit 2，乜都唔停 |
| `OCR_PHASE_MAX_SECONDS` | 一次 call 嘅總時間，連還原計埋；要低過 agent 嘅 shell 上限（Qwen Code 最多 600 秒，建議 540） |
| `OCR_PHASE_SWAP` | `auto`（預設：顯存唔夠或者讀唔到先停）、`always`、`never`（永遠唔停，runner 亦唔會因為本機模型而延後）。`auto` 同 `always` 都淨係停 call 開始嗰陣開緊嘅模型：用戶自己停咗嘅唔停、唔開 |
| `OCR_PHASE_REQUIRED_MIB` | 覆寫 OCR 需要嘅顯存；唔設就按今次 call 嘅引擎計 |
| `OCR_PHASE_TENANT_MIB` | 本機模型開返需要嘅空閒顯存；設咗就等夠先還原 |
| `OCR_PHASE_CLIENT_MATCH` | 逗號分隔，用嚟認模型其他 client 嘅命令行片段（例如 Qwen Code 嘅 `cli.js`） |
| `OCR_PHASE_CLIENT_IGNORE` | 逗號分隔，永遠唔等嘅 client：命令行片段，或者 `uid:N`（某個 OS 使用者嘅程序，包括報 pid `-1` 嗰啲）。俾一直開住連線池嘅 reverse proxy、LLM gateway、web UI 用 |
| `OCR_PHASE_NOTIFY_CMD` | 還原唔到嗰陣通知人嘅命令，訊息喺 `OCR_PHASE_NOTICE` |
| `OCR_PHASE_UNLIMITED_SHELL` | `1`：用戶自己拆咗 harness 嘅 shell 時限而且試過先設（agent 唔設）；`OCR_PHASE_MAX_SECONDS=0`（`--max-seconds 0`）先准停模型跑成本書 |
| `OCR_PHASE_STATE_DIR` | 交接狀態目錄，預設 `${XDG_STATE_HOME:-$HOME/.local/state}/jyut-ocr/handover` |

一個 OCR 階段一條命令，**一定前景**跑，tool call 嘅 timeout 開到最大：

```bash
"$JYUT_OCR_PYTHON" scripts/run_ocr_phase.py -- "$JYUT_OCR_PYTHON" scripts/ocr_book.py BOOK > BOOK/ocr.log 2>&1; echo "exit=$?"; grep -F '[ocr-book] DRAFT' BOOK/ocr.log; tail -3 BOOK/ocr.log
```

`grep` 嗰行係草稿：引擎 B 喺今次 call 讀完全書，`ocr_book.py` 就印 `[ocr-book] DRAFT 路徑`（PDF 隔籬嘅 `X-draft.md`；冇 PDF 或者抄唔到就係 `BOOK/draft.md`，括號講點解），`tail -3` 睇唔到佢。見到就照 `SKILL.md` 第 2 步話畀用戶知；冇就係今次 call 冇出草稿。

Agent 跑交接嘅規矩：

- 一個 tool call 淨係放一個 `run_ocr_phase.py`。唔准放背景（`is_background`、`&`、`nohup`、中途轉背景都唔得）：模型停咗嗰陣，行緊喺佢上面嘅 agent 冇模型可以諗嘢。
- 唔好將輸出 pipe 去 `tail`／`head`（`$?` 會變成 `tail` 嘅 exit code）：照上面先寫落 log，再 `echo "exit=$?"`。
- 照最尾一行 `[ocr-phase] RESULT exit=N` 做（下面嘅退出碼）。75 同 76 要再跑，就照抄 RESULT 行尾 `next call:` 嗰條命令（已經加咗 `--round N+1`），後面照舊接 `> BOOK/ocr.log 2>&1; echo "exit=$?"; grep -F '[ocr-book] DRAFT' BOOK/ocr.log; tail -3 BOOK/ocr.log`；每次 tool call 嘅 description 亦寫唔同字（例如第幾輪）：Qwen Code 見到連續 5 個一模一樣嘅 tool call 會當死循環。
- `OCR_PHASE_UNLIMITED_SHELL=1` 先可以唔設 tool call timeout、一個 call 做晒（設任何數都會有返時限）。呢個值淨係用戶喺 `env.sh` 設：佢拆咗 harness 嘅 shell 時限（例如 Qwen Code 嘅 `tools.shell.defaultTimeoutMs: 0`），再用一個長過舊上限、冇 timeout 嘅 call（例如 `sleep 700; echo ok`）試過。Agent 唔好自己設、唔好喺命令前面 export，亦唔好因為今次未撞過時限就當冇時限。咁樣都俾 harness 殺咗，即係設定冇生效：唔好再試，話畀用戶知，之後照有時限咁做。冇呢個設定，交接同 `wait_for_run.py` 嘅 tool call timeout 照開到最大（Qwen Code `600000`），`OCR_PHASE_MAX_SECONDS` 低過 shell 上限。
- 背景有步驟（`book_profile.py`、`proofread_pages.py` 經 http）用緊模型嗰陣，唔好開交接：佢會等嗰啲請求，等唔切就 exit 76。

`ocr_book.py` 喺同一個時限內按次序做 Hunyuan、`build_draft.py`（Hunyuan 讀完全書即刻出俾用戶睇嘅初稿）、Paddle 同補讀；封好嘅頁一律跳過，所以同一條命令再跑就由上次停低嘅位置繼續。一個交接窗口包晒所有引擎，每次 call 模型只停一次、開一次。前面嘅步驟喺今次 call 已經封咗頁，下一個引擎就要淨低嘅時間夠「載入模型加最長一頁」先開（量度值記喺 `BOOK/reports/ocr-times.json`，每個引擎封到頁就更新；載入時間由 runner 開始計到引擎自己話模型載好（run report 嘅 `modelReadyAt`：llama-server 答到模型、Paddle worker 嘅 ready），冇呢個訊號就用第一頁封好嘅時間減嗰頁自己用嘅秒數，`loadFrom` 講用咗邊個；未量度用預設值）；唔夠時間就唔載入，成條命令 exit 75。今次 call 第一個要載入嘅引擎唔受呢條限制（預算本身太細嘅話，要靠佢報錯）；但佢有足估計時間都喺第一頁之前用晒時間，就記低「載入最少要咁耐」（`loadAtLeast`），exit 75，下次 call 用返新估計；冇足估計時間就 exit 1，即係預算太細，要加大；RESULT 行會列出開始嗰陣淨低幾多秒、載入加最長一頁要幾多秒。Paddle 同 Hunyuan（dedicated 或者 shared）載入模型都最多等到時限，唔會超過。Shared Hunyuan 嘅 broker 攞到 GPU、開始載入 server 會話畀 client 知；到時限仲載緊，就唔當排隊（唔係 75，唔會喺交接窗口變成 76 再跑過無限次），runner exit 1，講明預算太細、載入用咗幾耐。只有 Hunyuan 嘅機（例如 Intel Mac）加 `--single-engine`。

退出碼：

- `0`：做完。
- `75`：時間到，已封嘅頁保留，模型已經開返；再跑同一條命令。75 同 76 嘅 RESULT 行最尾有 `next call:`，就係同一條命令加 `--round N+1`：`--round` 乜都唔改，淨係令連續幾次 call 唔會一模一樣（agent harness 嘅 loop detection 會將連續相同嘅 tool call 當成兜圈）；照抄嗰條就得。直接跑 `ocr_book.py` 嘅 75 一樣有。（直接跑、冇經 `run_ocr_phase.py` 而 RESULT 行話 GPU 屬於本機模型就唔同：照佢列嘅命令經交接再跑，見下面「窗口以外」。）
- `76`：GPU 忙（其他工作排緊 GPU 隊列、另一個交接進行緊，或者模型其他 client 仲用緊佢）；通常**乜都冇停**，等一陣再跑（照抄 RESULT 行嘅 `next call:`）。係 client 擋住嘅話，RESULT 行會寫明係邊個 pid；次次都係同一個一直連住嘅 proxy／gateway／web UI，就要設 `OCR_PHASE_CLIENT_IGNORE`（見下面），再跑都冇用。runner 喺窗口入面一頁都冇封到就 exit 75（排隊或者冇時間載入模型），亦報成 76，因為即刻再跑只會白白停一次模型；呢個情況模型其實停過又開返，RESULT 行會照講。
- `1`：runner 失敗；或者停咗模型之後顯存仍然唔夠（RESULT 行 `GPU FULL`）：即刻開返模型，列出數字，再逐個列 GPU 上面嘅 compute 程序：pid、使用者、程序名、用幾多 MiB、屬於邊個 systemd unit，連釋放佢嘅命令（systemd 服務就 `systemctl --user stop UNIT`，系統服務 `sudo systemctl stop UNIT`，其他就 `kill PID`；其他使用者嘅程序就要佢本人或者 `sudo`）。技能自己乜都唔殺、唔停，交俾用戶決定；釋放咗再跑同一條命令；或者預算太細，引擎載入模型都唔夠時間（RESULT 行列出時間，要加大預算）。
- `2`：設定錯誤，乜都冇做。
- `3`：模型冇開返到；watchdog 會繼續試，睇 `status.json`。上次交接留低模型冇開，今次 call 會先開返；開唔返亦係 `3`，一頁 OCR 都冇跑，RESULT 行寫明手動開返嘅命令。

窗口點運作：

- 開窗口要先攞到全機交接鎖同共用 GPU 隊列，兩樣都係唔等、即試；攞唔到就 exit 76，唔停模型。
- 需要幾多顯存：今次 call 嘅引擎取最大嗰個（佢哋輪住跑，唔係加埋）。Paddle 約 12 GiB；Hunyuan 按 slots × 每 slot context 計 KV cache（每 token 約 96 KiB），再加約 3.4 GiB，例如 16 slots × 16384 約 27.4 GiB。Hunyuan 用 CPU 或外部 endpoint、Paddle 用 CPU（`OCR_PADDLE_DEVICE=cpu`／`--device cpu`）就唔計（Linux）。macOS 冇 nvidia-smi，用 `vm_stat` 睇共用記憶體；Linux 讀唔到（冇 nvidia-smi 或者佢答唔到）就當唔夠，照停（模型開緊先停）。
- 模型開咗未：call 開始嗰陣先睇。`OCR_PHASE_HEALTH_URL` 喺 2 秒內答 2xx、個 host:port 有嘢 listen 緊（例如模型仲載緊），或者 GPU 上面有佢嘅程序（listen 緊嗰個 port 嘅程序、佢嘅子程序，或者命令行寫明 `--port` 係嗰個 port），都當開緊，照上面計使唔使停；停咗就一定開返。冇開（用戶自己停咗）就唔停、唔開、冇暫代服務、冇 watchdog：GPU 夠位就直接跑；唔夠位就 exit 1（`GPU FULL`），照下面退出碼 `1` 列出佔住 GPU 嘅程序同釋放佢嘅命令，乜都唔停。`status.json` 寫 `left-down`，唔係「交接留低冇開」，所以下一次 call 唔會開返佢。冇 `OCR_PHASE_HEALTH_URL` 就分唔到，照舊按顯存決定，停咗都照開返。WSL2（nvidia-smi 列唔到程序）同 macOS（冇 nvidia-smi）淨係靠 health 網址同 port 判斷。每次 call 嘅結果寫喺 RESULT 行尾 `[gpu: …]`（例如 `model up, stopped and started again`、`model not up, left as it was`），同 `status.json` 同 `timings.json` 嘅 `lastWindow`：`modelUp`、`released`、`restored`，`GPU FULL` 嘅話仲有 `heldBy`。
- 停模型之前先搵模型嘅其他 client（`scripts/_tenant_clients.py`）：有請求喺線上嘅，等佢做完先停；預算唔夠等就 exit 76。最多等到淨低嘅時間仲夠一個窗口：停模型（同等顯存放返出嚟）嘅時間，加今次 call 引擎之中最慢嗰個嘅「載入模型加最長一頁」（`BOOK/reports/ocr-times.json`，未量度用預設值），最少 60 秒；等完唔夠就 exit 76，乜都唔停，因為咁短嘅窗口載唔切模型、一頁都封唔到。預算本身已經唔夠呢個數，就照舊最少留 60 秒。閒置嘅 session 唔使等：停咗模型之後，同一個 port 有暫代服務答 503 加 `Retry-After`（大約窗口長度嘅六分一），叫佢遲啲再試，開返模型之前先關。停模型嘅命令返咗而個 port 仲未放（例如淨係發咗停止訊號），暫代服務最多再試 5 秒先放棄。冇 `OCR_PHASE_HEALTH_URL` 就唔搵 client、冇暫代服務。
- runner 喺自己嘅 process group 跑，用窗口私有嘅 GPU 隊列（`OCR_GPU_QUEUE_DIR`）同 `OCR_PHASE_WINDOW`：唔會排喺交接自己嘅 lease 後面塞死，其他 session 照排隊等。runner 命令唔好自己加 `--gpu-queue-dir`。
- 窗口以外：設咗 `OCR_PHASE_RELEASE_CMD`，直接跑 runner（或者其他 session 嘅 runner）唔會將模型載入塞滿咗嘅 GPU，而係 exit 75，寫明「GPU belongs to the local model; run through run_ocr_phase.py」，摘要尾多一行 `NOT RUN: …, not the same command again`。呢個 75 唔係「時間到」：直接再跑同一條命令只會再延後。`ocr_book.py` 嘅 RESULT 行會寫明要經 `run_ocr_phase.py` 跑，連成條命令列埋；`wait_for_run.py` 讀到亦係咁講。Linux 上 Paddle 用 CPU 唔使模型嘅顯存，唔受呢條限制，照排自己嘅 `local-cpu` 隊。
- 開返模型之前，要等窗口嘅 OCR 程序全部走晒：runner 成個 process group、runner 報告嘅 Hunyuan broker 同 server（`OCR_PHASE_CHILDREN`），同私有隊列入面每一個 lease。GPU 子程序會繼承 lease，所以 WSL2 列唔到 GPU 程序都照用得。等一陣仲喺度就殺；殺完仲喺度就唔開返模型（exit 3），交俾 watchdog。OCR 模型仲喺 GPU 嗰陣，一定唔開返模型。
- watchdog：淨係今次 call 停咗模型先開。wrapper 被殺（包括 SIGKILL）都會接手：殺窗口嘅程序樹，等 OCR 模型走，再開返模型；開唔到就 backoff 重試半個鐘，寫 `status.json`，有 `OCR_PHASE_NOTIFY_CMD` 就通知人。watchdog 放棄咗或者開返失敗之後，下一次 call 一開窗口就睇 `status.json`：模型 health 答返就當已經開返；唔係就先跑 `OCR_PHASE_RESTORE_CMD` 開返模型，再重新計使唔使停模型；開唔返就 exit 3，一頁 OCR 都唔跑，RESULT 行寫明手動開返嘅命令。唔會因為模型冇開、顯存好多，就當「唔使停模型」照跑，報 DONE（講緊交接留低冇開嘅模型；用戶自己停咗嘅，`status.json` 係 `left-down`，唔會開返）。watchdog 仲試緊（佔住 GPU 隊列）嘅話就 exit 76，ERROR 行會講。狀態、watchdog log 同量度（`timings.json`）喺 `${XDG_STATE_HOME:-$HOME/.local/state}/jyut-ocr/handover/`（私有，0700）。
- 時間：所有等待（runner 收尾、kill grace、等 OCR 模型離開、開返模型、health check）都計入預算，一次 call 唔會超過 `OCR_PHASE_MAX_SECONDS`。預留俾收尾嘅時間按呢部機量度到嘅還原時間調整，未量度就預 60 秒；量到嘅估計最多 300 秒，亦唔超過預算嘅一半，所以還原慢過頭都唔會令之後每次 call 都 exit 2；冇預算嘅 call 量到嘅還原時間只會拉低、唔會拉高呢個估計（慢過估計嘅另外記喺 `unbudgetedRestoreSeconds`）；還原未完、call 嘅時間就用晒（exit 3），佢用咗幾耐都照記入 `restoreSeconds`，當係最少要咁耐，下一次 call 就預多啲，唔會次次都喺同一度斷；停模型嘅時間亦會量度（`timings.json` 嘅 `releaseSeconds`），未量度就預 30 秒。
- `--max-seconds 0`（冇預算）只限 shell 冇時間上限，而且要 `OCR_PHASE_UNLIMITED_SHELL=1` 講明；否則要停模型就會拒絕。

## GPU 交接期間嘅其他 client

停本地模型之前，`run_ocr_phase.py` 用 `scripts/_tenant_clients.py` 搵其他用緊模型嘅程式，等佢哋用完先停；停咗之後喺空出嚟嘅 port 開暫代服務。

- **點樣搵**：
  - 「socket」：有 ESTABLISHED TCP 連線去 `OCR_PHASE_HEALTH_URL` 嗰個 host:port 嘅程序。Linux 讀 `/proc/net/tcp`、`/proc/net/tcp6` 同各程序嘅 fd；macOS 用 `lsof`。URL 係 loopback 位址，就連去任何 loopback 位址同一個 port 都計。其他 OS 使用者嘅連線睇唔到係邊個程序，報 pid `-1`，照樣要等。
  - 「process」：命令行包含 `OCR_PHASE_CLIENT_MATCH` 任何一段嘅程序（逗號分隔，例如 `@qwen-code/qwen-code/cli.js,zed-editor`；空白段會略過）。閒置嘅 session 冇開連線，淨睇網絡睇唔到，所以要呢個設定。
  - 「ignored」：上面兩種 client 入面，命令行包含 `OCR_PHASE_CLIENT_IGNORE` 任何一段、或者屬於佢列出嘅 `uid:N` 嘅（例如 `nginx,litellm,uid:33`）。永遠唔等；停模型嗰陣佢條連線會斷，之後再連就由暫代服務答 503。
  - 自己、自己嘅上層（shell、agent harness）同下層（runner、watchdog）一律唔計；殭屍程序唔計。
- **等嘅規則**：每隔 `poll_s` 秒查一次；一查到冇「socket」client 就可以停模型，超過 `timeout_s` 仍然有就唔停。「process」client 唔使等：佢冇請求做緊，下一個請求會畀暫代服務叫佢等。只係保留住（keep-alive）嘅連線一樣計：Qwen Code 0.27.3 答完一個請求之後連線留 60 秒，所以啱啱答完嘅 session 最多要等大約一分鐘。Reverse proxy（nginx）、LLM gateway（例如 LiteLLM）、web UI（例如 Open WebUI）或者編輯器嘅連線池會一直開住上游連線，有冇請求都唔放，每次 call 都會等到預算用完再 exit 76，永遠開唔到窗口。所以 exit 76 嘅 ERROR 同 RESULT 行會列出仲連住嘅 pid 同命令行；認得係呢類 client，就將佢命令行嘅一段（另一個使用者嘅程序報 pid `-1`，就用 `uid:N`）加入 `OCR_PHASE_CLIENT_IGNORE`。真係做緊嘢嘅 agent session 唔好加：佢做緊嘅請求會喺停模型嗰陣斷。
- **暫代服務**：所有 method、所有路徑都答 `503`、`Retry-After: N`，同 JSON `{"error":{"message":"local model paused for OCR; retry after N seconds","type":"server_busy"}}`。個 port 仲有嘢 listen（真服務未走）就唔開。佢讀晒請求 body 先答，又等 client 先斷線（最多 5 秒）。還原模型之前先關暫代服務：即刻唔再收新連線，已經喺度嘅請求有 2 秒答完；之後仲開住嘅連線（閒置 keep-alive、未上載完、答咗都唔斷）用 reset 斷，唔送 FIN。邊個先送 FIN 邊個留 TIME_WAIT，所以個 port 冇 TIME_WAIT，真服務即刻 bind 得返，有冇設 `SO_REUSEADDR` 都得。
- **Qwen Code 0.27.3 點處理**（讀安裝咗嘅程式碼，未實測）：
  - 每個請求嘅外層試 7 次，429／503 有 `Retry-After` 就等足 N 秒，冇上限；
  - 入面嘅 OpenAI SDK 再試 `generationConfig.maxRetries` 次（冇設定係 3），N 細過 60 先照等；
  - chat stream 對 429／503 再成輪重試 `maxRetries` 次（冇設定係 10），每次至少等 60 秒；
  - 所以由佢第一個請求計起：`maxRetries` 係 0 大約撐 6×N 秒；係 1 而 N 細過 60 大約撐 26×N＋60 秒（N＝30 即 840 秒）；N 由 60 起反而短，大約 13×N；
  - 錯誤訊息唔可以似 timeout（`timeout`、`timed out`、`deadline exceeded` 等）：Qwen Code 會將呢類錯誤重新包過，`Retry-After` 就冇咗。
- **限制**：
  - 查完之後先開始嘅請求睇唔到；喺查完同停模型之間開始嘅請求會斷。
  - 暫代服務只幫到識 `503`＋`Retry-After` 嘅 client；編輯器等其他程式點反應未知。
  - 經本機另一個位址（例如 LAN IP）連入嘅 client 睇唔到，要靠命令行對中。
  - 依賴暫代服務之前，要得用戶同意、停咗本地模型，實測一次。

## 並行設定同核圖

本機實驗已確認單 server 多請求可行；樣本／排程大小會影響收益，八頁短測同持續 32 頁測試可以有不同結論。唔因顯存有餘就開多份模型，亦唔將某部機嘅設定寫死成通用預設。

比較時固定 render hashes、模型、有效 context／輸出上限、prompt 同採樣設定，分開模型啟動、warmup、OCR 同校對時間；量度實際活躍請求、吞吐、顯存、失敗／截斷同逐字差異。並行同重跑都可能改變輸出，即使 temperature 0／固定 seed 亦唔保證 byte-identical。正常完成或者 hashes 相同唔代表來源內容正確。

## 命令

`RENDER_DIR` 入面用 `page-0001.png`、`page-0002.png` 命名，數字係 PDF scan page，唔係 printed page。`survey_pdf.py` 支援由來源 PDF 切圖，亦支援 `--images-dir` 為已有 `page-NNNN.png` 目錄建立 `source_kind: "images-dir"` 嘅 `page-manifest.json`（唔改原圖）。輸出放工作目錄，唔覆蓋原圖或者最終 Markdown。

以下假設已初始化；先讀過設定檔確認用途，再於同一 shell 載入。用自訂 `--config-dir` 時改成安裝器印出嘅位置：

```bash
. "${XDG_CONFIG_HOME:-$HOME/.config}/jyut-ocr/env.sh"

# 1. Render PDF，或者用 --images-dir 載入現成 page-NNNN.png。
"$JYUT_OCR_PYTHON" scripts/survey_pdf.py /path/to/book.pdf BOOK
# "$JYUT_OCR_PYTHON" scripts/survey_pdf.py BOOK --images-dir /path/to/images

# 2. 兩個引擎、草稿同補讀；本機模型佔住 GPU 就經 run_ocr_phase.py（見上面「GPU 交接」）。
"$JYUT_OCR_PYTHON" scripts/ocr_book.py BOOK
"$JYUT_OCR_PYTHON" scripts/build_draft.py BOOK

# 3. Book profile（<=4 頁嘅短檔唔使），再逐頁校對；A 套 exit 10 就答 bundle，再跑同一條。
"$JYUT_OCR_PYTHON" scripts/book_profile.py BOOK/renders BOOK/ocr-paddle \
  BOOK/ocr-hunyuan BOOK/book-profile.json
"$JYUT_OCR_PYTHON" scripts/proofread_pages.py BOOK/renders BOOK/ocr-paddle BOOK/proofread \
  --draft2-directory BOOK/ocr-hunyuan

# 4. 全部頁封好之後定稿（跨頁表格已經喺第 3 步尾自動接好）。
"$JYUT_OCR_PYTHON" scripts/finish_book.py BOOK
```

單引擎：第 2 步 `ocr_book.py BOOK --single-engine`；唔跑 profile；`proofread_pages.py BOOK/renders BOOK/ocr-hunyuan BOOK/proofread --single-engine`（逐頁對住頁圖轉錄，底稿做提示）；`finish_book.py BOOK --single-engine --ocr-a BOOK/ocr-hunyuan`。macOS 選 Vision 時用 `run_apple_vision_ocr.py BOOK/renders BOOK/ocr-vision --first-page 1 --last-page N --workers auto --report BOOK/reports/vision-run.json`（`N` 係 `BOOK/renders` 入面嘅頁數；呢個 runner 一定要講明頁範圍，`--first-page` 同 `--last-page` 要一齊畀，或者用 `--pages`），再將上面嘅 `ocr-hunyuan` 換成 `ocr-vision`。

`finish_book.py --single-engine` 必須明確提供真實 `--ocr-a`，唔接受 `--ocr-b` 或 `--extract-figures`。佢驗逐頁 seals，再組裝及執行 final audit，**唔跑依賴兩引擎嘅 repair**；跨頁段落先保持分開，可能需要接續嘅位置記成疑點（`single-engine-boundary`），唔憑單一底稿自動黐段。工作檔同證據會保留（唔清走）。Final audit 嘅錯誤同雙引擎路線一樣，記入 `doubts.md` 做疑點，完成稿照交付，RESULT 行寫明有幾多個。

個別 runner 都可以直接跑（例如淨係重做幾頁）：`run_hunyuanocr.py RENDER_DIR OUT_DIR`、`run_paddleocr_vl.py RENDER_DIR OUT_DIR`、`run_hunyuanocr_second_look.py RENDER_DIR OCR_A_DIR OCR_B_DIR`，都接受 `--pages 3,8-10` 或 `--first-page 1 --last-page 80`，唔畀 selector 就處理目錄中所有合法 `page-NNNN.png`。Report（`--report`）放喺各自 OCR output 目錄外；兩個引擎唔共用同一個 output directory。

`proofread_pages.py` 冇 `--draft2-directory` 時，先用 profile `sources` 記低、量度嗰陣用嘅引擎 B 目錄，再搵 `DRAFT_DIR` 隔籬嘅 `ocr-hunyuan/`；都搵唔到就 exit `2`，叫你傳 `--draft2-directory` 或者 `--single-engine`。兩份底稿 JSON 嘅 `engine` 要係 A＝PaddleOCR-VL、B＝HunyuanOCR；掉轉咗或者係其他引擎就 exit `2`，寫明點樣調返轉。`finish_book.py` 對 `--ocr-a`／`--ocr-b` 一樣咁查，亦先用 profile `sources` 記低嘅目錄，再用預設目錄名。

某頁缺引擎 B 嘅底稿，預設唔校對：引擎 B 嘅 OCR 可能仲行緊或者中斷咗。嗰啲頁當 failed 計（run report 嘅 `counts.engineBMissing`），stderr 有 WARNING 列出頁碼；等引擎 B 行完，再行同一個命令就得。真係要淨用 A 嘅底稿轉錄，就加 `--allow-missing-engine-b`。缺 A 就淨用 B 嘅底稿轉錄（冇 A 嘅版面）。淨用一個引擎轉錄嘅頁都加 `engine-draft-missing` 疑點，run report 計入 `counts.oneEngine`；已經咁樣封好嘅頁，之後冇加呢個開關都照計 current。兩個引擎都冇底稿，或者冇 render，都算 failed。補返底稿之後，嗰頁會報 blocked，要對嗰幾頁加 `--overwrite --pages N` 重做。冇 profile 嘅雙引擎文件，`<=4` 頁就用同 `book_profile.py` 一樣嘅免校準快速路徑；更長就要先跑 `book_profile.py`。

**校對 transport**：預設 `--backend packet`，題目寫成 bundle、exit `10`，由當前 agent 或者普通 packet workers 讀該 wave 嘅 `bundle-*.json`、睇圖、寫 answers，經 `apply_packet_answers.py` 驗證追加，再重跑同一條命令；命令未 exit `0` 前唔跳去下一步。冇子 agent 時由當前 agent 順序回答。完整格式見 [subagent-orchestration.md](subagent-orchestration.md)。`--backend http` 經 OpenAI-compatible 服務問模型：endpoint 用 `--endpoint`，冇就用 `PROOFREAD_ENDPOINT`；model 用 `--model`，冇就 `PROOFREAD_MODEL`，再冇就用服務 `/v1/models` 列出嘅第一個；要 key 就設 `PROOFREAD_API_KEY`。當前 agent 本身就係嗰個本機模型（`JYUT_OCR_AGENT_IS_LOCAL_MODEL=1`，或者 Qwen Code 嘅 `QWEN_CODE_MODEL` 等於 `PROOFREAD_MODEL` 而 `PROOFREAD_ENDPOINT` 喺 loopback），預設就係 http，packet 會被拒絕（頁圖唔可以入佢嘅 context）；唔係嗰個模型就設 `JYUT_OCR_AGENT_IS_LOCAL_MODEL=0`。`book_profile.py` 用同一套規則。呢個同 Hunyuan OCR runner 嘅外部 `--endpoint` 係兩層唔同介面，唔好混用。

按實際新出嘅 wave 回答；開咗 `--content-fidelity on` 就包括整頁 inventory、內容補讀同 coverage-review，唔按固定 wave 數跳過任務。`auto` 照評測規則轉表：橫排表保持原行列，直排表由右至左嘅直欄變成由上至下嘅行；只有明確要求保留物理印刷格網先傳 `--table-orientation as-printed`。

無設定檔時直接提供 `--model /path/to/model.gguf --mmproj /path/to/mmproj.gguf --llama-server /path/to/llama-server`。Hunyuan（包括 `run_hunyuanocr_second_look.py`）預設自動建立／加入私有 broker，backend 使用自選 loopback port；唔掃描或接管其他 HTTP 服務。`--port` 只用於 `dedicated` 模式（預設 8095），已佔用就報錯。

`auto` 唔係無條件轉置，亦唔睇內容：印刷方向由頁面版面判斷，重建出嚟嘅物理格網直接照方向轉；引擎 A 嘅格仔唔一定係物理格網，所以先用直排頁嘅合計、引擎 B 嘅閱讀次序（成張表逐行讀要等於頁面閱讀次序）定轉法，全部都定唔到先問模型對圖。結構步自己寫嘅表（引擎格仔唔收、又冇重建）都要守呢條：橫排頁照收；其他頁要引擎 B 嘅閱讀次序（照寫、對角轉置、轉九十度、倒轉四樣之中定到一樣）同佢嘅分行（引擎 B 一行讀到嘅格，表入面要喺同一行）指向同一個轉法先轉（封存頁 `tableOrientation` 記 `structure pass table N`，`decisionChanges` 記 `D23`）；有啲有橫框線嘅表，引擎 B 一格一行咁逐橫帶讀，淨睇次序會轉錯。兩樣定唔到同一個轉法，就照結構步寫法、唔問模型。兩樣都係引擎 B 一次讀法：佢逐橫帶讀、一行幾格，寫啱咗嘅表都會數成要轉，所以每次轉都有 `table-orientation-turned` 疑點叫人對圖核。合計唔做證據：合計只講紀錄沿邊條軸，唔講邊條軸係印出嚟嘅一行（合計印喺每欄腳、定係自己印成一欄，對得上嘅軸唔同），用嚟核會攔住啱嘅轉法、又會擔保錯嘅轉法，而 bench 規則本身係機械規則，唔睇內容。引擎 A 嘅格仔唔做第二個證據：直排頁佢有時係物理格網、有時係閱讀次序，同引擎 B 嘅分行一樣兩頭都得。單行／單列格仔唔問模型，保留未定，由跨頁接表按後頁欄位處理；`as-printed` 保護物理格網，唔免除格內文字核圖。完整失效分類同驗證方法見 `content-fidelity.md`。

`finish_book.py` 預設完整保留原頁文字與結構：

- **唔裁切／刪除圖版轉寫文字**（只有顯式加 `--extract-figures` 先會切圖並以圖片連結取代圖版內轉寫）；
- **保留原印註號數值**（例如印 `②④⓪` 就寫成 `[^240]`；只有顯式加 `--renumber-footnotes` 先會全書重編為 `[^1], [^2], ...`）。標號全書唯一：同一個原印註號再出現（例如每頁由 ① 重新起，或者同一版有兩條 ①），後出嗰條改用 `[^1-2]`、`[^1-3]`……（原號、連字號、第幾次用），引用同定義用同一個 ID。逐頁配對：同號嘅註號同註數目一樣，就按閱讀次序一個對一個；配唔上嘅一半照原印留低，記入 `finish/footnotes.json` 同 `doubts.md`，唔會補造另一半。跨頁只配明顯係同一條嘅註：前頁最尾未配嘅註號，對下頁註文開頭未配嘅同號註，而且全書上一個註號正係佢減一（所以 ① 唔會跨頁配），配咗嘅列喺 `footnotes.json` 嘅 `crossPage`；
- **保留腳註定義喺頁底原位**：正文跨頁接落去（`join`）嗰陣，嗰頁嘅頁底註放喺接好嗰段之後，按原印次序；只有顯式加 `--relocate-footnotes` 先會喺移除 page marker 時將每條定義搬去引用所在 block 後面；
- **字面星號與雙圈句號**：正文同 Markdown 表格入面印出嚟嘅字面 `*` 自動跳脫為 `\*`（HTML `<table>` 格內同真實 bullet 除外），`U+16FE6` 統一寫作 `。。`。

## 輸出、續跑同核圖

B 套（本機模型校對）嘅 agent 喺對話入面唔讀頁圖、唔答 packet 題：呢節講嘅「對住頁圖轉錄」係流程經 HTTP 問模型，agent 淨係跑 script、讀 RESULT 行（見 `SKILL.md` 一）；A 套淨係喺 packet 題目問嘅位睇圖。

- 每頁底稿輸出 `page-NNNN.json`／`.txt`（`schemaVersion: 2`）；`run_hunyuanocr_second_look.py` 寫 `page-NNNN.second-look.json`／`.txt` 並保存裁圖喺 `second-look-crops/`（少於 3 頁嘅短檔自動保護窄欄正文，只排除極端邊緣單行頁碼條）。
- 完成頁要 render、模型、runtime 同影響輸出嘅請求設定都吻合，輸出 hashes 亦冇被改過，先會續用。模型指 Hunyuan 嘅 weights 同 projector（外部 endpoint 就係 model ID 同 revision），或者 Paddle 嘅 pipeline 版本同 model revision；runtime 指 Hunyuan 用嘅 `llama-server` 執行檔 hash（外部 endpoint 嘅 runtime 由服務擁有人管理，唔計），補讀亦一樣；請求設定指 prompt、採樣同 max tokens。換 llama.cpp 版本或者重新 build `llama-server` 之後，之前嘅 runtime 寫嘅底稿會報 blocked，等同一本書唔會混埋兩個 runtime 嘅底稿。Runner 同 helper code 嘅 hash、部署設定（server mode、slots、context、client workers、device）、interpreter 路徑、套件版本同 render 絕對路徑都照記入底稿，但唔用嚟判過期，所以改 code、搬書資料夾或者換 slots 都唔使重做 OCR。半套、過期或改過嘅輸出會報 blocked；先查原因，再用新輸出目錄，或者只對確定要重做嘅 `--pages` 加 `--overwrite`。唔用全書 overwrite 解決局部問題。
- Paddle worker 嘅 stderr（PaddleX log、CUDA 同爆顯存訊息）寫入 run report 隔籬嘅 `<report 檔名>.worker.log`（例如 `paddleocr-run.worker.log`），每次開 worker 重寫，run report 嘅 `workerLog` 記住路徑。載入失敗、worker 中途斷咗或者有頁 failed，runner 喺 stderr 印埋 log 最後 20 行（每行前面加 `|`），先睇呢幾行再決定點處理。
- 舊版 runner 寫嘅底稿冇 `draftIdentity`，亦可能冇 raw 回應。只要 render 吻合，而記錄落嘅 profile 同而家嘅 prompt 同採樣一致，就照續用（runtime 換咗都照收），run report 逐頁標 `legacy`。舊嘅補讀紀錄冇 `profileSha256`，只要讀嘅係同一份引擎 A 底稿、門檻一樣，亦照收。
- Hunyuan 回應唔係以 `stop` 結束（例如到咗 max tokens）時，已讀出嘅文字照存做底稿，JSON 記 `finishReason` 同 `truncated: true`，run report 逐頁標示並計數。呢啲頁唔使人手補：`proofread_pages.py` 會將引擎 B 開始重複之前讀到嘅字當第二份讀法合併，重複嗰段唔合併；截完剩唔夠十個字就對住頁圖轉錄，引擎 A 底稿做提示（兩種都有 `engine-b-truncated` 疑點）。要重新 OCR 就只對嗰幾頁加大 `--max-tokens` 再加 `--overwrite`；重新 OCR 過、唔再截斷嘅底稿會令嗰頁嘅封存過期，要對嗰頁加 `--overwrite` 重新校對。回應格式錯誤仍然算失敗頁。
- Packet 模式（`--backend packet`）以 `answers.jsonl` 保存不可改寫嘅答案紀錄，鎖內原子更新；已回答嘅 `request_id` 重播唔再呼叫模型，同一 ID 嘅答案或用量衝突會拒絕。未回答項目寫入固定嘅 `wave-NN/bundle-*.json` 並以 exit `10` 暫停；重播同一 wave 唔重寫清單。`apply_packet_answers.py` 驗證後先套用。模型、有效設定同 helper 身份改變就唔當成同一請求。重播唔再計費，但逐頁 usage 會計返答案記錄嘅用量，每個答案計一次；答案冇記用量就記 `null`，唔當成零。一個 bundle 最多 `--max-items-per-bundle`（預設 20）條題目，題目多就多開幾個 bundle，唔會將幾百張頁圖塞落同一個 bundle。`apply_packet_answers.py --answered-by MODEL` 逐條記低邊個模型答（答案本身寫咗 `answered_by` 就用佢嘅），run report 嘅 `packetAnsweredBy` 按模型計數，用嚟核對全書係咪同一個模型答。已經答咗嘅題要改答案，就用 `--amend`（可以加 `--reason`）：新紀錄寫明取代邊個答案嘅 SHA-256，舊紀錄照留喺 `answers.jsonl` 做審計；已經封好嘅頁要對輸出列出嘅頁加 `--overwrite --pages` 重做，先會用新答案。冇 `--amend` 嘅衝突答案照樣拒絕。
- `proofread_pages.py` 對標點、結構同表格寫回執行字元及格位守門。答案被拒就重試一次，再唔得就用退返嘅版本，嗰頁照封，記疑點（`formatter-rejected`、`structure-rejected`、`table-rebuild-refused` 等，見 `long-book-workflow.md`〈疑點（doubts.md）〉）；被拒嘅候選同原因留喺 seal。開咗 `--content-fidelity on`，清點或者覆核定唔到亦係疑點（`content-fidelity-unresolved`），唔當失敗頁；見 `content-fidelity.md`。
- `proofread_pages.py` 續跑時，舊版技能封好嘅頁照收，唔重新校對：封頁嗰陣仲未有嘅規則 key（`variantPairs`、`agreedMarks`、`unreadTableCharacters` 同而家長開嘅規則）缺咗都當已完成，run report 逐頁標 `legacy`、寫明缺邊啲 key，並計入 `counts.legacy`。有記錄嘅 key 值唔同（例如 profile、variant 清單或者開關改咗）、render／底稿改咗或者封條對唔上，就仍然報 blocked。
- 非零 exit 唔當完成：exit `75` 代表時間預算到期（`deferred`），exit `10` 代表等待 packet 答案（`PacketPending`），兩者都可以用同一命令安全續跑；`proofread_pages.py` exit `11` 代表等緊 packet 答案之餘亦有頁 failed 或者 blocked：照 exit `10` 咁答嗰個 wave，再按 run report 嘅 `pages` 處理嗰啲頁；exit `1` 係有頁 failed／blocked 而冇題目等緊。其他錯誤先處理原因；失敗頁按 run report 寫嘅原因處理（B 套淨係睇 RESULT 行、話畀用戶知），唔靜靜跳過：`wait_for_run.py` 嗰行列出 failed／blocked 頁同 report `pages` 逐頁嘅原因（`[failed 3, 7: 原因]`），B 套唔使亦唔准開 report。
- Hunyuan 嘅 `whole-page` bbox 只代表整頁，confidence 0 唔係逐字低信心；唔用佢估字嘅位置。Paddle 嘅 bbox 係左上原點 normalized xywh，confidence 係 layout detector 分數。Apple Vision 嘅座標方向唔同，唔混用。
- 引擎二只讀部分頁時，將其 scan coverage 分開記；缺少第二底稿唔係空白讀法，亦唔係一致票。比較時唔正規化走異體字、來源括號同註號。
- 封存頁記低嘅校對 transport、model 名、endpoint 同 script／helper hash 淨係記錄，唔判過期；舊版技能封好嘅頁照收（`legacy`），見 `long-book-workflow.md`〈封條同續跑〉。Draft hashes 同 sealed page hashes 證明輸出同輸入／設定嘅關係，唔單獨證明語義無誤。

## 等背景步驟跑完（`wait_for_run.py`）

長步驟放背景跑、輸出寫去自己嘅 log（`> LOG 2>&1`，每次開一個新 log）嗰陣，唔好一輪又一輪讀 log 或者已封存嘅頁，改用：

```bash
python3 scripts/wait_for_run.py OUTPUT LOG [--progress BOOK]
```

`OUTPUT` 係嗰個步驟寫出嚟嘅嘢：`proofread_pages.py` 嘅輸出目錄；`book_profile.py` 嘅 profile；OCR runner（`run_hunyuanocr.py`、`run_paddleocr_vl.py`、`run_apple_vision_ocr.py`，直接跑或者經 `run_ocr_phase.py`）嘅輸出目錄；`run_hunyuanocr_second_look.py` 就係佢重寫嘅引擎 B 目錄，只計今次重寫咗嘅頁；`ocr_book.py`（一條命令跑晒成個 OCR 階段）嘅書目錄；`stitch_tables.py` 嘅 `--output-dir`（冇就係佢改寫嘅目錄）；`repair_book.py` 嘅 `--output`；`apply_packet_answers.py` 嘅 packets 目錄；`finish_book.py` 嘅書目錄。一次最多等 `--timeout` 秒（預設 570，低過 Qwen Code 一次 shell 600 秒嘅上限），唔問模型、唔開任何連線，最後只印一行 `[wait] RESULT exit=N ...`：

- exit `0`：做完，冇頁 failed、blocked，亦冇題目等緊 packet 答案。嗰行講有 `WARNING` 就先 `grep -n WARNING LOG` 睇晒，再做下一步。
- exit `1`：有頁 failed 或者 blocked、有 ERROR 或 traceback、服務冇還原，或者步驟根本開唔到工（參數錯、搵唔到 script 或命令）。有頁 failed／blocked，嗰行就列出頁同 run report `pages` 寫嘅原因（`; pages failed or blocked: [failed 3, 7: 原因] …`，同一個原因嘅頁擺埋一齊）。A 套讀嗰行，要再查先睇 run report 同 log 尾；B 套淨係讀嗰行，將頁碼同原因話畀用戶知，唔開 report、唔讀 log。唔好就咁重跑。
- exit `2`：判斷唔到：`wait_for_run.py` 自己嘅參數錯；冇程序跑緊又搵唔到 LOG（路徑錯）；或者 log 寫咗 report 路徑，但冇摘要行、report 又讀唔到。檢查兩個路徑同 log 尾，唔好因為呢個重跑步驟。
- exit `4`：未做完就停咗（被殺、中斷或者死咗）、OCR 用完時間預算，或者 GPU 忙（`run_ocr_phase.py` exit `76`：通常乜都冇停、冇跑；窗口開咗但一頁都冇封到嘅 76，模型停過又開返，wait_for_run 會照講）。重跑同一條命令，已封存嘅頁照留；`repair_book.py` 就要加 `--resume`。
- exit `10`：等緊 packet 答案。嗰行寫明 wave 目錄同 `apply_packet_answers.py` 命令：答晒 wave 入面每個 `bundle-*.json`，寫入佢指定嘅 answers 檔，跑嗰條命令，再重跑同一條步驟命令。
- exit `11`：等緊 packet 答案，同時有頁 failed 或者 blocked（`proofread_pages.py` exit `11`）：A 套照 exit `10` 咁答嗰個 wave，再按嗰行列嘅頁同原因（run report 嘅 `pages`）處理嗰啲頁；淨係重跑修唔好佢哋。B 套唔答（見 `SKILL.md` 五）。
- exit `75`：等到時限步驟仲跑緊：即刻再叫一次 `wait_for_run.py`。

做完未，睇步驟自己印嘅摘要行（`requested N | completed N | pending N | ...`；一次 run 印幾多行就計晒幾多行，failed、blocked、pending 加埋計）；摘要行冇嘅數，或者成個 run 冇摘要行，就睇 run report 嘅 `counts` 同 `exitStatus`。見到 `[packets] PENDING`、pending 數唔係 0，或者 report 嘅 `exitStatus` 係 `10`／`11`，一定報 PENDING，唔會報做完。

Linux 用 `/proc` 搵跑緊嘅程序；冇 `/proc`（macOS）就用 `ps` 讀命令行、狀態同開始時間（準到秒），用 `lsof` 讀工作目錄同輸出去向。`ps` 將成條命令行當一串字，有空格嘅參數會被拆開，呢類 run 靠佢寫緊嘅 log 認返；冇 `lsof` 就讀唔到工作目錄同輸出去向，用相對路徑開嘅 run 都係淨靠 log 認。

等 `ocr_book.py`：佢寫好草稿就印 `[ocr-book] DRAFT 路徑`；之後每一行（跑緊、做完或者停咗）最尾都加 `; DRAFT 路徑`，引擎 A 仲跑緊都見到。

`--progress BOOK`：等第 3 階段（`proofread_pages.py`）期間每 `--progress-every` 秒（預設 300）重建一次 `BOOK/proofreading.md`（已封存嘅頁用校對稿，其餘用底稿），返回之前再建一次，嗰行最尾寫埋檔案同校對咗幾多頁；建唔到就喺嗰行講，exit code 照跟步驟。

上游部署資料：[HunyuanOCR 模型卡](https://huggingface.co/tencent/HunyuanOCR)、[PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)。
