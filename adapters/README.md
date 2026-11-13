# 可選平台 adapters

核心 `SKILL.md` 同 `references/subagent-orchestration.md` 只描述讀圖、檔案、腳本能力，以及 writer、packet worker、independent auditor 嘅職責。平台工具名稱、custom-agent 註冊、模型繼承及權限語法放喺呢一層。冇 adapters 時，用普通 worker 接受完整 assignment，或者由主 agent 順序寫稿／回答 packets。

目前提供 Codex TOML 同 Claude Code Markdown writer／auditor 角色。透過 `./install.sh --agent codex --with-agents` 或 `--agent claude --with-agents` 安裝；唔加 `--with-agents` 就只裝技能。`agents/openai.yaml` 只係可選展示 metadata，唔係其他 agent 執行技能嘅前提。

Codex adapters 省略 `model`／`model_reasoning_effort`，沿用平台有效解析結果；Claude adapters 用 `model: inherit`。使用者或平台嘅更高優先權 override 仍然有效，主 agent 要如實交代，唔假稱必然同模型。技能唔鎖固定 model ID。

Codex custom-agent 敏感檔案讀取曾拒絕 leaf symlink，所以安裝器用 byte-for-byte、SHA-256／mode 封存嘅 regular-file projection；Claude agent 檔同 skill 用 exact symlink。改 adapter 後用安裝器 `--check` 檢查，再重新安裝刷新 projection。Repo 仍係唯一 canonical source。

Auditor 角色分別宣告 Codex `sandbox_mode = "read-only"`，或者 Claude `permissionMode: plan` 並移除直接 write tools。**宣告唔等於實際隔離**：平台版本、parent bypass 或其他 runtime override 可能令佢失效。派獨立只讀審核前要驗有效權限；唔可以只靠 prompt「唔好寫入」聲稱 hard read-only。若無法提供隔離，保留 sealed candidate 同 pending audit，轉交另一個有只讀存取權嘅 session 或獨立人員。

其他平台可以用自己支援嘅普通 worker／子 agent 功能，按通用 assignment contract 交代來源、頁段、hash、輸出及職責。平台無子 agent 時，主 agent 可以順序完成全部 writer／packet 任務；需要獨立審核嘅工作仍要不同 reviewer，同一 writer 再讀一次唔會變成獨立 auditor。
