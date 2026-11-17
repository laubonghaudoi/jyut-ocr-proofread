# 通用技能安裝同 OCR 環境

Repo 根目錄係唯一技能來源。將整個目錄放入 agent 嘅技能集合，即可讀到根目錄 `SKILL.md` 同相對資源；唔需要 Codex plugin manifest 或 marketplace。平台原生安裝技能通常唔執行 shell 初始化，首次使用仍要由同一套安裝器檢查執行環境。

## 統一入口

喺技能根目錄執行 `./install.sh --help` 睇全部參數：

```bash
# 預設 generic 技能位置；兩個 OCR 引擎（HunyuanOCR 同 PaddleOCR-VL）都裝，Linux／WSL2 唔使揀。
./install.sh

# macOS 首次安裝要先揀引擎（見〈macOS：先揀引擎〉）：下載兩個引擎，或者淨用 Apple Vision。
./install.sh --engine auto
./install.sh --engine vision

# macOS 橫排快速雙引擎模式；Vision 做 A 稿，Hunyuan 做 B 稿，唔裝 Paddle。
./install.sh --engine vision,hunyuan

# Apple Silicon、macOS 14 或以上：明確用 MLX；native 保留原有 Paddle 推理。
./install.sh --env-only --engine auto --paddle-vl-backend mlx-vlm-server
./install.sh --env-only --paddle-vl-backend native

# 重複 --agent 安裝到多個平台；qwen 係 Qwen Code。唔預設安裝 custom agents。
./install.sh --agent codex --agent claude --agent gemini --agent antigravity --agent qwen

# 任意平台嘅技能集合目錄；程式加 jyut-ocr-proofread 子目錄。
./install.sh --skill-dir /path/to/skills

# 只配置 OCR 環境，適合原生技能管理器已放好檔案嘅情況。
./install.sh --env-only
./install.sh --env-only --check

# 明確覆蓋：細機淨裝 Hunyuan；或者全部用 CPU。
./install.sh --env-only --engine hunyuan
./install.sh --env-only --device cpu

# 聲明本機校對模型（同埋 GPU 交接用嘅停／開命令）。
./install.sh --env-only --local-model-endpoint http://127.0.0.1:8080/v1/chat/completions \
  --local-model-name your-model-id --local-model-concurrency 4 \
  --local-model-release-cmd 'your-service-manager stop your-model-service' \
  --local-model-restore-cmd 'your-service-manager start your-model-service'
```

| 選項 | 意義 |
|---|---|
| `--agent` | 可重複：`generic`、`codex`、`claude`、`gemini`、`antigravity`、`qwen`；冇指定預設 `generic` |
| `--skill-dir` | 自訂技能集合目錄，唔係 `SKILL.md` 所在葉目錄 |
| `--with-agents` | 另裝所選 Codex／Claude 嘅可選 writer／auditor adapters |
| `--engine` | 明確覆蓋：`auto`（預設，Hunyuan 同 Paddle；Intel Mac 淨係 Hunyuan）、`hunyuan`、`vision`（只限 macOS，唔下載模型）、`vision,hunyuan`（macOS 橫排快速雙引擎，唔裝 Paddle）。唔傳就沿用上次揀嘅，冇就 `auto`；macOS 未揀過就先問（見〈macOS：先揀引擎〉） |
| `--device` | `auto`（預設：有 NVIDIA GPU 用 CUDA，Apple Silicon 用 Metal，否則 CPU）、`cpu`、`cuda`、`metal`；唔傳就沿用上次明確揀嘅裝置。Vision 自己管理裝置；MLX 模式嘅 Paddle layout 用 CPU |
| `--paddle-vl-backend` | `auto`、`native`、`mlx-vlm-server`；唔傳就沿用上次要求。MLX 需要 Apple Silicon 同 macOS 14 或以上，用獨立 Python 3.12 環境；`--device cpu` 同明確 MLX 要求衝突，會先報錯 |
| `--env-only` | 唔放置技能，只做環境初始化或檢查 |
| `--skill-only` | 只放置或檢查技能入口，唔配置 OCR 環境 |
| `--check` | 只讀檢查；唔下載、建置、改設定或啟動 OCR；MLX 只讀套件 metadata，唔 import MLX、初始化 Metal 或載入模型 |
| `--offline` | 只用本機已存在而通過核驗嘅資源；缺項會報告 |
| `--prepare-only` | 下載、核驗同寫設定，但唔試跑（試跑可能要 GPU 交接，一定要前景）；狀態 `pending-smoke`，exit `2`，直到冇呢個選項嘅一次試跑通過。期間 `--check` 都報 `pending-smoke`（exit `2`），講明淨係欠試跑，叫你喺前景跑 `./install.sh --env-only`；設定之後再改過就照報 action-required |
| `--install-system-deps` | 顯式允許嘗試已支援套件管理器；否則只列缺項同安裝指引 |
| `--config-dir`／`--data-dir` | 改設定及 runtime／模型／venv 儲存位置 |
| `--backup-root` | 被替換嘅技能入口備份去邊（預設 `~/.jyut-ocr-proofread-backups`） |
| `--local-model-endpoint`／`--local-model-name` | 本機校對模型嘅 chat completions URL 同 model ID（一齊用），寫入 `PROOFREAD_ENDPOINT`、`PROOFREAD_MODEL` |
| `--local-model-release-cmd`／`--local-model-restore-cmd` | 停同開返嗰個模型嘅 shell 命令（一齊用），寫入 `OCR_PHASE_RELEASE_CMD`、`OCR_PHASE_RESTORE_CMD` |
| `--local-model-health-url` | 模型開返之後答 2xx 嘅 URL，寫入 `OCR_PHASE_HEALTH_URL`；唔傳就用 endpoint 嘅 `/v1/models` |
| `--local-model-concurrency` | 嗰個 server 一次跑幾多個請求（佢嘅 `--max-concurrency`、parallel slots 或者 lanes），寫入 `PROOFREAD_MAX_INFLIGHT`；要同 `--local-model-endpoint`、`--local-model-name` 一齊用 |

