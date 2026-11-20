# 叢集大批 OCR：H200 Slurm 嘅配方

喺 H200 叢集（Slurm、Ubuntu 22.04、NVIDIA driver 570、冇 root）用呢個技能大批 OCR 嘅做法，2026-11-19 喺 M1 叢集逐件量度。數字係每張 H200 或者每個 8 卡節點。各部分分開量過；成條流程頭尾嘅吞吐未量度，量完補返落〈未量度同已知問題〉。

## 配方

| 節點 | GPU | 做乜 |
|---|---|---|
| OCR 節點（1 個） | 0 | SGLang serve PaddleOCR-VL-1.6（固定版本權重），`--mem-fraction-static 0.6` |
| | 1–3 | 三條 OCR 線：每條一張卡，HunyuanOCR（llama.cpp，16 slots）同 PaddleOCR-VL 嘅版面偵測；VL 識別交 GPU 0 |
| | 4–7 | 兩個校對模型 replica（2 × TP2） |
| 校對節點（其餘） | 0–7 | 四個校對模型 replica（4 × TP2） |

校對模型係 Qwen3.8-Flash-Next-FP8，SGLang 0.5.23，每個 replica TP2 EP2 加 MTP（NEXTN 3/4）、FP8 KV cache、`--max-running-requests 104`。六個節點即係一個 OCR 節點加五個校對節點，共 22 個 replica。

## 點解咁分

- 校對每頁用嘅 GPU 時間大約係 OCR 嘅 13 倍：OCR 兩個引擎合計每張卡每粒鐘約 1,280 頁（每頁約 2.8 GPU 秒），校對每張卡每粒鐘約 98 頁（每頁約 37 GPU 秒）。所以 OCR 淨係要幾張卡，其餘全部校對。
- 一張卡嘅 PaddleOCR-VL 伺服器頂得住約七張卡嘅 HunyuanOCR；六個節點嘅校對大約每粒鐘 4,300 頁，三條 Hunyuan 線（每條約 1,400 至 1,600 頁）啱啱夠。

## 兩個 OCR 引擎

- **HunyuanOCR**（引擎 B）：llama.cpp b10969。預編嘅 Ubuntu CUDA 二進制要 glibc 2.38，Ubuntu 22.04 行唔到，安裝器會自動用源碼建置同一個 commit（要 `nvcc`、`cmake`、C++ 編譯器）。一張卡 4／8／16／32 slots：每粒鐘 1,409／1,512／1,612／1,587 頁；16 slots 已經 89% 忙，顯存 28 GB。`OCR_HUNYUAN_SLOTS=16` 要明寫：安裝器試跑失敗會將佢改做 1。
- **PaddleOCR-VL**（引擎 A）：H200 上 native 生成卡死（Paddle 3.3.1 CUDA 12.6 wheel、driver 570：一個 CPU 核 100%、GPU 0%，一頁讀唔完）。改用 `--vl-backend sglang-server`（見 `local-ocr.md`〈PaddleOCR-VL 交外部 VL 伺服器（sglang-server／vllm-server）〉）：版面偵測喺本線張卡，VL 識別交 SGLang。一個伺服器 1／4／8／16 個版面 process：每粒鐘 2,258／6,476／9,201／11,341 頁。
- 冇 root 裝 Poppler：`apt-get download poppler-utils libpoppler118 libopenjp2-7`，`dpkg -x` 解落共用盤，再用一個設定 `LD_LIBRARY_PATH` 嘅 wrapper 擺入 `PATH`（唔好全域設，會影響 Paddle 嘅 CUDA 函式庫）。

## 校對模型

一個節點唔同排法，同時 16／64／192／384 個請求，每秒生成 token：

| 排法 | 16 | 64 | 192 | 384 |
|---|---|---|---|---|
| 4 × TP2 + MTP | 2,605 | 6,256 | 10,737 | 12,819 |
| 4 × TP2 | 1,652 | 4,222 | 6,777 | 6,622 |
| 2 × TP4 + MTP | 2,425 | 5,633 | 顯存爆 | 顯存爆 |
| 2 × TP4 | 1,603 | 3,950 | 5,950 | 5,119 |

1 × TP8 + MTP 一樣顯存爆（`--mem-fraction-static 0.85`）。28 個 bench 案平均每頁約 59,000 個輸出 token、約 20 個請求，即係每個節點每粒鐘約 780 頁；每個校對節點要保持約 384 個請求喺度做先食得滿。

## 點跑

- `ocr_batch.py` 用 `--backend http` 嗰陣一次淨校一本，仲要等晒全部書 OCR 完先開始：佢假設部機嘅本機模型要讓 GPU 畀 OCR。叢集上校對模型喺第二啲節點，所以要自己將每本書嘅單本 command（SKILL.md 四）並排咁行：render、`ocr_book.py`、`book_profile.py`、`proofread_pages.py --backend http`、`finish_book.py`。一本書 OCR 完即刻校對，OCR 線同時讀下一本。
- 每條 OCR 線：`CUDA_VISIBLE_DEVICES` 係佢張卡，自己一個 `XDG_STATE_HOME`（技能嘅 GPU 隊同 Hunyuan broker 放喺本機盤），等幾條線唔會互相排隊；`OCR_PADDLE_DEVICE=gpu:0`、`OCR_PADDLE_WORKERS=8`，連埋 `OCR_PADDLE_VL_*` 指去伺服器。
- 每條線同時兩本書，再設 `OCR_PADDLE_GPU_RESOURCE`（例如 `paddle-gpu`）：一本行緊 Paddle，另一本嘅 Hunyuan 照讀，唔使排同一條隊。14 本 4,420 頁實測，一個節點（7 條線）每粒鐘 7,124 頁；一條線一本書、共用一條隊嗰陣約 6,300 至 6,700 頁。
- 設定同環境（`XDG_CONFIG_HOME`、`XDG_DATA_HOME`）放共用盤，所有節點用同一份；本書目錄放節點本機盤：成批書嘅 render 好大，共用盤嘅配額裝唔落，交付完先抄定稿出去。
- 校對經一個 router 分畀所有 replica（揀手頭最少請求嗰個），每本書 `--max-inflight` 開大啲，令同時做嘅書加埋夠請求。

## 未量度同已知問題

- 成條流程頭尾嘅吞吐：未量度（量緊：24 本藏書，6,478 頁）。
- 一個新開或者重排嘅 replica，第一批請求要編譯 kernel，大約慢 40 秒，之後正常。
- 2 × TP4、1 × TP8 加 MTP 喺高並行顯存爆；要用就要調低 `--max-running-requests` 或者 `--mem-fraction-static`，未試。
