# 粵文 OCR 校對

一份通用 [Agent Skill](https://agentskills.io/specification)，用原掃描圖像將歷史中文 PDF 忠實轉錄、校對成 Markdown。Repo 根目錄就係技能目錄：`SKILL.md`、`scripts/` 同 `references/` 只有一份，Codex、Claude Code、Gemini CLI、Antigravity 或其他支援技能嘅 agent 都可以載入。

**智能校對由調用技能嘅當前 agent 模型負責。** HunyuanOCR、Apple Vision 同按需使用嘅 PaddleOCR-VL 只提供底稿。Agent 要能直接讀原頁／裁圖、讀寫工作檔及執行所需腳本；唔需要指定模型、外部校對 API key 或 custom-agent adapters。冇子 agent 時可以順序完成；同一 writer 重讀唔算獨立審核。

## 安裝

支援 Linux、macOS 同 Windows WSL2；目前唔支援原生 Windows。先 clone 呢個 repo，再喺 repo 根目錄執行：

```bash
# Linux／WSL2：配置 HunyuanOCR；可用 CUDA 就用，否則用 CPU。
./install.sh --agent claude

# 可以同時裝畀幾個 agent。
./install.sh --agent codex --agent gemini --agent antigravity

# macOS：選 Apple Vision，或者 --engine hunyuan 下載固定模型。
./install.sh --agent claude --engine vision

# 其他 agent：傳「技能集合」目錄，程式會放入 jyut-ocr-proofread 子目錄。
./install.sh --skill-dir /path/to/agent/skills --engine hunyuan

# 只讀檢查已安裝入口、依賴、設定同已有試跑證據。
./install.sh --agent claude --check
```

安裝器先檢查並重用已核實嘅環境，建立獨立 Python venv、配置依賴同 OCR 引擎，再實際試跑一張合成頁，成功先標為就緒。HunyuanOCR 固定 BF16 模型與 projector，約 1.94 GiB；`llama.cpp` 固定 commit 建置，唔跟隨 `latest`。需要系統套件、管理員權限或 Apple Command Line Tools 時會列出缺項；可補齊後重跑，唔會自動改顯示卡驅動。詳見 [安裝、平台選項及復原](references/install.md)。

| 選項 | 預設個人技能目錄 |
|---|---|
| `--agent generic` | `~/.agents/skills/` |
| `--agent codex` | `~/.agents/skills/` |
| `--agent claude` | `~/.claude/skills/` |
| `--agent gemini` | `~/.gemini/skills/` |
| `--agent antigravity` | `~/.gemini/config/skills/` |

Gemini CLI 同 Antigravity 係兩個安裝目標。平台原生技能管理器通常只放置檔案，唔會執行環境初始化；用嗰種方式安裝後，首次使用時喺技能根目錄跑 `./install.sh --env-only`，再 `./install.sh --env-only --check`。macOS 無互動安裝要明確指定 `--engine vision` 或 `--engine hunyuan`。新增技能後按平台要求重新載入技能／開新 session。

額外 writer／auditor 角色只係可選便利設定：`--with-agents` 先會安裝目前支援嘅 Codex／Claude adapters。通用核心用能力同角色職責分工，冇安裝 adapters 亦可用普通 worker 或順序處理。平台權限及模型繼承細節見 [adapters](adapters/README.md)。

## 點樣用

可以直接要求 agent：

> 用 jyut-ocr-proofread，將呢份 PDF 全部掃描頁轉錄成 Markdown，保留原字形、標點、表格同註號，列出未能確定嘅地方。

首次使用先做環境檢查，讀本書凡例同目標 repo 指示，再選最低足夠嘅工作 profile。`basic` 係預設逐頁工作；有跨頁結構或分段時用 `structured`；明確要求可重播證據封存時先用 `release`。全部 profile 都要求逐頁直接核原圖。

腳本命令喺技能根目錄執行。以下係已初始化環境嘅起點；自訂設定目錄時改成安裝器印出嘅路徑：

```bash
. "${XDG_CONFIG_HOME:-$HOME/.config}/jyut-ocr/env.sh"
"$JYUT_OCR_PYTHON" scripts/survey_pdf.py /path/to/book.pdf /path/to/book-work
"$JYUT_OCR_PYTHON" scripts/run_hunyuanocr.py \
  /path/to/book-work/renders /path/to/book-work/ocr-hunyuan \
  --report /path/to/book-work/hunyuan-run.json
```

macOS Vision 改用 `scripts/run_apple_vision_ocr.py`。單引擎預設毋須 `book-profile.json` 引擎校準：直接以 `proofread_pages.py --backend packet` 核圖，完成後用 `finish_book.py --single-engine --ocr-a` 指定真實底稿組裝；未定跨頁接續保留為疑點，唔跑雙引擎修書。完整底稿、packet 核圖、補讀、定稿及續跑命令見 [本機 OCR](references/local-ocr.md)。使用安裝器配置嘅 `JYUT_OCR_PYTHON`，唔將 venv Python 路徑 resolve 成系統 Python。

Packet 流程明確傳 `--backend packet`，由當前 agent 讀所需圖像及回答 JSON bundles；exit `10` 代表等待答案，經 `apply_packet_answers.py` 驗證後重跑同一命令。外部 HTTP 校對服務只有顯式選擇先會啟用。冇可用底稿時，可以直接逐頁視覺轉錄，唔假造空底稿令 packet pipeline 繼續；冇讀圖能力就不能聲稱完成視覺校對。

## 內容保留同驗證

- 原掃描圖像係最終依據；引擎一致、文字通順、測試通過同 hash seals 都唔等於原文正確。完整頁圖普查與最後覆核用嚟找雙底稿共同漏塊。
- 保留原字形、數字、外文、算式、原印項號與註號、舊標點；雙圈句號寫 `。。`，正文及 Markdown 表內字面星號寫 `\*`。頁面家具按交付範圍移除，唔將一次性出版資料或圖版內文字當成家具。
- 表格方向與格內讀序分開核圖。預設 `auto` 按圖重建邏輯紀錄；只有明確要求物理格網先用 `as-printed`。跨頁接表須有同表、格位及註文歸屬證據。
- 每個 writer 只改自己頁段；封存後停寫。需要獨立審核時，由不同 reviewer 核 sealed candidate；平台未提供所需只讀隔離，或者未有獨立 reviewer，就保留相應未完成狀態。
- 正文／標點、閱讀次序、表格格位同識別錯誤率分開驗證。單元測試、實頁試驗、跨平台實機驗證同同條件 benchmark 亦分開報告，唔以安裝成功宣稱準確率改善。

[SKILL.md](SKILL.md) 提供完整規則及按需要載入嘅 references；[內容完整性](references/content-fidelity.md) 說明漏讀與忠實保留；[release gate](references/release-gate.md) 保存可重播交付契約。實書嘅書名、頁碼、專名、來源句式及單一版次政策留喺 project-local 記錄，核心回歸測試用 synthetic 正反例。

## 可選人工標註

一般 OCR／校對唔需要另一個 repo。需要互動式人工對圖裁定時，可以用 `scripts/review.py` 接上 `jyut-ocr-bench` 共用介面；圖片、題目同 append-only 答案歷史留喺相應 project。未裝介面時仍可完成一般校對並保存疑點，唔以缺少介面為理由丟棄未定讀法。詳見 [人工標註工具](references/manual-review-tool.md)。

## 維護

```bash
python3 -m unittest discover -s tests -v
```

測試以合成資料及 fake runtimes 驗證契約；實際 OCR 引擎 smoke、平台原生技能發現，以及當前模型讀技能／核原圖嘅操作驗證分開記錄。成功列出技能唔等於模型已核圖；未實機驗證嘅平台唔標成已通過。版本歷史只維護喺 [CHANGELOG.md](CHANGELOG.md)。GJ.cool 係可選底稿來源；已有 token 可用 `GJCOOL_ACCESS_TOKEN`／`GJCOOL_AUTH`，唔將真 token 提交入 repo。

```text
SKILL.md             # 通用技能入口
install.sh           # 安裝／環境檢查入口
scripts/             # 安裝、OCR、packet、校對及驗證工具
references/          # 按任務需要載入嘅規則
agents/              # 可選平台展示 metadata
adapters/            # 可選平台 custom-agent 設定
tests/              # 合成回歸測試
```

## License

MIT