Generic 同 Codex 共用 `~/.agents/skills/`；Claude 用 `~/.claude/skills/`；Gemini CLI 用 `~/.gemini/skills/`；Antigravity 用 `~/.gemini/config/skills/`；Qwen Code 用 `~/.qwen/skills/`。選多個 agent 時相同目標只安裝一次。Gemini CLI 同 Antigravity 唔係同一個 discovery 位置；原生平台嘅插件安裝命令亦唔等於安裝任意根目錄 `SKILL.md`。

安裝器保留 repo 作單一來源，技能入口指向該來源；移走或刪除 repo 會令本機連結失效。需要複製部署時，保留完整相對目錄；唔好只複製 `SKILL.md`。平台可能需要 reload 或新 session 先重新發現技能。

## 兩個引擎、裝置同平台

Linux／WSL2 上安裝器唔問揀邊個引擎：HunyuanOCR（GGUF，經 llama.cpp）同 PaddleOCR-VL 都裝，一齊做底稿。macOS 首次安裝先問用戶（下一節）；揀咗下載（`--engine auto`）就同樣裝呢兩個。預設用 GPU，冇可用 GPU 先用 CPU。

| 平台 | HunyuanOCR | PaddleOCR-VL |
|---|---|---|
| Linux／WSL2，有 NVIDIA GPU | CUDA | GPU；卡太細或者太舊就 CPU（見下表） |
| Linux／WSL2，冇 NVIDIA GPU | CPU | CPU |
| macOS Apple Silicon（揀 `--engine auto`） | Metal | macOS 14 或以上預設 MLX：Metal 做 VL、CPU 做 layout；舊 macOS 或明確 native 全程用 Paddle CPU |
| macOS Intel（揀 `--engine auto`） | CPU | 唔裝：PaddleOCR-VL 要 PaddlePaddle 3.2.1 或以上，macOS x86_64 最新只有 3.0.0；可以另用 Apple Vision 做提示底稿 |
| macOS（揀 `--engine vision`） | 唔裝 | 唔裝；淨用 Apple Vision，唔下載模型 |
| macOS（揀 `--engine vision,hunyuan`） | Metal 或 CPU | 唔裝；改用 Apple Vision 做 A 稿 |

### Apple Silicon：Paddle VL backend

獨立對照實驗通過後，`--paddle-vl-backend auto` 喺 Apple Silicon、macOS 14 或以上預設用 `mlx-vlm-server`；其他平台、較舊 macOS 同明確 `--device cpu` 繼續用 `native`。預設記喺 `runtime/ocr-lock.json` 嘅 `mlx.enabled_by_default`。明確 `--paddle-vl-backend native` 永遠保留原有 Paddle 推理；明確 MLX 要求喺唔支援嘅平台或同 `--device cpu` 一齊用會報錯，唔會靜靜轉 backend。

要求寫入 `JYUT_OCR_PADDLE_VL_BACKEND`，實際選擇寫入 `OCR_PADDLE_VL_BACKEND`。舊環境嘅 `OCR_PADDLE_DEVICE=cpu` 如果只係 `JYUT_OCR_DEVICE=auto` 當時揀出嚟，下次 setup 可以升級到 MLX；用戶明確揀嘅 CPU 或 native 會保留。`--check` 遇到需要升級嘅環境只會報 stale，唔會幫你改設定或者安裝。

MLX 同 Paddle 共用原有固定 revision 嘅 `PADDLE_VL_MODEL_DIR`，唔另換量化權重；Paddle layout 照用 `PADDLE_LAYOUT_MODEL_DIR`。`MLX_VLM_PYTHON` 指向獨立嘅 `mlx-python/`，固定 `mlx-vlm==0.8.0` 及完整依賴。MLX 同原有 Paddle 嘅 `transformers` 等依賴分開，唔可以合併兩個 venv。`--prepare-only` 會準備埋呢個環境；`--offline` 只重用已核驗環境或完整本機 cache，缺項會停低。

### macOS：先揀引擎

Mac 有內置嘅 OCR（Apple Vision），所以由 Hugging Face 下載嘅引擎（幾 GB 權重加 Paddle 環境）喺 macOS 係用戶嘅選擇，唔係預設。首次安裝（冇傳 `--engine`，`env.sh` 又未有上次揀嘅結果）喺下載任何嘢、裝任何依賴之前先問：

| 選擇 | 裝乜 | 約佔磁碟 | 之後點跑書 |
|---|---|---:|---|
| `--engine auto` | HunyuanOCR 同 PaddleOCR-VL（Intel Mac 淨係 HunyuanOCR），由 Hugging Face 下載 | Apple Silicon native 約 5.7 GB；MLX 額外預留約 2.2 GB；Intel 約 2.4 GB | 每頁兩份底稿，完整流程 |
| `--engine vision` | 淨係 Apple Vision，唔下載模型 | 0.2 GB 以內（核心 Python 環境） | 每頁一份底稿，單引擎流程（`SKILL.md` 四〈單引擎〉） |

