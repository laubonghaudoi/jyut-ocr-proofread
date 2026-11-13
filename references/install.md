# 通用技能安裝同 OCR 環境

Repo 根目錄係唯一技能來源。將整個目錄放入 agent 嘅技能集合，即可讀到根目錄 `SKILL.md` 同相對資源；唔需要 Codex plugin manifest 或 marketplace。平台原生安裝技能通常唔執行 shell 初始化，首次使用仍要由同一套安裝器檢查執行環境。

## 統一入口

喺技能根目錄執行 `./install.sh --help` 睇全部參數：

```bash
# 預設 generic 技能位置；Linux／WSL2 預設 Hunyuan。
./install.sh

# 重複 --agent 安裝到多個平台；唔預設安裝 custom agents。
./install.sh --agent codex --agent claude --agent gemini --agent antigravity

# 任意平台嘅技能集合目錄；程式加 jyut-ocr-proofread 子目錄。
./install.sh --skill-dir /path/to/skills --engine hunyuan --device cpu

# 只配置 OCR 環境，適合原生技能管理器已放好檔案嘅情況。
./install.sh --env-only --engine hunyuan
./install.sh --env-only --check

# macOS 無互動安裝必須揀引擎。
./install.sh --agent claude --engine vision
./install.sh --agent claude --engine hunyuan --device auto
```

| 選項 | 意義 |
|---|---|
| `--agent` | 可重複：`generic`、`codex`、`claude`、`gemini`、`antigravity`；冇指定預設 `generic` |
| `--skill-dir` | 自訂技能集合目錄，唔係 `SKILL.md` 所在葉目錄 |
| `--with-agents` | 另裝所選 Codex／Claude 嘅可選 writer／auditor adapters |
| `--engine` | `auto`、`vision`、`hunyuan`；Linux／WSL2 `auto` 用 Hunyuan，macOS 互動選擇 |
| `--device` | Hunyuan 嘅 `auto`、`cpu`、`cuda`、`metal`；唔改校對模型 |
| `--env-only` | 唔放置技能，只做環境初始化或檢查 |
| `--check` | 只讀檢查；唔下載、建置、改設定或啟動 OCR |
| `--offline` | 只用本機已存在而通過核驗嘅資源；缺項會報告 |
| `--install-system-deps` | 顯式允許嘗試已支援套件管理器；否則只列缺項同安裝指引 |
| `--config-dir`／`--data-dir` | 改設定及 runtime／模型／venv 儲存位置 |

Generic 同 Codex 共用 `~/.agents/skills/`；Claude 用 `~/.claude/skills/`；Gemini CLI 用 `~/.gemini/skills/`；Antigravity 用 `~/.gemini/config/skills/`。選多個 agent 時相同目標只安裝一次。Gemini CLI 同 Antigravity 唔係同一個 discovery 位置；原生平台嘅插件安裝命令亦唔等於安裝任意根目錄 `SKILL.md`。

安裝器保留 repo 作單一來源，技能入口指向該來源；移走或刪除 repo 會令本機連結失效。需要複製部署時，保留完整相對目錄；唔好只複製 `SKILL.md`。平台可能需要 reload 或新 session 先重新發現技能。

## 固定模型、runtime 同裝置

Hunyuan 模型來源固定為 `ggml-org/HunyuanOCR-GGUF` revision `8e070c9ad79e4ca97a9b4daa2f1ce17e8759afb1`：

| 檔案 | Bytes | SHA-256 |
|---|---:|---|
| `HunyuanOCR-bf16.gguf` | 1083218528 | `a160215620dbd0ab43ec6faa28259654fd24c929953aa97c765176f7c0363217` |
| `mmproj-HunyuanOCR-bf16.gguf` | 997235840 | `46401739a91d0778d86369bb952db685b215512d61a941c3b859f337f6014fcd` |

