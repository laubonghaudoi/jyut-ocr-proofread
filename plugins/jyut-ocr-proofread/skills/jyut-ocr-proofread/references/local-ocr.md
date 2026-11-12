# Ubuntu／Linux 本機 OCR 底稿

預設用 **HunyuanOCR** 頂替 macOS Apple Vision 嘅底稿步驟；**PaddleOCR-VL 按需要補讀**。呢兩個 runner 移植自 `jyut-ocr-qwen` 嘅 OCR 層，輸出仍只係 draft。後續由本技能嘅 agent 逐頁睇原圖、轉錄、重建段落／註腳／表格，同執行所選 profile 嘅覆核。唔需要啟動 Qwen 校對模型。

**校對模型跟隨調用技能嘅 session**：Codex 使用當前 Codex agent 模型，Claude Code 使用當前 Claude agent 模型；本頁嘅 `--model`／模型路徑全部只配置 OCR 底稿 backend。Bundled runners 可以獨立使用，唔依賴 `jyut-ocr-qwen` checkout、指定 GPU 型號或開發者嘅個人路徑。其他電腦按實際硬件／runtime 配置底稿引擎，智能校對責任維持不變。

## 揀引擎同補讀範圍

- 普通正文先跑 Hunyuan；agent 一樣要核對所有 in-scope scan，唔只睇模型標成可疑嘅位置。
- 需要第二讀法、版面區塊座標，或者見到多欄次序、表格、淡字、漏行／重複、註號、人名／異體字疑點，先用 Paddle 補讀有關完整 scan。前後頁有跨頁結構時一併核圖。
- 初步核圖已見到問題遍及全書，或者用戶要求雙底稿時，可以兩個引擎讀全書；Hunyuan 共用批次完成並釋放 GPU 後，Paddle 再按 GPU 隊列接棒。
- 兩引擎分歧只係核圖線索；一致亦可能一齊錯。唔自動投票、唔按多數字形全域替換，唔由 OCR 排除任何頁嘅 visual review。
- 引擎缺失、GPU 暫時不可用或單頁失敗時，講明原因並繼續按原圖轉錄；唔反覆等待服務，唔自動安裝模型，亦唔 fallback 去 Tesseract。

## 模型同 GPU

喺 skill 目錄執行 runner。Hunyuan 用現有相容 `llama-server`、GGUF 模型同 mmproj；Paddle 用裝好 GPU runtime／`paddleocr` 嘅獨立 Python 環境，worker 要支援 `PaddleOCRVL(pipeline_version="v1.6")`。權重唔包喺 skill，亦唔因為上游模型卡更新就自動換版本。

Paddle loader 可能自行下載未快取嘅模型；執行前先確認所選 pipeline 嘅權重已喺本機，未確認就先按原圖校對。現有安裝用另一個相容 pipeline 時，明確傳 `--pipeline-version`。更換快取權重後更新 `--model-revision`／`PADDLEOCR_MODEL_REVISION`，令舊底稿唔會被錯當成同一模型嘅 current output；呢個 revision 只係自訂標籤，唔等於權重 hash。

可以沿用已核實嘅 `~/.config/jyut-ocr/env.sh` 路徑同並行設定，或者傳 CLI 參數；唔覆寫整份機器設定。已有呢份設定時，各 session 啟動前都載入同一份，連 `OCR_HUNYUAN_MODE`／`OCR_HUNYUAN_SLOTS` 一併沿用，唔只抄模型路徑而另揀 slots，否則會分成不同批次。唔好將 venv Python symlink resolve 成系統 Python。

| 設定 | CLI | 用途 |
|---|---|---|
| `HUNYUAN_MODEL` | `--model` | Hunyuan GGUF |
| `HUNYUAN_MMPROJ` | `--mmproj` | 同版本視覺 projector |
| `HUNYUAN_LLAMA_SERVER` | `--llama-server` | 支援該模型嘅 llama.cpp server |
| `PADDLEOCR_PYTHON` | `--python` | Paddle venv 嘅 Python |
| `OCR_HUNYUAN_MODE` | `--server-mode` | 預設 `shared`；`dedicated` 獨佔批次 |
| `OCR_HUNYUAN_SLOTS` | `--server-slots` | 所有 Hunyuan clients 合共嘅並行上限 |