- **喺 terminal 跑**（stdin 係 terminal）：安裝器列出兩個選擇同各自要幾多磁碟，等你輸入 `1` 或者 `2`。答三次都唔係，或者冇人答，就當冇 terminal 處理。
- **冇 terminal**（agent 嘅 shell、pipe、背景）：安裝器即刻停低，exit `2`，`ACTION REQUIRED` 行以 `The OCR engines of this Mac are not chosen yet` 開頭，列出兩個 `--engine` 同各自要幾多磁碟；乜都未下載。Agent 見到呢行要**問用戶揀邊個，唔准代佢揀**，再喺同一條命令加嗰個 `--engine` 重跑。`--check` 喺未揀之前報同一段（`action-required`）。
- 揀過一次就記入 `env.sh`（`JYUT_OCR_ENGINE`），之後唔傳 `--engine` 都唔再問。想轉就明確傳另一個 `--engine`：由 Vision 轉 `auto` 會下載兩個引擎；由 `auto` 轉 `vision` 唔會刪已下載嘅模型，要自己清走資料目錄入面嘅 `models/`、`paddle-models/`、`paddle-python/`、`mlx-python/`、`runtimes/`。
- `--engine hunyuan`（淨裝 Hunyuan）照舊係明確覆蓋，唔喺問題入面。
- `--engine vision,hunyuan` 同樣要明確傳，唔喺首次問題入面；只下載 Hunyuan，Vision 做 A 稿、Hunyuan 做 B 稿，兩個引擎順序跑。呢條路線係橫排快速模式；準確度／CER 由正常校對流程處理，唔係安裝或速度驗收條件。
- Linux／WSL2 冇內置引擎，唔問，照舊兩個都裝。

- AMD、Intel 等非 NVIDIA GPU 暫時當冇 GPU，用 CPU。
- 判斷有冇 GPU 只靠 NVIDIA driver（`nvidia-smi` 答到），唔使裝 CUDA toolkit（`nvcc`）：llama.cpp 用固定版本嘅現成 CUDA 二進制，Paddle 嘅 GPU wheel 自帶 CUDA。
- `--device auto` 唔會將 CPU 寫死：自動揀嘅結果寫入 `OCR_HUNYUAN_DEVICE`／`OCR_PADDLE_DEVICE` 畀 runner 用，但記住嘅要求（`JYUT_OCR_DEVICE`）仍然係 `auto`，下次安裝會重新偵測。淨係明確傳 `--device cpu` 先會一直用 CPU。
- 自動揀 CUDA 但準備唔到 CUDA runtime（driver 太舊又冇 `nvcc`）時，Hunyuan 暫時用 CPU，輸出會講原因；下載中斷就停低等你重跑續傳，唔會轉 CPU。
- CPU 會同時停用 decoder、projector 同 KV cache GPU offload（包括 `--n-gpu-layers 0` 同 `--no-mmproj-offload`）。CPU 速度未量度，大書可能好慢。

PaddleOCR-VL 嘅 GPU wheel 喺 Paddle 自己嘅 index，按 NVIDIA driver 同 compute capability 揀，最新 CUDA 優先：

| Index | NVIDIA driver | Compute capability |
|---|---|---|
| `cu130` | 580 或以上 | 7.5 或以上（包括 Blackwell） |
| `cu129` | 575.51.03 或以上 | 7.0 或以上（Paddle 嘅 Blackwell 頁用呢個；Linux aarch64 淨係有呢個） |
| `cu126` | 560.28.03 或以上 | 7.0 至 9.0 |
| `cu118` | 520.61.05 或以上 | 7.0 至 9.0 |

以下情況 Paddle 用 PyPI 嘅 CPU wheel：冇 NVIDIA GPU、compute capability 低過 7.0、GPU 總顯存少過約 11.5 GiB（Paddle 300 dpi 實測峰值 11,495 MiB；compute capability 7.x 冇 bf16，要用 float32，估計要雙倍，未量度）、或者 driver 舊過上表全部。Linux aarch64 冇 CPU wheel，GPU 用唔到就唔裝 Paddle。

## 固定版本同核驗

全部固定版本寫喺 `runtime/ocr-lock.json`。下載按固定大小同 SHA-256 核驗；中斷後可續跑，雜湊不符唔會安裝。已核實嘅本機檔案可以重用，單憑檔案存在唔會標為就緒。

**HunyuanOCR 模型**：`ggml-org/HunyuanOCR-GGUF` revision `8e070c9ad79e4ca97a9b4daa2f1ce17e8759afb1`：

| 檔案 | Bytes | SHA-256 |
|---|---:|---|
| `HunyuanOCR-bf16.gguf` | 1083218528 | `a160215620dbd0ab43ec6faa28259654fd24c929953aa97c765176f7c0363217` |
| `mmproj-HunyuanOCR-bf16.gguf` | 997235840 | `46401739a91d0778d86369bb952db685b215512d61a941c3b859f337f6014fcd` |

**llama.cpp**：release `b10969`（commit `391fac16460f15233a7740550d858ac96df3419d`）。優先用 release 現成二進制，SHA-256 係 GitHub 公布嘅 digest；CUDA 版連同 release 嘅 CUDA runtime bundle 一齊裝喺同一個資料夾，唔使本機有 CUDA toolkit：

