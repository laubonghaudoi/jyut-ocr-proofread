# 叢集大批 OCR：H200 Slurm 嘅配方

幾百至過千本書唔好一頁頁排本機 GPU 隊，拆成 **OCR 節點 + 校對模型 Fleet** 兩層跑；配套腳本同 Slurm 範本已經整齊放入技能目錄 `cluster/`（全部經環境變數同 CLI 參數設定，唔寫死叢集路徑、帳號或書號）。

## 配方

- **兩張 8×H200 節點**起跑（或者多節點 `cluster/slurm/fleet.sbatch` 供幾百頁同時校對）：
  - **OCR 節點**（`cluster/slurm/ocr-node.sbatch` + `cluster/slurm/vl-server.sh`）：每張 H200 開 1 個 `sglang serve`（PaddleOCR-VL-1.6，**必帶 `--chat-template runtime/paddleocr-vl-server-chat-template.jinja`**）+ 1 個 `run_hunyuanocr.py` shared broker。兩者同卡共駐顯存安全（合計約 ~50 GB / 141 GB），GPU-hours 計入「OCR 層」。
  - **校對 Fleet**（`cluster/slurm/fleet.sbatch` + `cluster/slurm/fleet-node.sh`）：多張 H200 分卡啟動 SGLang serve 校對視覺語言模型，將 `http://host:port` 寫入共享 `BACKENDS_FILE`。
  - **Central Router**（`cluster/fleet_router.py`）：單一 async 反向代理接晒所有 `proofread_pages.py`／`book_profile.py` 請求，按 least-inflight 分派、定期探測 `/v1/models`、回應未傳出前遇上游斷線自動重試另一台，並喺 `/status` 吐 JSON 健康指標。
- **實測總規模（2026-11，1,433 冊走完整條大批管線／519,178 頁，連前期測試共 1,442 冊交付）**：
  - 總牆鐘時間：**72.06 小時**；
  - 總算力：**7,607.9 H200 GPU-hours**（`ocr` 節點 **1,188.4** GPU-h + 校對 Fleet **6,419.5** GPU-h）＋ **3,340.4 CPU core-hours**。

## 點解咁分

- `ocr_book.py` 逐頁讀，GPU 同 I/O 節奏固定，每卡專責讀書唔會同 128 個並發校對線程爭 KV cache；
- `book_profile.py` 同 `proofread_pages.py` 淨係發無狀態 HTTP 請求，將多機 Fleet 收斂成一條 `--endpoint` URL 之後，**CPU 節點／校對專機**（`cluster/slurm/proof-node.sbatch`，配 `--ocr-gpus ""`）都可以純用 HTTP 吃盡成個 Fleet 算力；
- 校對 bottleneck 喺 reasoning tokens 而唔係 OCR 速度：實測校對消耗佔總 GPU-hours 約 **84.4%**（6,419.5 / 7,607.9），將 GPU 分流給 Fleet 先最慳時間。

## 兩個 OCR 引擎

- **Engine A（PaddleOCR-VL-1.6 via SGLang `--vl-backend sglang-server`）**：版面偵測留喺本機 GPU/CPU，block 識別經 `--chat-template runtime/paddleocr-vl-server-chat-template.jinja` 送 SGLang；2026-11 大批實測單節點每鐘可處理上萬頁。
- **Engine B（HunyuanOCR via `llama-server` shared broker）**：每卡 1 個 `llama-server`，預設開 CUDA graphs；遇到混頁大小引致 CUDA graph 死鎖或中止，內建 stall watchdog（`OCR_HUNYUAN_STALL_TIMEOUT`，預設 600 秒）自動砍掉重開並加 `GGML_CUDA_DISABLE_GRAPHS=1`。
- **可選單頁容錯**：`ocr_book.py --max-missing-engine-b 1` 連 `run_hunyuanocr_second_look.py --allow-missing-engine-b`，個別頁（1,433 冊入面得 7 冊出現過單頁缺引擎 B）唔再卡死成冊，轉入單引擎 fallback 並記 `engine-draft-missing` 疑點；預設仍係 `0`（嚴格全頁齊全）。

## 校對模型

喺 1,377 冊最新 `proofread-run.json` 入面 **255,023 頁**嘅實測統計（`--loop-detector` 長開）：