執行前用 `nvidia-smi` 睇空閒顯存同 GPU processes，確認有冇其他書或共享服務用緊張卡。唔以 GPU utilization 暫時為 0 當成可以停服務；已有工作繼續用緊就先做原圖校對。只有本次已有 GPU 交接授權、確認無其他使用者同有復原方法時，先按機器嘅管理方式釋放佔用。呢個 skill 唔會自動停／重啟 NInfer，亦唔調用 qwen pipeline 嘅 GPU 交接 wrapper。

由各 session 嘅主 agent 啟動 bundled runner，runner 自動加入同一個本機 Hunyuan 服務。GPU 上只載入一份 Hunyuan 模型；服務按 session 輪流分派頁面，保持有限並行。Paddle 仍然按 FIFO 取得獨佔 GPU 位。LLM 子 agent 唔自行啟停 OCR 模型或共享推理服務。

## 跨 session 共用 Hunyuan 同 GPU 排隊

- Hunyuan 預設 `--server-mode shared`。同一電腦、同一 OS 使用者、相同 queue／resource、模型與 runtime／profile 身份嘅 sessions，自動連同一個由技能管理嘅 broker；唔需要手動開 server。Codex、Claude、Gemini 等本機 agent 只要實際載入呢份技能並調用同一版本 runner，就用同一個機制；唔代表各平台已自動發現技能。
- 每個 CLI 所選 scan 批次係一個 client。每個 session 有獨立 render／output／report；唔同 writer 唔共用輸出檔。全數可續用嘅批次唔啟動 broker。Client 喺 raw OCR 完成就離開，GPT／Claude／Gemini 校對期間唔持有 GPU membership。
- Broker 持有一個 GPU lease，模型只載入一次，所有 client 合共最多 `--server-slots` 個請求。每請求處理一張頁圖；16 個 sessions 唔等於每個都獨佔 16 個 GPU 位。`--workers` 控制單個 client 嘅未完成頁數，預設等於 server slots，亦唔可超過佢；每個 session 輪流獲分派，唔以一本書嘅所有頁塞滿無界隊列。
- 通用預設係 4 slots；本機按實測用 `OCR_HUNYUAN_SLOTS` 或 `--server-slots` 設定。`--ctx-size` 係**每個 slot**嘅 context，總 context 係兩者相乘；唔縮細每請求容量換取並行。並行數唔係顯存自動偵測，其他電腦要按實際容量／吞吐選值。Client worker 數可以唔同；模型、context、server slots 或 request profile 唔同就分成不同服務，依 FIFO 順序用 GPU。
- 關閉／取消一個 client，只移除佢嘅待處理頁同連線，其他 session 繼續。已送入 backend 嘅請求可能跑到完成或 timeout。最後一個 client 離開後，約 0.5 秒無新 client 就關閉自建 server，等模型程序退出後交棒；唔等 agent 校對全書。Broker 獨立於第一個 client，唔會因第一個 session 被關閉而停晒其他書。
- Broker／backend 異常退出時，未完成頁唔封成成功，runner 報失敗供續跑；唔無限重試錯誤。Linux backend 經獨立 launcher 安裝 parent-death signal，並繼承 GPU lease descriptor；broker 突然消失時，未退出嘅 backend 仍會阻止下一份模型提早載入。Broker 唔係無人值守嘅全書工作管理器：client 結束後未提交嘅頁，要由 agent 續跑。
- Paddle 或另一個設定嘅 backend 已排隊時，目前 Hunyuan broker 停止接受新 client，完成現有 clients 嘅批次後釋放 GPU；新到者等下一輪。長書想更早交棒可以用 page selectors 切批。`--server-mode dedicated --server-slots 1 --workers 1` 可用舊有逐批獨佔模式，同樣參與 FIFO；外部 `--endpoint` 仍由其擁有人管理，唔加入／卸載呢個共用服務。
- GPU 隊列預設 `${XDG_STATE_HOME:-$HOME/.local/state}/jyut-ocr/gpu-queue`、resource `local-gpu`。可用 `OCR_GPU_QUEUE_DIR`／`--gpu-queue-dir` 同 `OCR_GPU_RESOURCE`／`--gpu-resource` 改。只有不同實體 GPU 先分 resource；唔以書名、session、port 或安裝路徑分隊列。Resource 唔會自行揀 GPU，裝置配置要一致。通訊 socket 同服務 logs 喺同使用者私有嘅 `/tmp/jyut-ocr-UID/`，run report 記 broker／server PID、設定身份同 log 位置。
- 預設一直等 GPU；`--gpu-queue-timeout SECONDS` 同 `OCR_PHASE_DEADLINE` 可限制等候／啟動。Shared 模式 `--gpu-queue-timeout 0` 只加入已 ready 嘅服務，唔冷啟動；到期未處理頁記 `deferred`，冇其他 failed／blocked 時 exit 75，可以同一命令續跑。唔反覆開新 runner 代替等候。
- 呢個機制只協調同一設定嘅本技能 runners；不同 OS 使用者、容器、NInfer、舊版 scripts 同其他程式唔會自動加入。唔因排到位就停別人服務或假設顯存足夠。有工作運行時唔刪 queue、socket 或 lock 檔。

