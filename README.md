# 粵文 OCR 校對

一份通用 [Agent Skill](https://agentskills.io/specification)，用原掃描圖像將歷史中文 PDF 忠實轉錄、校對成 Markdown。Repo 根目錄就係技能目錄：`SKILL.md`、`scripts/` 同 `references/` 只有一份，Codex、Claude Code、Gemini CLI、Antigravity、Qwen Code 或其他支援技能嘅 agent 都可以載入。

**兩個本機 OCR 引擎出底稿，校對者定讀法。** HunyuanOCR 同 PaddleOCR-VL 預設一齊裝、一齊用（macOS 首次安裝會先問：由 Hugging Face 下載呢兩個引擎，定係淨用系統內置嘅 Apple Vision、唔下載模型）；腳本負責流程同所有決定論檢查。校對者預設係調用技能嘅當前 agent（答 packet 題）；亦可以交本機模型（http）。Agent 本身就係本機模型嗰陣，一定由腳本直接問模型，頁圖唔入 agent 嘅 context。

## 技能結構

成個技能分三層：先一次過安裝 OCR 環境；每本書由兩個 OCR 引擎出底稿；再由校對者逐頁答流程問嘅題，最後修書、組裝成 Markdown。

### 1. 安裝

![安裝流程：install.sh 檢查已有環境、判斷平台同裝置（macOS 首次安裝先問用戶：下載兩個引擎，定係淨用 Apple Vision）、下載及核驗兩個引擎、每個引擎試跑合成頁、寫 env.sh；GPU 優先，冇就 CPU；本機模型佔住 GPU 就經 GPU 交接試跑](docs/diagrams/skill-install.svg)

- Linux／WSL2 上 `install.sh` 唔問揀邊個引擎：HunyuanOCR（GGUF＋llama.cpp）同 PaddleOCR-VL 都裝；已核實嘅環境照用，其餘下載固定版本並核對 SHA-256。
- macOS 有內置嘅 Apple Vision，所以首次安裝未下載任何嘢之前先問用戶：`--engine auto` 由 Hugging Face 下載兩個引擎（Apple Silicon 約佔 5.7 GB），定係 `--engine vision` 淨用 Apple Vision（唔下載模型，約 0.2 GB）。喺 terminal 跑會直接問；冇 terminal（例如由 agent 跑）就停低，講明兩個選擇，由 agent 問咗用戶再加 `--engine` 重跑，唔會代用戶揀。揀過一次之後記住，唔再問。
- 裝置預設用 GPU（Linux／WSL2 用 NVIDIA CUDA，Apple Silicon 用 Metal），冇可用 GPU 先用 CPU；卡太細或者太舊、或者喺 macOS，Paddle 用 CPU。
- Intel Mac 淨係 HunyuanOCR，Apple Vision 做提示；原生 Windows 唔支援，用 WSL2。
- 每個引擎試跑一張合成頁，全部成功先標就緒，設定寫入 `~/.config/jyut-ocr/env.sh`。
- 聲明咗本機校對模型同佢嘅停／開命令而 GPU 唔夠空位：兩個引擎嘅試跑放喺一次 GPU 交接入面（停模型、試跑、開返）。

### 2. 跑一本書

![跑一本書：出圖、ocr_book.py 跑 HunyuanOCR、草稿、PaddleOCR-VL 同補讀、量度本書、逐頁校對、定稿交付；本機模型佔住 GPU 就經 GPU 交接；校對者係當前 agent 或者本機模型](docs/diagrams/skill-run.svg)

- 分工：腳本負責流程同所有決定論檢查；OCR 引擎只出底稿；判斷讀法嘅係校對者。
- `survey_pdf.py` 出圖；`ocr_book.py` 一條命令做晒引擎 B（HunyuanOCR）、未校對草稿 `X-draft.md`（即刻交畀用戶）、引擎 A（PaddleOCR-VL）同引擎 B 補讀。
- 本機模型佔住 GPU（`env.sh` 有 `OCR_PHASE_RELEASE_CMD`）：`run_ocr_phase.py` 前景包住 `ocr_book.py`，等模型其他 client 做完先停模型，窗口入面閒置嘅 session 收到 503 加 `Retry-After`，兩個引擎跑完再開返模型。
- `book_profile.py` 量度兩個引擎同書眉（`<=4` 頁嘅短檔唔使）；`proofread_pages.py` 逐頁校對，全部頁封好就自己接跨頁表格。
- 校對者預設係當前 agent：`--backend packet` exit `10` 出題，agent 睇圖作答，經 `apply_packet_answers.py` 交答案再重跑，直到 exit `0`。
- 可以改由本機模型校對：`--backend http`，腳本經 `PROOFREAD_ENDPOINT`、`PROOFREAD_MODEL` 直接問。Agent 本身就係嗰個本機模型（例如 Qwen Code 配本機 Qwen）：`SKILL.md` 嘅 B 套 command 明寫 `--backend http`；腳本認得出（`JYUT_OCR_AGENT_IS_LOCAL_MODEL=1`，或者 Qwen Code 嘅 model 名等於 `PROOFREAD_MODEL`）就預設 http、拒絕 packet，頁圖唔入 agent 嘅 context。
- `finish_book.py` 驗封頁、修書、組裝、寫疑點清單、做最後審核（審核錯誤都當疑點），照評測輸出規則（見〈內容保留同驗證〉）將 `X.md` 同 `X-doubts.md` 放喺 PDF 隔籬；`clean_book.py --all` 清走工作目錄。
- 單引擎（淨係得 HunyuanOCR）係明確例外，用 `--single-engine`。

### 3. 逐頁校對

![逐頁校對：對齊兩份底稿、裁決分歧、加標點、補讀排版、字元守門、封頁；開關開先有整頁清點同整頁覆核；例外係空白頁、只有一份底稿同守門唔過記疑點](docs/diagrams/skill-page.svg)

- 腳本對齊兩份底稿：兩個引擎一致嘅字唔問；唔同嘅地方由校對者睇裁圖揀讀法。
- 之後逐段加標點（唔准改字）、補讀同排版（段落、標題、表格）；排版結果要過字元守門 `verify_characters`，重試都唔過就用退返嘅版本，記疑點照封。
- 封頁寫 `page-NNNN.md` 加 `.json` 封條；定唔到嘅記入疑點，唔當失敗。
- 例外：兩個引擎都冇字就唔問、封空頁；只有一份底稿（或者冇漢字但有西文、數字）就對住原圖轉錄；兩個引擎次序對唔上就成頁用偏好引擎嘅讀法；缺引擎 B 底稿嘅頁唔封，等 OCR 讀完再跑。
- 內容完整性 v2 係開關 `--content-fidelity on`，預設關，用戶要求先開：每頁多整頁清點、排版前補讀同最後覆核三條題，定唔到就記疑點照封。
- 全部頁封好之後，腳本自動接跨頁表格。

圖由 [Archify](https://archify.si/) 生成，來源係 `docs/diagrams/*.workflow.json`；改流程之後要同步更新呢三張圖。

## 安裝

支援 Linux、macOS 同 Windows WSL2；目前唔支援原生 Windows。先 clone 呢個 repo，再喺 repo 根目錄執行：

```bash
# 兩個 OCR 引擎都裝；有 NVIDIA GPU（或者 Apple Silicon）就用，否則用 CPU。
# macOS 首次安裝會先問揀邊個引擎（見下面兩條），揀咗先下載。
./install.sh --agent claude

# macOS：直接講明揀邊個，唔使答問題。
./install.sh --agent claude --engine auto     # 由 Hugging Face 下載 HunyuanOCR 同 PaddleOCR-VL
./install.sh --agent claude --engine vision   # 淨用內置嘅 Apple Vision，唔下載模型

# 可以同時裝畀幾個 agent；qwen 係 Qwen Code。
./install.sh --agent codex --agent gemini --agent antigravity --agent qwen

# 其他 agent：傳「技能集合」目錄，程式會放入 jyut-ocr-proofread 子目錄。
./install.sh --skill-dir /path/to/agent/skills

# 細機淨裝 HunyuanOCR，或者全部用 CPU（明確覆蓋）。
./install.sh --agent claude --engine hunyuan
./install.sh --agent claude --device cpu

# 只讀檢查已安裝入口、依賴、設定同已有試跑證據。
./install.sh --agent claude --check
```

安裝器先檢查並重用已核實嘅環境，用 uv 嘅 Python 3.12 建立獨立 venv，配置兩個引擎，再實際試跑，成功先標為就緒。HunyuanOCR 固定 BF16 模型與 projector；`llama.cpp` 用固定 release（b10969）嘅現成二進制，按 SHA-256 核驗，冇合適二進制先用源碼建置同一個 commit。PaddleOCR-VL 嘅套件同權重固定版本，預先下載到本機資料夾，OCR 時唔會再下載。需要系統套件、管理員權限或 Apple Command Line Tools 時會列出缺項；可補齊後重跑，唔會自動改顯示卡驅動。詳見 [安裝、平台選項及復原](references/install.md)。

| 選項 | 預設個人技能目錄 |
|---|---|
| `--agent generic` | `~/.agents/skills/` |
| `--agent codex` | `~/.agents/skills/` |
| `--agent claude` | `~/.claude/skills/` |
| `--agent gemini` | `~/.gemini/skills/` |
| `--agent antigravity` | `~/.gemini/config/skills/` |
| `--agent qwen` | `~/.qwen/skills/` |

Gemini CLI 同 Antigravity 係兩個安裝目標。平台原生技能管理器通常只放置檔案，唔會執行環境初始化；用嗰種方式安裝後，首次使用時喺技能根目錄跑 `./install.sh --env-only`，再 `./install.sh --env-only --check`。新增技能後按平台要求重新載入技能／開新 session。

本機有 OpenAI-compatible 模型負責校對（或者 agent 本身就行緊佢），用 `--local-model-endpoint URL --local-model-name NAME` 聲明；同 OCR 搶同一張 GPU 就再加 `--local-model-release-cmd` 同 `--local-model-restore-cmd`，見 [install.md](references/install.md)〈本機校對模型〉。

額外 writer／auditor 角色只係可選便利設定：`--with-agents` 先會安裝目前支援嘅 Codex／Claude adapters。冇安裝 adapters 亦可用普通 worker 或順序處理。平台權限及模型繼承細節見 [adapters](adapters/README.md)。

## 點樣用

可以直接要求 agent：

> 用 jyut-ocr-proofread，將呢份 PDF 全部掃描頁轉錄成 Markdown，保留原字形、標點、表格同註號，列出未能確定嘅地方。

Agent 照 `SKILL.md` 嘅流程表一步一個 command：出圖、OCR、book profile、逐頁校對、定稿、交付。用戶會收到三次：引擎 B 讀完即刻有未校對草稿 `X-draft.md`；校對期間有進度稿；最後 PDF 隔籬有定稿 `X.md` 同疑問清單 `X-doubts.md`，工作目錄之後清走。定唔到嘅位全部列喺 `X-doubts.md`，要對住原書核；流程唔會聲稱已逐頁視覺校對。

腳本命令喺技能根目錄執行，使用安裝器配置嘅 `JYUT_OCR_PYTHON`：

```bash
. "${XDG_CONFIG_HOME:-$HOME/.config}/jyut-ocr/env.sh"
"$JYUT_OCR_PYTHON" scripts/survey_pdf.py /path/to/book.pdf BOOK
"$JYUT_OCR_PYTHON" scripts/ocr_book.py BOOK
"$JYUT_OCR_PYTHON" scripts/book_profile.py BOOK/renders BOOK/ocr-paddle BOOK/ocr-hunyuan BOOK/book-profile.json
"$JYUT_OCR_PYTHON" scripts/proofread_pages.py BOOK/renders BOOK/ocr-paddle BOOK/proofread --draft2-directory BOOK/ocr-hunyuan
"$JYUT_OCR_PYTHON" scripts/finish_book.py BOOK
```

Packet 步驟 exit `10` 時答題再重跑；長步驟放背景，用 `scripts/wait_for_run.py` 等。完整流程、exit 碼同續跑見 `SKILL.md`；各步細節、每類疑點同開關見 [跑書流程](references/long-book-workflow.md)；引擎、裝置同 GPU 交接見 [本機 OCR](references/local-ocr.md)。冇可用 OCR 引擎、要人手分段轉錄時見 [人手分段同 structured 定稿](references/structured-workflow.md)。

## 內容保留同驗證

- 原掃描圖像係最終依據；引擎一致、文字通順、測試通過同 hash seals 都唔等於原文正確。
- 輸出跟評測規則：只刪書眉、頁碼、版心、印章、索書號、條碼同館藏印；保留原字形、數字、外文、算式、原印項號與註號、舊標點；雙圈句號寫 `。。`，字面星號寫 `\*`；交付稿冇頁碼分隔。
- 註腳：引用 `[^n]` 留喺原位，保留原印編號，重用嘅原印號改用 `[^n-2]` 呢類唯一 ID；定義預設留喺頁底；配唔上嘅一半照原印留低，記做疑點。
- 表格：橫排表保持原行列，直排表由右至左嘅直欄變成由上至下嘅行；冇表頭或者有合併格用 HTML `<table>`；跨頁續表合併。圖表入面嘅字照轉，預設唔抽圖（`--extract-figures` 先抽）。
- 封存頁記低校對 transport、model 同 script hash 但唔用嚟判過期；舊版技能封好嘅頁同 OCR 底稿照收。OCR 底稿淨係 render、模型、runtime 同影響輸出嘅設定改咗先要重做。
- 正文／標點、閱讀次序、表格格位同識別錯誤率分開驗證。單元測試、實頁試驗、跨平台實機驗證同同條件 benchmark 亦分開報告，唔以安裝成功宣稱準確率改善。

實書嘅書名、頁碼、專名、來源句式及單一版次政策留喺 project-local 記錄，核心回歸測試用 synthetic 正反例。

## 可選人工標註

一般 OCR／校對唔需要另一個 repo。需要互動式人工對圖裁定時，可以用 `scripts/review.py` 接上 `jyut-ocr-bench` 共用介面：現行 bench（review API 2）將答案寫入 bench 自己 git 跟蹤嘅 `sample-catalogue/v1.json`，改動歷史靠 git；舊 bench（API 1）先將圖片、題目同 append-only 答案歷史留喺相應 project。本技能唔保存答案。未裝介面時仍可完成一般校對並保存疑點，唔以缺少介面為理由丟棄未定讀法。詳見 [人工標註工具](references/manual-review-tool.md)。

## 維護

```bash
python3 tests/run_all.py                    # 全套：unittest 加回歸 check
python3 tests/run_all.py --only unit        # 淨係 tests/test_*.py
python3 tests/run_all.py --only checks      # 淨係 tests/check_*.py
python3 tests/run_all.py stitch --log-dir /tmp/jyut-ocr-tests   # 揀名有 stitch 嘅檔，留低每個檔嘅輸出
```

`tests/run_all.py` 係唯一測試入口：每個檔喺自己嘅 subprocess 跑，限時 900 秒，`PROOFREAD_ENDPOINT` 指去冇人聽嘅 port，唔使 GPU、唔使模型；逐個檔印 PASS／FAIL 同總結，有一個失敗就 exit 1。`tests/check_*.py`、`tests/cases/` 同 `tests/golden/` 係回歸 check 同人手核過嘅 golden；評已校對輸出目錄嘅 `tests/run_regression.py` 同 benchmark 快照同步，見 [開發筆記](references/developer-notes.md)。

測試以合成資料及 fake runtimes 驗證契約；實際 OCR 引擎 smoke、平台原生技能發現，以及當前模型讀技能／核原圖嘅操作驗證分開記錄。成功列出技能唔等於模型已核圖；未實機驗證嘅平台唔標成已通過。版本歷史只維護喺 [CHANGELOG.md](CHANGELOG.md)。

```text
SKILL.md             # 通用技能入口（流程表）
install.sh           # 安裝／環境檢查入口
env.sh.example       # 本機設定樣本（安裝器寫真嘅 env.sh）
scripts/             # 安裝、OCR、packet、校對、修書及驗證工具
references/          # 按任務需要載入嘅規則
runtime/             # 固定版本清單（ocr-lock.json）
agents/              # 可選平台展示 metadata
adapters/            # 可選平台 custom-agent 設定
tests/               # 合成測試、回歸 check 同 golden（run_all.py 係入口）
docs/diagrams/       # 技能結構圖（SVG）同 Archify 來源
```

## License

MIT