| 平台 | 檔案 | 條件 |
|---|---|---|
| Linux x86_64 CUDA | `llama-b10969-bin-ubuntu-cuda-13.3-x64.tar.gz` ＋ `cudart-llama-b10969-bin-ubuntu-cuda-13.3-x64.tar.gz`（共約 559 MB） | driver 580 或以上 |
| Linux x86_64 CUDA | `llama-b10969-bin-ubuntu-cuda-12.8-x64.tar.gz` ＋ `cudart-…-cuda-12.8-x64.tar.gz`（共約 763 MB） | driver 570.26 或以上 |
| Linux aarch64 CUDA | `llama-b10969-bin-ubuntu-cuda-13.3-arm64.tar.gz` ＋ `cudart-…-cuda-13.3-arm64.tar.gz` | driver 580 或以上 |
| Linux CPU | `llama-b10969-bin-ubuntu-x64.tar.gz`（16.8 MB，解壓約 41 MB）／`…-ubuntu-arm64.tar.gz` | |
| macOS Apple Silicon | `llama-b10969-bin-macos-arm64.tar.gz`（11.1 MB，Metal shader 已內嵌） | |
| macOS Intel | `llama-b10969-bin-macos-x64.tar.gz` | |

冇合適二進制（例如 driver 舊過 570.26）先用源碼建置同一個 commit：要 `git`、`cmake`、C++ 編譯器，CUDA 仲要 `nvcc`（喺 `/usr/local/cuda/bin` 但唔喺 `PATH` 亦搵得到）；macOS Metal 要 Command Line Tools 嘅 `clang`，唔使獨立嘅 `metal` 編譯器（llama.cpp 預設內嵌 shader）。換咗 runtime 之後，用舊 runtime 寫嘅新版 Hunyuan 底稿會報 blocked；冇 `draftIdentity` 嘅舊底稿照收（見 [local-ocr.md](local-ocr.md)）。

**PaddleOCR-VL**：`paddlepaddle-gpu`（或 CPU 版 `paddlepaddle`）3.3.1、`paddleocr[doc-parser]` 3.7.0、`paddlex` 3.7.2，裝喺自己嘅 Python 3.12 環境（`PADDLEOCR_PYTHON`）。PyPI 嘅 wheel（`paddleocr`、`paddlex`、CPU 版 `paddlepaddle`）用 lock 入面嘅 SHA-256 核驗。Paddle 自己嘅 GPU index 冇公布 hash，lock 只記大小並標明 `trust-on-first-use`：第一次下載時安裝器將下載到嘅 SHA-256 記入 `setup-state.json` 嘅 `trustOnFirstUse`，之後再下載同一個 wheel 唔同就拒絕安裝。如果 Paddle 真係重新發佈咗，刪咗嗰條紀錄再重跑。其他依賴（numpy、opencv 等）用 `runtime/paddle-constraints.txt` 固定版本，每個 Paddle wheel 都用：佢由一個用得嘅 Paddle 環境生成（`runtime/make_paddle_constraints.py`），每個版本附 PyPI 公布嘅 SHA-256，用 uv 裝就逐個核對，唔啱就唔裝；冇 uv、用 pip 就淨係固定版本、唔核 hash（pip 一見到 hash 就要求每個 package 都有）。Paddle wheel 自己釘死版本嘅 package（CUDA 嘅 `nvidia-*` wheel、`cuda-python`、`opt-einsum`）同淨係佢哋先拉入嚟嘅（`cuda-bindings`、`cuda-pathfinder`）唔放入呢個共用檔，因為每個 CUDA 版本釘嘅版本唔同（例如 cu129 x86_64 要 `cuda-python==12.9.4`，cu130 要 13.0.3），放入去其他版本就裝唔到：cu130 x86_64 嘅 wheel 另外加 `runtime/paddle-constraints-cu130-x86_64.txt`（一樣附 hash）；cu129、cu126、cu118 嘅呢啲 package 用 Paddle wheel 自己釘嘅版本，唔核 hash。兩個檔都冇列嘅 package（某啲平台先要嘅）照舊由 PyPI 解析。已經符合 lock 嘅 Paddle 環境唔會因為呢啲檔重裝；檔案內容只記錄喺 setup 狀態，唔影響 setup 指紋。

**MLX 環境**另用 `runtime/mlx-requirements.txt` 固定全部套件版本同 wheel hash；呢個檔本身嘅 SHA-256 亦記喺 `runtime/ocr-lock.json`。uv 同 pip 都用 `--require-hashes` 安裝，唔會套用 Paddle 嘅「pip 只核版本」例外。`--check` 核對 Python 3.12、venv 隔離同全部固定套件版本；真正 Metal／模型載入留畀合成頁試跑。已配置嘅外部 MLX 環境只有完全符合先沿用，唔符合就另建受管理環境，唔改外部 venv。

權重固定 revision，逐個檔案核大小同 SHA-256（Hugging Face 樹嘅 LFS oid，其餘檔案嘅 SHA-256 由同一 revision 嘅檔案計出）：

| 模型 | Revision | 大小 |
|---|---|---:|
| `PaddlePaddle/PaddleOCR-VL-1.6` | `c5630abae1d940eafe0697512a0325494b02ab42` | 1,930,459,435 bytes（`model.safetensors` `85a479d5…`） |
| `PaddlePaddle/PP-DocLayoutV3` | `7b48a7566925fa464281f930c58eee04fe2c862a` | 132,017,021 bytes（`inference.pdiparams` `70bd316b…`） |

安裝器預先下載到資料目錄 `paddle-models/`，再用 `PADDLE_VL_MODEL_DIR`、`PADDLE_LAYOUT_MODEL_DIR` 指畀 runner，所以 OCR 時唔會喺 GPU 隊列或者限時入面下載最新版。PaddleX 自己嘅 cache（`~/.paddlex/official_models`）入面核驗得到嘅檔案會 hard link（唔得就複製）過去，唔使再下載；cache 本身唔會改。