## 並行設定同核圖

本機實驗已確認單 server 多請求可行；樣本／排程大小會影響收益，八頁短測同持續 32 頁測試可以有不同結論。唔因顯存有餘就開多份模型，亦唔將某部 5090 嘅設定寫死成通用預設。

比較時固定 render hashes、模型、有效 context／輸出上限、prompt 同採樣設定，分開模型啟動、warmup、OCR 同 agent 校對時間；量度實際活躍請求、吞吐、顯存、失敗／截斷同逐字差異。並行同重跑都可能改變輸出，即使 temperature 0／固定 seed 亦唔保證 byte-identical。兩版均要返原圖判斷，唔以正常完成或 hashes 相同當成來源內容正確。原有漏標點、表單次序等問題仍由當前 agent 逐頁核圖。

## 命令

`RENDER_DIR` 入面用 `page-0001.png`、`page-0002.png` 命名，數字係 PDF scan page，唔係 printed page。`survey_pdf.py` 支援由來源 PDF 切圖，亦支援 `--images-dir` 為已有 `page-NNNN.png` 目錄建立 `source_kind: "images-dir"` 嘅 `page-manifest.json`（唔改原圖）。輸出放工作目錄，唔覆蓋原圖、annotated segment 或最終 Markdown。

以下假設路徑已設好；如採用現有設定檔，先讀過確認用途，再於同一個 shell call 載入：

```bash
. "$HOME/.config/jyut-ocr/env.sh"

# 1. Render PDF（或對現成圖片資料夾用 --images-dir）並建立 page-manifest.json
python3 scripts/survey_pdf.py /path/to/book.pdf BOOK
# 無 PDF、只有現成 page-NNNN.png 時：
# python3 scripts/survey_pdf.py BOOK --images-dir /path/to/images

# 2. 雙引擎底稿 + 局部補讀（Hunyuan 共用 SharedHunyuanClient + GPUQueue）
python3 scripts/run_paddleocr_vl.py BOOK/renders BOOK/ocr-paddle \
  --report BOOK/reports/paddleocr-run.json
python3 scripts/run_hunyuanocr.py BOOK/renders BOOK/ocr-hunyuan \
  --report BOOK/reports/hunyuanocr-run.json
python3 scripts/run_hunyuanocr_second_look.py BOOK/renders \
  BOOK/ocr-paddle BOOK/ocr-hunyuan \
  --report BOOK/reports/second-look-run.json

# 3. Book profile 與逐頁校對（預設 --backend packet 三階段 checkpoint-replay）
python3 scripts/book_profile.py BOOK/renders BOOK/ocr-paddle \
  BOOK/ocr-hunyuan BOOK/book-profile.json \
  --packets-dir BOOK/packets/profile
# 若 exit 10（PacketPending）：由當前 session 嘅 Agent/Subagents 讀 wave-NN/bundle-*.json 睇圖作答，
# 經 apply_packet_answers.py 寫入 answers.jsonl，再重跑同一條命令直至 exit 0（<=4 頁短檔自動走免校準快速路徑）。

python3 scripts/proofread_pages.py BOOK/renders BOOK/ocr-paddle BOOK/proofread \
  --draft2-directory BOOK/ocr-hunyuan --profile BOOK/book-profile.json \
  --packets-dir BOOK/packets/proofread \
  --table-orientation auto
# 同樣按 Wave 1 -> Wave 2 -> Wave 3 經 apply_packet_answers.py 回填答案並重跑至 exit 0；
# 若目標評測或專案要求表格嚴格保持原印方向唔轉置，傳 --table-orientation as-printed。

# 4. 跨頁表格縫合與全書決定論修書／定稿
python3 scripts/stitch_tables.py BOOK/proofread --renders BOOK/renders \
  --drafts BOOK/ocr-paddle --drafts2 BOOK/ocr-hunyuan \
  --output-dir BOOK/proofread-stitched
python3 scripts/finish_book.py BOOK --proofread BOOK/proofread-stitched
```

