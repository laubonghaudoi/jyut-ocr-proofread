# Subagent 分工同 packet 答題契約

A 套（當前 agent 校對）答 packet bundle、分工畀 subagent，或者人手分段轉錄要獨立覆核嗰陣讀呢份。佢補充 `SKILL.md`；文字依據始終係 PDF 原圖。Custom agent 註冊係可選：邏輯角色可以交畀接收完整 assignment 嘅普通 worker；平台冇子 agent 功能，就由主 session 順序做晒 writer 同 packet 工作。

B 套（本機模型校對）唔用 packet：題目由腳本經 http 問本機模型，agent 唔睇圖，亦唔叫 subagent 代做（`SKILL.md` 第一節）。

調用技能嗰個 session 嘅模型負責 A 套嘅校對同最後內容判斷。Writer 同 auditor 跟住 session 實際生效嘅模型設定，除非用戶或者平台明確 override；技能唔鎖 model ID。揀 OCR 引擎唔會揀另一個校對模型。

## 目錄

- [能力檢查](#能力檢查)
- [兩種並行](#兩種並行)
- [Packet bundle 點答](#packet-bundle-點答)
- [Assignment 要交代乜](#assignment-要交代乜)
- [分段 writer 嘅流程](#分段-writer-嘅流程)
- [獨立覆核嘅流程](#獨立覆核嘅流程)
- [主 agent 收尾](#主-agent-收尾)

## 能力檢查

答題嘅模型要睇得到 bundle 指定嘅原頁圖同裁圖。淨係讀到文字嘅 worker 要將題目原封退返，唔可以由 OCR 文字估一個睇圖嘅判斷。檔案存取、執行腳本同需要嘅 audit 能力，都要先確認，先可以話完成。

並行係可選。一個 writer 可以順序做晒所有頁段或者 bundle，擁有權、coverage 同封存規則照舊。同一個 writer 讀多次係自我覆核，唔係獨立覆核。Profile 或者項目要求獨立覆核，就將封好嘅 candidate 交畀另一個唯讀 session 嘅 reviewer，或者有唯讀權限嘅獨立人員；未有之前保留 candidate 同明確 pending 嘅 audit，唔靜靜降低要求。平台嘅角色名、調用工具同 sandbox 語法放喺可選嘅 `adapters/`，唔放喺邏輯角色契約。

## 兩種並行

- **LLM subagent**：答 bundle、分段轉錄或者覆核有範圍嘅頁。只有主 agent 可以開同協調；子 agent 唔准再開子 agent。
- **OCR 程序**：主 agent 跑 `ocr_book.py`（HunyuanOCR、PaddleOCR-VL、補讀）。Apple Vision 用獨立嘅 bounded 本機 process pool，唔係 LLM agent。OCR 程序冇推理設定，底稿永遠唔係文字依據。唔好用 LLM slot 去包相同嘅 OCR 程序；子 agent 唔准自己開 OCR 或者停共用推理服務（見 `local-ocr.md`）。
- **答 packet bundle 嘅子 agent**：照 `scripts/packet_dispatch.py` 嘅派工單，有幾多份 assignment 就開幾多個，一次過開晒，但永遠唔超過平台實際有嘅 slot（用 `--max-workers` 講畀派工單知）。撞到限流就減少並行、按服務要求冷卻，最少 60 秒冇再限流先逐步加返；未答嘅包再跑派工單就會再列出嚟。
- **一批書嘅答題者**（`ocr_batch.py --round`，SKILL.md 四〈幾個 PDF 一齊做〉）：唔使自己跑派工單，佢將所有書等緊嘅 bundle 夾埋，寫成 `BATCH/plans/round-NN/worker-KK.txt`，每個檔就係一個答題者嘅成段指示（同派工單嘅 `prompt` 一樣）。同一時間最多 `--max-workers` 個，每個攞幾多題按答題模型定（下面〈每個答題者攞幾多〉）；一輪派唔晒嘅下一輪再派，唔好叫一個答題者讀幾個檔。
- **每個答題者攞幾多**：答題者每開一個都有固定開銷，所以一份越大，每條題越平越快，預設一份最多 10 條題（技能量過準確度嘅最大份量）。例外係 prompt 過咗某個大細就加價嘅模型（`packet_dispatch.py` 嘅 `PRICE_STEPS`）：一份要細到答題者嘅 prompt 留喺條線以下。`ocr_batch.py --round` 按 `--answered-by` 自己做晒。單本書自己跑派工單嗰陣：派工單加 `--answered-by MODEL`，佢就按估計 token 裝每一份，一次最多 `--max-workers` 份，裝唔晒嘅下次再派；但 bundle 唔會拆開，所以呢類模型行校對嗰步要加 `--max-items-per-bundle 1`，否則一個 bundle 已經大過一份。數字同估算方法喺 `references/long-book-workflow.md`〈每個答題者攞幾多題〉。
- **分段 writer 同 auditor**（人手分段轉錄）：同時運行嘅 LLM 子 agent 預設最多 8 個，用戶明確要求可以去到 16 個，由 2 個開始。唔好為咗填滿 slot 排重疊嘅 writer。

## Packet bundle 點答

兩條路線都出 packet：兩份真實底稿就先跑 `book_profile.py`，再將 profile 同兩個底稿目錄交畀 `proofread_pages.py`；淨係一份真實底稿就 `proofread_pages.py --single-engine`，唔使 profile。唔可以將一份底稿複製成假嘅第二引擎證據。題目寫成 `packets/wave-NN/bundle-XX.json`，裁圖喺 `packets/crops/`，腳本 exit `10`（`proofread_pages.py` 同時有頁 failed／blocked 就 exit `11`）。

1. **分工**：跑 `scripts/packet_dispatch.py BOOK/packets`（幾本書就列晒佢哋嘅 packets 目錄），照佢印嘅 `assignments` 每份開一個答題者，`prompt` 原文照貼；主 agent 自己順序做亦得。一份 assignment 係幾個相連嘅 bundle，大約 `--items-per-worker` 咁多題（預設 10）；份數唔多過 `--max-workers`（預設 24，設做平台同時開到嘅數）。答題者淨係讀 `references/packet-worker.md`：唔載入技能、唔跑 script、唔計 hash。實測（2026-11-17，細模型）：噉樣嘅答題者每題 6.4 秒、6.2 萬輸入 token；一案一個長 session 係 7.6 秒、9 萬。每個 bundle 仍然只有一個擁有者同一個輸出：bundle 嘅 `expected_answers_file`（`packets/wave-NN/answers-XX.json`）。`--max-bundles` 係題量容許時嘅分包目標（預設 8）。逐頁校對每包上限由 `--max-items-per-bundle` 設（預設 20，env `PROOFREAD_MAX_ITEMS_PER_BUNDLE`），profile 每包上限 20。題多過總容量就多開包。
2. **答題**：每條題睇佢 `image_path` 指嘅 PNG（裁圖，或者整頁 render），照佢自己嘅 `system` 同 `user_text` 寫答案，`answer` 係原原本本嘅答案文字。每條題都要答：圖睇唔清、或者定唔到，都唔係留空一題或者成包唔交嘅理由——照嗰題自己講嘅做法答，冇講就寫最有根據嘅讀法；腳本會逐個答案檢查，有需要會再問。常見嘅題：
   - `adjudicate`（裁決）：直接睇裁圖上標住嘅字位，照 prompt 嘅格式答（`字: ...`）；兩個候選都唔係就照實答。裁圖影唔到爭議嗰個位就寫 `定案：睇唔到`，唔好靠上下文估：估位裁出嚟嘅圖，腳本會喺下一個 wave 用成段字嘅大圖再問一次（題目會講明），仲係睇唔到先保留引擎嘅讀法、記低呢個位未睇到。有啲裁圖由幾段拼成（欄尾接下一欄頭），題目會講明。
   - `census+punctuate`（重試係 `punctuate-retry`、`punctuate-shape`）：喺放大嘅 block 圖數印咗嘅標點（census），再將嗰啲符號加入鎖定嘅字串（唔准加、刪、換任何字）。A 套預設一題做晒點算同加標點；分開問嘅 `census`、`punctuate` 係 B 套（http）嘅預設問法，見到就照各自嘅 prompt 答。
   - `table-rebuild`（及 `-retry`）：由裁圖重建表；用晒 `required` 嘅字，`allowed` 以外嘅字淨係真係印咗先寫。
   - `orient`、`record-order`、`cell-direction`、`notation`：照圖同現有格網答結構或者符號問題。讀向要有精確行／欄位置同圖像證據；讀得通嘅詞或者對得上嘅合計唔准作倒轉嘅理由。
   - `format`（及 `-retry`）：加 Markdown 結構（標題、段落、列表、表格），刪書邊嘅書眉同頁碼，每個鎖定嘅字、標點、原圖證實嘅數字／算式／項號同 `⟦Tn⟧` 表格記號都要原封保留；原印項號加 bullet 之後仍然係內容。
   - `writer`：淨係得一份底稿或者引擎讀唔到嘅頁：對住整頁原圖轉錄，底稿只係提示。
   - `coverage-read`：盲讀一張裁圖，用嚟證實裁決答「無」嘅位係咪真係冇字。
   - `note-numbers`（issue #8）：睇完整原頁，逐條頁底註答佢印嘅編號（圈入面兩三位數照寫阿拉伯數字，例如 72、240）、條註開頭幾個字、同正文入面佢個註號前面緊貼嘅幾個字，照 JSON 契約答；睇唔清嘅編號唔好寫，唔好由前後編號推斷。淨係規則配唔到註號嘅頁先會問。
   - `page-inventory`、`content-recovery`、`coverage-review`（淨係 `--content-fidelity on`）：睇完整原頁，照 JSON 契約答；兩份底稿一致唔係完整嘅證據；唔可以由註文或者連號推斷漏咗嘅註號。見 `content-fidelity.md`。
   - 任何 `-correction` 題：腳本話上一個答案唔合契約（欄位、hash 抄錯），照佢講嘅改一次。
3. **寫答案**：`{"answers": [{"request_id": "...", "answer": "..."}]}`，每個 `request_id` 一次。`usage`、`response_id` 淨係寫量度到嘅；冇就唔寫（記做未知，唔當零）。可以寫 `answered_by`（答題 model 嘅確實 ID）。Worker 淨係寫自己嗰份 answers、報答咗幾多題，然後即刻**停寫**（STOP-WRITE）；唔准掂 `packets/answers.jsonl`、`page-NNNN.md`、`page-NNNN.json`。答案齊唔齊由 `packet_dispatch.py` 再跑一次睇（未齊嘅包會再列出嚟），內容由 `apply_packet_answers.py` 驗；用註冊咗嘅 writer 角色先要另外報 SHA-256。
4. **交答案**：答晒成個 wave，主 agent 跑 `[packets] PENDING` 行（或者 wave 嘅 `manifest.json`）列出嘅 `apply_packet_answers.py PACKETS_DIR WAVE_DIR`，加 `--answered-by MODEL`（答題模型嘅確實 ID；run report 嘅 `packetAnsweredBy` 按模型數，用嚟核全書係咪同一個模型答）。佢驗證後原子追加入 `answers.jsonl`。之後重跑同一條步驟命令：腳本重播答案，跑晒決定論守門（`verify_characters`、`restore_merged_characters`、`check_rebuilt_table`、`punctuation_pass_ok`、`deleted_runs`），出下一個 wave，或者封 `page-NNNN.md` 同 `page-NNNN.json`。直到 exit `0` 先去下一步。
5. **exit 11**：照答個 wave；另外 run report 嘅 `pages` 有 failed／blocked 嘅頁，重跑修唔好佢哋，要按原因處理（例如補底稿之後 `--overwrite --pages N`）。

**短文件合併跑法（雙引擎，可選）**：由主 agent 只讀 wave manifest 嘅分工資料，逐包派一個冇前文嘅新 worker。每包只派一次，等佢停寫並驗 hash 先 apply。Worker 只載入自己嗰包，有批量開圖能力就一次過開指定 PNG，視覺核對晒先寫一份答案。可用 `--max-bundles 1` 減少細 wave 嘅包數。先驗 worker 嘅讀圖能力同實際 effort。只有 worker 保持指定模型同 effort 時，先調低主 agent 嘅 effort。用主 session 作答嗰陣沿用原本嘅答題模型同 effort。

**量度**：設定 `JYUT_OCR_PACKET_EVENTS` 指向 packets 目錄外嘅 JSONL 檔（父目錄要先存在），`JYUT_OCR_PACKET_CASE` 可選填個案 ID。每行係發包、apply、resume 或封存事件，有 stage、invocation ID 同時間。重播同一個 wave 會再記一次 emit，按 stage 同 wave 分辨。用原生 session log 核 worker 啟停、模型、實際 effort 同用量。Sidecar 只供分析，續跑按原有 manifest、答案同封條。

規矩：

- Wave 唔會改：重播同一個 wave 用返同一啲檔同題目。同一個答案可以再用；同一題唔同嘅答案或者用量會被拒（exit 2），除非主 agent 用 `apply_packet_answers.py ... --amend`（可以加 `--reason`）：新紀錄寫明佢取代邊個答案嘅 SHA-256，舊紀錄留喺 `answers.jsonl` 做審計；已經用舊答案封好嘅頁，要對 apply 印出嘅頁加 `--overwrite --pages` 重做先會用新答案。
- 重播唔再計費；逐頁 usage 計返答案記錄嘅用量，每個答案計一次。
- 整頁清點、排版前補讀同最後覆核（開咗 v2 先有）係有界嘅步驟，唔係無限改錯：契約錯誤每條最多改一次，定唔到嘅記疑點。
- 題目綁定圖、prompt 同影響答案嘅設定；新圖、新候選或者新契約要新答案。舊 v1 證據淨係歷史讀取。
- 唔好為咗答題以外嘅理由逐頁覆核封存頁；bundle 冇問嘅位，定唔到嘅已經喺 `doubts.md`。

## Assignment 要交代乜

（人手分段轉錄同獨立覆核用；packet worker 淨係要上面第 1 點。）主 agent 擁有頁段劃分同最後組裝。開子 agent 之前畀佢一份完整 assignment：

1. 唯一 assignment ID，同一個邏輯角色：分段 writer、packet worker 或者獨立覆核者；唔使平台註冊角色名；
2. 來源 PDF 嘅確實路徑、SHA-256 同範圍內 scan 數；
3. 佢擁有嘅 scan 範圍或者 scan 集，加淨係可以讀、唔可以寫嘅前後 context scan；
4. 每個 scan 對應嘅個別原圖／全解像度 render、目前 render SHA-256、臨時 page marker，同有就寫嘅印刷頁碼；
5. writer 嘅唯一輸出路徑；覆核者就係 candidate 路徑同封好嘅 SHA-256；
6. 所揀嘅 `basic` 或者 `structured` profile、本地 repo 規則、notation policy、已知更正，同風險標記（表格、旋轉 spread、重複、缺頁、淡字、註腳、書眉、未定邊界）；
7. 要回報嘅欄位，同要讀嘅 project-local manifest 或者證據路徑。

缺欄位、來源／render hash 對唔上、輸出路徑屬於另一個 writer，或者擁有嘅 scan 同另一個 active writer 重疊，就唔開工，將衝突報返主 agent。

## 分段 writer 嘅流程

（`structured-workflow.md` 嘅人手分段轉錄。）Writer 淨係擁有自己嗰段同 scan 範圍。

1. 讀底稿或者改字之前先驗來源同 render hash。HunyuanOCR、PaddleOCR-VL、Apple Vision、GJ.cool、Gemini 同其他 OCR 都只係提示；可選引擎冇讀嘅頁唔係空白讀法。
2. 讀 context scan 判斷接續、標題、列表、表格同註腳，但唔將佢哋轉錄入第二段，亦唔改佢哋 owner 嘅檔。
3. 淨係寫自己嗰段輸出。永遠唔改最後組裝嘅 Markdown、另一段、page／render manifest，或者主 agent 擁有嘅覆核證據。
4. 每個擁有嘅 scan 都對住佢個別嘅原圖／全解像度 render 核。保留臨時 page marker，報告每個未定嘅字、缺／重複 scan 同邊界決定。
5. 寫完計 SHA-256，交返 candidate seal：assignment ID、路徑、SHA-256、擁有嘅 scan 集、marker coverage、未解決嘅發現同邊界備註。
6. 交咗 seal 即刻**停寫**（STOP-WRITE）：連細嘅清理都唔做，唔因覆核意見自己補，亦唔刷新 hash，除非主 agent 明確開新嘅修訂 assignment。

有底稿就保留佢哋嘅檔案身份，將底稿→candidate 嘅實質文字差異交畀覆核做優先次序；唔可以淨做 diff 代替全頁覆核。對齊唔唯一就係 block 級風險，唔係精確字位。未定讀法同可修嘅錯分開記，附足夠上下文分辨第幾次出現。

封好之後任何 byte 改動都令嗰份 candidate 同基於佢嘅覆核作廢。主 agent 要指定一個 writer、出新 SHA-256 seal，再將受影響 scan 同邊界 context 交畀另一個覆核者。

## 獨立覆核嘅流程

覆核者一定唔係 writer，而且係唯讀：可以係獨立人員，或者另一個 agent／session。佢唔准改 candidate、用 shell redirection 改，亦唔准出替代嘅段。

派之前確認覆核者對封好嘅 candidate 同來源證據真係淨係有唯讀權限；自動覆核者要驗實際權限，唔可以靠 prompt 或者 adapter 聲明，更大嘅 runtime override 可以令隔離失效。平台做唔到，就交另一個唯讀 session 或者獨立人員，期間保持 pending。覆核者將發現交返主 agent，由主 agent 保存證據。

1. 重新計 candidate 嘅 SHA-256，同 writer 嘅 seal 唔同就拒絕。
2. 驗來源同 render hash，再用個別原圖／全解像度 render 覆核 100% 指定 scan；contact sheet、縮圖同 OCR 輸出淨係導航用。
3. Context scan 淨係做上下文，唔當自己擁有，亦唔重複計 coverage。
4. 核文字、原字形同標點、頁面家具、marker coverage 同次序、跨頁接續、標題、列表、表格、註腳，同 assignment 列明嘅每個風險。
5. 交返封好嘅 SHA-256、確實核過嘅 scan 集、context scan 集、逐 scan 嘅證據處理、邊界結論，同明確嘅未解決清單。指定 scan 100% 覆核而且未解決係零先算通過。

喺佢講明嘅位置逐個解決交過嚟嘅疑點：話「呢頁核過」唔會逐個解決，核咗一個字亦唔會解決成句。筆畫睇唔清而保留最有根據讀法嘅，係來源限制，同可修嘅錯分開報。交畀人或者對返人工裁定見 `human-review-feedback.md`。

Candidate 要改，就報發現然後停；唯讀覆核永遠唔會自己變成 writer。

## 主 agent 收尾

組裝之前，主 agent 確認：

- 各 writer 擁有嘅 scan 集互斥，加埋覆蓋預定範圍；
- 每段目前嘅 bytes 同 writer 嘅 seal 一致；
- 要求獨立覆核嘅，每段都有不同覆核者嘅通過結果，100% 擁有 scan 同所需邊界 context 都有交代；自我覆核另外記，唔可以頂替；
- 冇 writer 仲 active 或者可能再改封好嘅段；
- 跨段邊界、marker 次序、結構同未解決發現都對好。

淨係主 agent 組裝最後 Markdown，之後跑適用嘅決定論檢查。遲來嘅改動令受影響嘅 seal 同覆核作廢，嗰段返去修訂流程；唔可以靜靜改已經接受咗嘅 candidate。