- **總呼叫同 tokens**：`3,302,525` 次（均 **12.95 次／頁**）；總輸出 `9,222,723,445` completion tokens（均 **36,164 tokens／頁**，當中 reasoning **34,583 tokens／頁**）。
- **逐冊分佈（>=20 頁，`n=889` 冊）**：`p10=13,343`、`p25=18,627`、**中位數 `p50=27,822`**、`p75=44,613`、`p90=66,505`、`p99=157,165` completion tokens／頁。
- **按題型佔比**：`punctuate` **32.5%**（均 11,739 tok/頁，4.04 次/頁）、`adjudicate` **25.0%**（9,052 tok/頁，1.97 次/頁）、`census` **19.5%**（7,068 tok/頁，4.78 次/頁）、`punctuate-retry` **14.7%**（5,309 tok/頁，0.84 次/頁）、`format` **5.5%**、`format-retry` **1.7%**、其餘（`coverage-read`、`cell-direction`、`note-numbers`）合共 **1.1%**。
- **Loop detector 實效**：成批觸發 **`100,891` 次 `loopCuts`**，直接慳返 **`1,432,949,679` reasoning tokens**。

## 點跑

詳見 `cluster/README.md` 同 `cluster/` 各腳本 `--help`：

1. **準備環境**：設定 `JYUT_SKILL_DIR`、`PADDLE_VL_MODEL_DIR`、`PROOFREAD_MODEL_DIR`、`JYUT_WORK_DIR`、`JYUT_OUT_DIR`（參考 `cluster/README.md` 環境變數表）。
2. **啟動校對 Fleet**：提交 `sbatch cluster/slurm/fleet.sbatch`，等 `fleet_router.py` 寫出健康後端清單及 `/status` JSON。
3. **啟動 OCR／校對 Worker**：
   - `sbatch cluster/slurm/ocr-node.sbatch` 跑 `cluster/mass_ocr.py`，預設 `--dpi auto`（#11 按實際掃描 PPI 同每頁 40M 像素上限自動選定 `pdftoppm -r`）、`--order alternate`（大細書交錯排隊，避免長書擠到尾段）、`--max-missing-engine-b 1`、`--gpu-max-consecutive-failures 3`（死卡自動熔斷）、每書獨立 `fcntl.flock`（`.book.lock`）防撞書；
   - Slurm wall-time 快到前，用 `cluster/cutover.sh`（經 `cluster/tcp_pipe.py` 兩輪串流同步＋Pass-2 驗證）無縫遷移去 `cluster/slurm/proof-node.sbatch`；`--ocr-gpus ""` 強制鎖住唔洗走已有 `renders/` 同 `ocr-*` 底稿。
4. **即時監控同體檢**：
   - `python3 cluster/monitor.py OUT_DIR... --fleet-status-url http://ROUTER:8081/status --interval 30`；
   - `python3 cluster/mass_health.py OUT_DIR... --work-dir WORK_DIR --stale-minutes 45`。

## 未量度同已知問題

以下 2026-11 大批跑數暴露過嘅故障模式，現已喺技能同 `cluster/` 內修復並有離線單元測試：

- **`HunyuanServer` CUDA graph 死鎖**：`llama-server` `/v1/models` 仲回 200 但推理完全卡死，現由 `scripts/run_hunyuanocr.py` 內建 `OCR_HUNYUAN_STALL_TIMEOUT` watchdog 主動偵測並以 `GGML_CUDA_DISABLE_GRAPHS=1` 重啟；
- **`paddle-python` cold-start 探測超時**：NFS 上 `verify_paddleocr_available()` 逾時已放寬至 180 秒、自動注入 `PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True`，並加入 per-venv 原子 JSON 快取；
- **大開本低 PPI 掃描檔撐爆宿主機 RAM**：`survey_pdf.py --dpi auto` 避免將低 PPI 大圖無謂放大成過億像素；`proofread_pages.py` 亦改用滑動視窗提交、crop-first 灰階、大圖並發解碼訊號量、縮圖快取同串流前釋放 JSON buffer；
- **多模態 placeholder 同 PaddleX 合成 `<img>` 漏網**：`Client._request` 中和 `<image>`／`<|vision_start|>` 等 token 防 HTTP 400；worker、proofread 同 `audit_ocr_markdown.py` 剝走並審計 `<img src="imgs/img_in_...">`；
- **分頁邊界修書契約衝突**：`repair_book.py` `forms_stage` 已經過 `rn.edge_safe(...)`；
- **個別卡／單頁異常**：`cluster/mass_ocr.py` 加入 GPU 連續失敗熔斷同 `--max-missing-engine-b` 單頁容錯（唯容錯退回單引擎校對嘅品質仍不如雙引擎，疑點留待人工覆核）。

仲未系統量度嘅：超過 4 節點以上 Fleet 嘅路由擴展極限、不同書目規模下最慳成本嘅 OCR：Fleet 卡數動態配比，以及跨叢集排程策略。