無設定檔時直接提供 `--model /path/to/model.gguf --mmproj /path/to/mmproj.gguf --llama-server /path/to/llama-server`。Hunyuan（包括 `run_hunyuanocr_second_look.py`）預設自動建立／加入私有 broker，backend 使用自選 loopback port；唔掃描或接管其他 HTTP 服務。`--port` 只用於 `dedicated` 模式（預設 8095），已佔用就報錯。若本機或遠端另有相容 OpenAI VLM 服務，亦可顯式傳 `--backend http --endpoint http://127.0.0.1:PORT`。

三個 OCR runner 都接受 `--pages 3,8-10` 或 `--first-page 1 --last-page 80`；唔畀 selector 就處理目錄中所有合法 `page-NNNN.png`。Report 放喺各自 OCR output 目錄外；兩個引擎唔共用同一個 output directory。

`finish_book.py` 預設完整保留原頁文字與結構：
- **唔裁切／刪除圖版轉寫文字**（只有顯式加 `--extract-figures` 先會切圖並以圖片連結取代圖版內轉寫）；
- **保留原印註號數值**（例如印 `②④⓪` 就寫成 `[^240]`；只有顯式加 `--renumber-footnotes` 先會全書重編為 `[^1], [^2], ...`），並支援同頁局部配對與跨一頁孤立註號回溯；
- **保留腳註定義喺頁尾／段末原位**（只有顯式加 `--relocate-footnotes` 先會喺移除 page marker 時將腳註搬去引用段落後面）；
- **字面星號與雙圈句號**：正文同 Markdown 表格入面印出嚟嘅字面 `*` 自動跳脫為 `\*`（HTML `<table>` 格內同真實 bullet 除外），`U+16FE6` 統一寫作 `。。`。

## 輸出、續跑同核圖

- 每頁底稿輸出 `page-NNNN.json`／`.txt`（`schemaVersion: 2`）；`run_hunyuanocr_second_look.py` 寫 `page-NNNN.second-look.json`／`.txt` 並保存裁圖喺 `second-look-crops/`（少於 3 頁嘅短檔自動保護窄欄正文，只排除極端邊緣單行頁碼條）。
- 只續用目前 render、runner／profile 同輸出 hashes 都吻合嘅完成頁。半套、過期或改過嘅輸出會報 blocked；先查原因，再用新輸出目錄，或者只對確定要重做嘅 `--pages` 加 `--overwrite`。唔用全書 overwrite 解決局部問題。
- Packet 模式（`--backend packet`）以 `answers.jsonl` 作為 append-only 確定性快取：已回答嘅 `request_id` 重跑時零耗費即時重播；未回答項目寫入 `wave-NN/bundle-*.json` 並以 exit `10` 暫停，`apply_packet_answers.py` 驗證 JSON 格式與欄位後原子追加。`proofread_pages.py` 對每次標點、結構同表格寫回都會執行 `verify_characters` 字元集鎖定同標點計數驗證，唔通過會自動拒絕或退回安全底稿。
- 非零 exit 唔當完成：exit `75` 代表時間預算到期（`deferred`），exit `10` 代表等待 packet 答案（`PacketPending`），兩者都可以用同一命令安全續跑；其他錯誤先處理原因。失敗頁仍要按原圖處理，唔靜靜跳過。
- Hunyuan 嘅 `whole-page` bbox 只代表整頁，confidence 0 唔係逐字低信心；唔用佢估字嘅位置。Paddle 嘅 bbox 係左上原點 normalized xywh，confidence 係 layout detector 分數。Apple Vision 嘅座標方向唔同，唔混用。
- 引擎二只讀部分頁時，將其 scan coverage 分開記；缺少第二底稿唔係空白讀法，亦唔係一致票。比較時唔正規化走異體字、來源括號同註號。
- Draft hashes 同 sealed page hashes 證明輸出同輸入／設定嘅關係，唔單獨證明語義無誤；唔取代 source/render manifest、視覺核對或 `release` tool snapshot。

上游部署資料：[HunyuanOCR 模型卡](https://huggingface.co/tencent/HunyuanOCR)、[PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)。