**Python**：兩個 venv 都用 Python 3.12，由 [uv](https://docs.astral.sh/uv/) 提供（`PATH` 上嘅 `uv`，或者 `~/.local/bin/uv`）。搵 3.12 用 `uv python find --system`：唔會揀任何 venv（啟動咗嘅 `VIRTUAL_ENV`，或者目前資料夾同佢上層嘅 `.venv`），uv 自己管理嘅同 `PATH` 上嘅 3.12 照搵到，所以喺邊個資料夾跑安裝器結果都一樣；舊到冇 `--system` 嘅 uv 就喺資料目錄搵，唔帶 `VIRTUAL_ENV`。uv 搵唔到 3.12 就由 uv 下載一個（約 30 MB）放入資料目錄 `python-runtimes/`（用 `--no-bin`，唔會喺 `~/.local/bin` 放 `python3.12`；舊過 0.8 嘅 uv 本身就唔會放），uv 嘅 wheel cache 放 `uv-cache/`（已設 `UV_CACHE_DIR` 就用你嘅）。安裝器永遠唔會自己下載 uv；冇 uv 時核心 venv 用而家嘅 Python（要有 `venv` 同 `ensurepip`），Paddle 要 `python3.12`（連 `venv`）。兩樣都冇就報錯，講點裝 uv（`curl -LsSf https://astral.sh/uv/install.sh | sh`、`brew install uv` 或 `pipx install uv`）。venv 嘅 interpreter 因為系統升級等原因行唔到，就移去 `*-incomplete-時間` 保留，再喺原位重建。

已經有嘅 `PADDLEOCR_PYTHON`（例如舊技能整嘅 venv）只要已經有上面嘅固定版本、同 GPU／CPU 啱，就直接用，安裝器唔會改佢；唔啱就另外喺資料目錄整 `paddle-python/`，舊 venv 原封不動。

揀 MLX 會再建立第三個 Python 3.12 venv `mlx-python/`，用同一套 uv Python 供應、離線及中斷恢復機制；核心、Paddle 同 MLX 三個環境各自裝依賴。

## 磁碟

下載前先檢查資料目錄所在嘅剩餘空間：按缺嘅部分估算（下載按實際大小，解壓同安裝按估計），加 1 GiB 餘量，唔夠就停低，乜都未下載。llama-server 同 Paddle 環境用安裝時一樣嘅方法判斷：已設定嘅 `HUNYUAN_LLAMA_SERVER` 唔係固定 commit（`--version` 同 hash 對唔上），或者 `PADDLEOCR_PYTHON` 嘅套件唔啱（只讀套件 metadata），安裝器會另外裝一份，估算就計埋新嗰份，唔當已經有。大約佔用（裝好之後；安裝途中暫時要多啲，因為壓縮檔同 wheel 裝好先刪）：

| 平台 | 約佔用 | 組成 |
|---|---:|---|
| Linux／WSL2，NVIDIA GPU（`cu130`） | 約 12 GB | Paddle GPU 環境 6.3 GB（實測：paddle 2.9 GB、NVIDIA 函式庫 2.6 GB）、兩個模型權重約 4.1 GB、llama.cpp CUDA 版連 runtime 約 1.5 GB（估計）、Python 約 0.3 GB。用源碼建置嘅 llama.cpp 約 0.3 GB，總數約 10.6 GB（實測） |
| Linux／WSL2，CPU | 約 6 GB | 兩個模型權重約 4.1 GB、Paddle CPU 環境約 1.5 GB（估計；wheel 195 MB）、llama.cpp 約 41 MB |
| macOS Apple Silicon（`--engine auto`、native backend） | 約 5.5 GB | 兩個模型權重約 4.1 GB、Paddle CPU 環境約 1.2 GB（估計；wheel 105 MB）、llama.cpp 約 30 MB |
| macOS Apple Silicon（MLX backend） | native 基礎上額外預留約 2.2 GB | 獨立 MLX Python 同依賴嘅保守安裝估算；沿用相同 Paddle 權重，唔重複下載模型 |
| macOS Intel（`--engine auto`） | 約 2.4 GB | Hunyuan 權重約 2.1 GB、llama.cpp、Python |
| macOS（`--engine vision`） | 0.2 GB 以內 | 核心 Python 環境，唔下載模型；估計上限 0.2 GB（計埋 uv 要下載 Python 3.12），uv 已經有 3.12 嗰陣實測 27 MB（Apple M5，2026-11-16） |
| macOS（`--engine vision,hunyuan`） | 約 2.4 GB | Hunyuan 權重、llama.cpp、核心 Python；Vision 使用系統框架 |

## 設定、初始化同就緒狀態

預設設定目錄係 `${XDG_CONFIG_HOME:-$HOME/.config}/jyut-ocr`。預設資料目錄喺 Linux／WSL2 係 `${XDG_DATA_HOME:-$HOME/.local/share}/jyut-ocr`，macOS 係 `~/Library/Application Support/jyut-ocr`。核心 venv 喺資料目錄 `python/`，Paddle venv 喺 `paddle-python/`，按需安裝嘅 MLX venv 喺 `mlx-python/`；設定由 `env.sh` 載入，樣本見 `env.sh.example`。

```bash
. "${XDG_CONFIG_HOME:-$HOME/.config}/jyut-ocr/env.sh"
"$JYUT_OCR_PYTHON" scripts/survey_pdf.py /path/to/source.pdf /path/to/work
```

| 設定 | 意義 |
|---|---|
| `JYUT_OCR_ENGINE` | 引擎要求：`auto`，或者明確覆蓋 `hunyuan`／`vision`／`vision,hunyuan` |
| `JYUT_OCR_ENGINES` | 實際裝咗嘅引擎，例如 `hunyuan,paddle` 或 `vision,hunyuan`；`ocr_book.py --engines` 可以明確覆蓋當次選擇 |
| `JYUT_OCR_DEVICE` | 裝置要求：`auto`，或者明確揀嘅 `cpu`／`cuda`／`metal` |
| `JYUT_OCR_PYTHON` | 一般腳本用嘅核心 venv Python |
| `HUNYUAN_MODEL`、`HUNYUAN_MMPROJ`、`HUNYUAN_LLAMA_SERVER` | Hunyuan 模型、projector、llama-server |
| `OCR_HUNYUAN_DEVICE`、`OCR_HUNYUAN_MODE`、`OCR_HUNYUAN_SLOTS` | Hunyuan 實際裝置、服務模式、slot 數 |
| `PADDLEOCR_PYTHON` | Paddle venv 嘅 Python |
| `OCR_PADDLE_DEVICE` | Paddle 實際裝置：`gpu:0` 或者 `cpu`；MLX 模式呢個值只指 layout |
| `JYUT_OCR_PADDLE_VL_BACKEND` | Backend 要求：`auto`／`native`／`mlx-vlm-server` |
| `OCR_PADDLE_VL_BACKEND` | 實際 backend：`native` 或 `mlx-vlm-server` |
| `MLX_VLM_PYTHON` | MLX 專用 Python 3.12，獨立於核心及 Paddle venv |
| `PADDLE_VL_MODEL_DIR`、`PADDLE_LAYOUT_MODEL_DIR` | 固定 revision 嘅 Paddle 權重資料夾 |

一般腳本用 `JYUT_OCR_PYTHON`，Paddle 用 `PADDLEOCR_PYTHON`，MLX server 用 `MLX_VLM_PYTHON`；唔將 venv Python symlink resolve 成系統 Python。完整 OCR 選項見 [local-ocr.md](local-ocr.md)。

跑書時引擎選擇順序係明確 `--engines`、`JYUT_OCR_ENGINES`、該書 `reports/ocr-engines.json`，最後先係 `hunyuan,paddle` 預設；冇載入機器設定嘅續跑仍可沿用該書記錄。`--single-engine` 照舊明確選 Hunyuan。

**沿用舊設定**：安裝器淨係改值有變嘅 key，舊行一行都唔刪，其他行（包括佢唔識嘅行）原封不動，改之前將原檔備份去 `env-backups/`。一個 key 喺頂層得一行、嗰行又淨係設呢個值，安裝器就喺原位喺嗰行前面加 `# `，變做註解保留，上面加一行註解寫明日期同係安裝器取代咗佢（例如 `# HUNYUAN_LLAMA_SERVER: replaced by the jyut-ocr installer on 2026-11-16; the earlier line is kept below as a comment.`），下面緊接寫新嘅 `export`：你睇得到新舊兩個值，shell 淨係用新值。寫咗幾行、喺 `if`／`case`／function 入面、或者同其他命令擺喺同一行，安裝器一行都唔郁，喺檔尾加一行新值（上面一行註解講明）。之後再改，兩種寫法都係改返安裝器自己嗰行 `export`，唔會再加註解、亦唔會多一份舊行；用同一個設定重跑就乜都唔改。`env.sh` 本來冇嘅 key，安裝器喺檔尾加一行 `export`，上面冇註解；呢行第一次改值嗰陣，安裝器分唔出係佢自己寫嘅，所以照頂層得一行嘅做法保留做註解一次，之後先淨係改 `export`。`env.sh` 原本嘅權限照舊（例如 `0664` 改完仍然係 `0664`）；安裝器新開嘅 `env.sh` 同 `env-backups/` 入面嘅備份係 `0600`（淨係你自己讀寫得）。寫入之前用 `bash -n` 檢查：原檔語法啱而改完會錯，就停低報錯，乜都唔寫。舊技能寫嘅 `env.sh` 照讀：`PADDLEOCR_PYTHON`、`OCR_PHASE_*`、`PROOFREAD_*`、`HUNYUAN_*`、`OCR_HUNYUAN_*` 都保留；`OCR_PHASE_*` 等命令照原文保留，唔展開、唔執行。路徑只支援 `$HOME`／`${HOME}`，安裝器自己寫嘅路徑係絕對路徑。舊安裝器嘅 `JYUT_OCR_ENGINE`（單一引擎）同 `OCR_HUNYUAN_DEVICE` 係嗰陣自動揀嘅結果，唔當成明確覆蓋：而家重新按平台揀（兩個引擎、重新偵測裝置），輸出會講明；想保留就明確傳 `--engine`／`--device`。`env.sh` 係 symlink 嘅話，安裝器寫入佢指向嘅檔案，條 link 保留；指向唔存在嘅資料夾或者唔係普通檔案就停低報錯。

**試跑**：初始化會檢查 Python、Pillow、PDF 渲染工具及所選引擎依賴，然後每個引擎用一張合成頁跑實際 OCR。只有全部引擎都輸出非空轉錄同合格逐頁 JSON，先將環境 fingerprint 及試跑證據寫入 `setup-state.json` 並標記就緒；`--check` 要每個引擎嘅證據都喺度兼冇改過。

- Hunyuan 試跑用已設定嘅 `OCR_HUNYUAN_SLOTS`，每個 slot 用 runner 預設嘅 16384 context：約 3,500 MiB 加每 slot 1,536 MiB（1 slot 約 5,036 MiB，16 slots 約 28,076 MiB），唔因為呢次優化改 slot 數。Paddle 約要 12,288 MiB；MLX 暫用同一個保守值，計埋同時存在嘅 MLX server 同 Paddle layout worker。
- macOS 試跑同 `run_ocr_phase.py` 共用 `vm_stat` 探測：只計 free、inactive、speculative，按實際 page size 換算；purgeable 同其他分類重疊，唔會再加。讀唔到就當未知，已有交接設定時交畀交接處理；冇交接就停低講明。Metal、MLX 同 Darwin native CPU 都共享呢個記憶體池。
- 預算按當次真正選中嘅引擎計，順序跑所以取最大值，唔係相加。`vision,hunyuan` 只計 Hunyuan 需要，唔套用 Paddle 嘅 12 GiB；Vision 本身由系統管理。`OCR_PHASE_REQUIRED_MIB` 明確正數覆蓋保留。MLX 試跑會明確傳 backend、MLX interpreter 同固定模型 revision。
- 等 GPU 隊列（另一本書嘅 OCR 用緊）最多 300 秒；等唔到就報 action-required，乜都冇停，佢做完再重跑。
- GPU 唔夠空位而又冇聲明本機模型：報 action-required，講要幾多、得幾多；唔會叫你改用 CPU，亦唔會記低 CPU。
- 已聲明本機模型嘅停／開命令（`OCR_PHASE_RELEASE_CMD`、`OCR_PHASE_RESTORE_CMD`），而 GPU 唔夠空位：兩個引擎嘅試跑放喺**一次** GPU 交接入面（`scripts/run_ocr_phase.py`）：停模型、試跑、開返模型。需要 `OCR_PHASE_MAX_SECONDS`（`--local-model-release-cmd` 冇設就寫 540），而且一定要喺前景跑。呢個預算係 agent 成個 shell call 嘅：安裝器記住自己幾時開始，核驗、uv 等等用咗嘅時間會扣除，交畀 `run_ocr_phase.py` 嘅 `--max-seconds` 係淨低嘅時間；淨低少過 240 秒（預算細過 480 秒就係預算嘅一半）就唔停模型、唔試跑，報錯叫你喺前景再跑同一條命令（已下載、已核驗嘅保留）。`OCR_PHASE_SWAP=always` 每次都交接，`never` 永遠唔交接。交接結果：exit `75` 係時間預算用完、模型已開返，重跑同一條安裝命令繼續；`76` 係 GPU 忙（另一份工、另一個交接，或者模型嘅其他 client 仲有請求），通常乜都冇停；窗口入面試跑一頁都冇封到亦報 `76`（模型停過又開返，RESULT 行會講）：等一陣再重跑；`3` 係模型冇開返，即刻用 restore 命令開返；`2` 係設定錯誤。
- Agent 本身就係嗰個本機模型（例如 Qwen Code 經本機服務）嗰陣：下載同建置可能長過 shell 上限，先跑 `./install.sh --env-only --prepare-only`（可以放背景；佢下載、核驗同寫設定，唔試跑，所以唔會交接），完成之後喺前景跑 `./install.sh --env-only`：已核驗嘅部分會跳過，淨低試跑經交接做。唔好喺背景跑會交接嘅命令。

**下載長過 shell 上限（任何 agent，唔止上面講嘅本機模型）**：第一次裝要下載同建置幾 GB（見〈磁碟〉），而 Claude Code、Codex、Gemini CLI、Qwen Code 呢類 harness 一個 shell call 都有時限（Qwen Code 600 秒）；agent 唔理係咪本機模型都分三步，喺技能根目錄做。macOS 首次安裝先問用戶揀引擎（〈macOS：先揀引擎〉），下面每條 `./install.sh` 都加嗰個 `--engine`；揀 `--engine vision` 冇模型要下載，直接做第 3 步：

1. **背景準備**：用 harness 自己嘅背景方式（Claude Code 嘅 `run_in_background`、Qwen Code 嘅 `is_background`）跑 `./install.sh --env-only --prepare-only > "${TMPDIR:-/tmp}/jyut-ocr-prepare.log" 2>&1; echo "[prepare] exit=$?" >> "${TMPDIR:-/tmp}/jyut-ocr-prepare.log"`；冇背景 tool 就成條包喺 `nohup sh -c '…' >/dev/null 2>&1 &` 入面。佢下載、核驗同寫設定，唔試跑，所以唔使 GPU、唔會交接。
2. **等佢做完**：harness 背景 call 完會通知就等通知；冇通知就隔幾分鐘睇一次 `tail -1 "${TMPDIR:-/tmp}/jyut-ocr-prepare.log"`，見到 `[prepare] exit=N` 先算完，唔好不停讀成個 log。`[prepare] exit=2` 加上面 JSON 嘅 `"status": "pending-smoke"` 即係準備好；有 `ACTION REQUIRED` 就照佢講補齊，再跑同一條（已核驗嘅檔跳過，中斷咗嘅下載續傳）。
3. **前景試跑**：`./install.sh --env-only`，**唔准放背景**：已核驗嘅部分跳過，淨低每個引擎一張合成頁嘅試跑；本機模型佔住 GPU 就經一次 GPU 交接做（前景、tool call timeout 開到最大）。通過先標 `ready`，再用 `./install.sh --env-only --check` 確認。

唔好喺背景跑會試跑或者交接嘅命令（即係冇 `--prepare-only` 嘅 `./install.sh`）。

Fingerprint 計環境：引擎、各引擎實際裝置、Python 主次版本、`requirements.txt` 同 `runtime/ocr-lock.json` 嘅內容、模型檔 hash、runtime binary 及 library hash、Hunyuan slot 數（試跑證明佢裝得落），同 Paddle 係 GPU 定 CPU 版、CUDA build 同權重 revision。MLX backend 另外計固定依賴檔 hash 同全部套件版本；切換 native／MLX 或改 MLX 依賴都要重新試跑。技能 scripts、Python 完整版本號、其他已記錄套件版本、venv 同模型路徑只記錄，所以改 code 或 Python patch 版本唔會單獨要求重新試跑。

依賴缺失時先按輸出補齊，再重跑同一入口。需要管理員權限、Apple 系統 UI 或使用者處理嘅步驟會明確報告，唔假稱安裝完成。冇引擎但當前 agent 有讀圖能力，可以順序視覺轉錄；冇讀圖能力則保留待處理狀態。

## 本機校對模型

預設由當前 agent 校對（packet）。要改用本機 OpenAI-compatible 模型，或者 agent 本身就行緊本機模型，用 `--local-model-endpoint URL --local-model-name NAME` 聲明。`NAME` 要同服務列出嘅 model ID、同 agent 用嘅名一模一樣：`proofread_pages.py` 見到 agent 就係呢個本機模型（Qwen Code 嘅 `QWEN_CODE_MODEL` 等於 `PROOFREAD_MODEL`，而 `PROOFREAD_ENDPOINT` 喺本機），就經 HTTP 叫佢，拒絕 packet，頁圖唔會入 agent 嘅 context。其他 agent 用 session 環境 `JYUT_OCR_AGENT_IS_LOCAL_MODEL=1` 講明（`0` 講明唔係）；agent 自己設就要每個 shell call 開頭 export（見 `SKILL.md` 三），名對唔上嘅 Qwen Code 亦一樣。咁嘅 agent 照 `SKILL.md` 第一節 B 套：唔開頁圖、唔讀封存頁，淨係跑 script、讀 RESULT 行。

**並行數**：加 `--local-model-concurrency N`（N 係 server 一次跑幾多個請求，即係佢自己嘅 `--max-concurrency`、parallel slots 或者 lanes；唔好估，睇 server 嘅設定），安裝器寫入 `PROOFREAD_MAX_INFLIGHT`。之後經 `--backend http` 嘅 `book_profile.py --workers` 同 `proofread_pages.py --max-inflight` 冇畀就用佢，所以 B 套唔使再加呢兩個開關（`SKILL.md` 四第 3、4 步）。`env.sh` 冇呢個 key，B 套開工前要問用戶 N，每次加 `--workers N`／`--max-inflight N`；請求多過 server 收得落，多出嚟嗰啲喺 server 排隊，等到佢嘅 pending timeout 就當失敗。

模型同 OCR 搶同一張 GPU 時，加 `--local-model-release-cmd` 同 `--local-model-restore-cmd`（一定要成對；restore 要可以重複執行），安裝器寫入 GPU 交接嘅 `OCR_PHASE_*`。之後 OCR 經 `scripts/run_ocr_phase.py` 交接，詳情見 [local-ocr.md](local-ocr.md)。

## 升級、備份同復原

技能位置調整時，更新本機入口而保留同一份根目錄內容。舊 plugin／marketplace 包裝已移除，唔再提供 plugin 安裝命令。已有 plugin cache 應按所用平台停用舊入口，避免同名技能重複載入。

安裝器先備份被替換嘅目錄、檔案或外來連結，再變更入口；預設備份位置係 `~/.jyut-ocr-proofread-backups`，輸出會列 manifest。Skill symlink 同 adapter projection 會驗 hash、mode 同來源／目標重疊；漂移、未知格式或無法安全還原時停止，唔靜靜覆蓋。

```bash
# 只處理安裝入口，唔配置 OCR 環境。
python3 scripts/install_local_adapters.py --dry-run --agent claude
python3 scripts/install_local_adapters.py --check --agent claude

# v4 manifest 要帶同原安裝一致嘅目標／adapter 選項。
python3 scripts/install_local_adapters.py --restore /absolute/path/to/manifest.json --agent claude
```

舊 schema v2／v3 backup manifest 按歷史 layout 驗證同復原；唔改寫舊 manifest。OCR 輸出、run reports 同 review evidence 保留原 bytes 及當時路徑；搬技能唔授權批量重寫歷史證據。已封存輸出嘅續用仍由原本 hash／profile 契約判斷。

## 驗證界線

驗證記錄分開保存以下結果，列實際平台／版本、命令、證據路徑及未測範圍：

- **Native discovery**：平台能列出／載入技能，根目錄同相對資源位置正確。靜態掃描設定或 list 命令唔等於模型讀過 `SKILL.md`，亦唔等於有讀圖能力。
- **實際 agent 操作**：由該平台嘅當前模型讀技能、開原頁／裁圖，回答合成單頁 packet，再驗證成品。唔用安裝器或者假回答替代模型視覺驗證。
- **引擎實機 smoke**：記所選 CPU／CUDA／Metal／Vision、固定資源及實際單頁輸出；同 packet 回答／視覺正確性分開。
- **自動回歸測試**：合成資料與 fake runtimes 驗證程式契約，唔當成上述三項實機驗收。

Linux CUDA 試跑唔證明 macOS Vision／Metal、Linux CPU 或 WSL2 已實機通過；未實測嘅路線要照實列明。單頁 smoke 只證明環境可運行，唔證明整本書準確率，更唔代替同條件 benchmark。