合共 2,080,454,368 bytes，約 1.94 GiB。[固定模型 metadata](https://huggingface.co/api/models/ggml-org/HunyuanOCR-GGUF/tree/8e070c9ad79e4ca97a9b4daa2f1ce17e8759afb1?recursive=true&expand=false)

Runtime 用 `ggml-org/llama.cpp` commit `67a17c17caa95742186f8b1ecadd1b5abd6d5ebb` 建置，唔下載 `latest` 替換現有配套。[固定版本建置文件](https://github.com/ggml-org/llama.cpp/blob/67a17c17caa95742186f8b1ecadd1b5abd6d5ebb/docs/build.md)

- Linux／WSL2：`auto` 只有喺 CUDA runtime、driver 及所需建置工具可用時先用 CUDA，否則 CPU。`nvidia-smi` 成功唔等於已有 `nvcc`。唔自動改 GPU driver；WSL GPU driver 由 Windows host 提供。
- macOS：可揀唔下載模型嘅 Apple Vision，或者固定 Hunyuan 配套；Hunyuan `auto` 優先 Metal，唔可用則 CPU。Vision 需要 Swift 同 Apple Command Line Tools；安裝器會指出要完成嘅系統步驟。
- CPU 同時停用 decoder、projector 同 KV cache GPU offload（包括 `--n-gpu-layers 0` 同 `--no-mmproj-offload`），唔只關閉 decoder offload。Device 設定會納入 OCR 續跑及共用服務身份，唔將不同裝置設定嘅底稿當成同一次運行。
- 全新設定預設一個 Hunyuan slot；保留現有明確配置嘅 slots。模型、runtime 同 Python 環境放使用者資料目錄，唔放 repo。

下載以固定大小及 SHA-256 核驗；中斷後可續跑，雜湊不符唔當成有效模型。已核實嘅本機模型／runtime 可以重用，單憑檔案存在唔會標為就緒。Paddle 係按需第二底稿，唔屬首次安裝必要下載。

## 設定、初始化同就緒狀態

預設設定目錄係 `${XDG_CONFIG_HOME:-$HOME/.config}/jyut-ocr`。預設資料目錄喺 Linux／WSL2 係 `${XDG_DATA_HOME:-$HOME/.local/share}/jyut-ocr`，macOS 係 `~/Library/Application Support/jyut-ocr`。Venv 喺資料目錄 `python/`，設定由 `env.sh` 載入。

安裝器保留已存在嘅設定，唔覆寫整份檔案；舊模型／runtime 路徑必須核驗。明確揀另一引擎或裝置後，按安裝報告核對最終有效設定。一般腳本用 `JYUT_OCR_PYTHON`，Paddle 用自己嘅 `PADDLEOCR_PYTHON`；唔將 venv Python symlink resolve 成系統 Python。

```bash
. "${XDG_CONFIG_HOME:-$HOME/.config}/jyut-ocr/env.sh"
"$JYUT_OCR_PYTHON" scripts/survey_pdf.py /path/to/source.pdf /path/to/work
```

主要配置包括 `JYUT_OCR_PYTHON`、`JYUT_OCR_ENGINE`、`HUNYUAN_MODEL`、`HUNYUAN_MMPROJ`、`HUNYUAN_LLAMA_SERVER`、`OCR_HUNYUAN_DEVICE`、`OCR_HUNYUAN_MODE` 同 `OCR_HUNYUAN_SLOTS`。原有 CLI／環境變數介面繼續可以用；完整 OCR 選項見 [local-ocr.md](local-ocr.md)。

初始化會檢查 Python、Pillow、PDF 渲染工具及所選引擎依賴，然後用合成頁跑實際 OCR。只有成功輸出非空轉錄同合格逐頁 JSON，先將對應環境 fingerprint 及試跑證據寫入 `setup-state.json` 並標記就緒。`--check` 驗目前檔案及已有證據，唔代替新機首次推論；改過模型、runtime 或有效設定需要重新驗證。

依賴缺失時先按輸出補齊，再重跑同一入口。需要管理員權限、Apple 系統 UI 或使用者處理嘅步驟會明確報告，唔假稱安裝完成。冇引擎但當前 agent 有讀圖能力，可以順序視覺轉錄；冇讀圖能力則保留待處理狀態。

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

舊 schema v2／v3 backup manifest 按歷史 layout 驗證同復原；唔改寫舊 manifest。OCR 輸出、run reports、project-local tool snapshots 同 release evidence 保留原 bytes 及當時路徑；搬技能唔授權批量重寫歷史證據。已封存輸出嘅續用仍由原本 hash／profile 契約判斷。

## 驗證界線

驗證記錄分開保存以下結果，列實際平台／版本、命令、證據路徑及未測範圍：

- **Native discovery**：平台能列出／載入技能，根目錄同相對資源位置正確。靜態掃描設定或 list 命令唔等於模型讀過 `SKILL.md`，亦唔等於有讀圖能力。
- **實際 agent 操作**：由該平台嘅當前模型讀技能、開原頁／裁圖，回答合成單頁 packet，再驗證成品。唔用安裝器或者假回答替代模型視覺驗證。
- **引擎實機 smoke**：記所選 CPU／CUDA／Metal／Vision、固定資源及實際單頁輸出；同 packet 回答／視覺正確性分開。
- **自動回歸測試**：合成資料與 fake runtimes 驗證程式契約，唔當成上述三項實機驗收。

Linux CUDA 試跑唔證明 macOS Vision／Metal、Linux CPU 或 WSL2 已實機通過；未實測嘅路線要照實列明。單頁 smoke 只證明環境可運行，唔證明整本書準確率，更唔代替同條件 benchmark。
