# 更新摘要

## Unreleased — 實戰校對陷阱收斂：HTML 表格結構審查、final 模式七項覆核警告、數位倉儲尾頁候選同校勘規則補充

- 背景：逐頁對照 37 冊（逾 6,500 頁）晚清至民國掃描文獻審核雙引擎機校稿（`machine` → `proofread`），反覆見到八類跨書系統性錯漏：多處 `# ` 氾濫與標題跳級、影子圖書館英文／JSON 元數據尾頁混入正文、直排內嵌西文漏讀成空括號或 `口口`／`□□`、無框線固定欄位名錄表跨頁結構分裂與 HTML `<table>` 未驗欄寬、跨頁接縫書眉頁碼黏入句中、孤立 `〇` 誤代句號 `。` 與行側著重密點誤生句號串、雙引擎同段局部重影衍文，以及鉛字 180° 倒植與原印跳頁號誤判缺頁。
- `scripts/audit_ocr_markdown.py`：
  - 接入 `jyut_ocr.core.tables`，對保護區與 code fence 以外嘅 HTML `<table>` 檢查標籤配平（`unclosed-html-table`）同計入 `rowspan`／`colspan` 後嘅每行欄寬一致性（`html-table-column-mismatch`），列為 `error`（實測 11 冊已精校書共 28 張 HTML 表零誤報）。
  - 於 `--mode final` 新增七項高信噪比 `warning`（唔改動 `AUDIT-FINAL` error 計數）：`multiple-h1`、`heading-level-jump`、`empty-parentheses`（豁免全形表式留空 `（　）`）、`suspicious-circle-punctuation`（非數字語境孤立 `〇` 或連續四字以上逐字句號串）、`suspicious-kou-run`（豁免「口口聲聲」等真詞）、`adjacent-duplicate-span`（同段緊接重出 18 字以上漢字）及 `repository-metadata-tail`。實測：11 冊精校完本（2,073 頁）中 9 冊零警告、僅 2 處單發真疑點；8 冊未精校機校書則全數精準命中多處 `# `（7–60 處）、孤立 `〇`（27–42 處）、密點句號串（247–289 處）、同段重影（2–13 處）及元數據尾頁。
- `scripts/survey_pdf.py`：新增 `detect_boilerplate_pages`，用 `pdftotext` 檢查首尾各兩頁是否附有現代數位倉儲元數據文字層，記入 `survey-report.json` 嘅 `boilerplate_pages` 候選（唔靜默刪頁，交視覺確認）。
- `references/`：於 `common-ocr-traps.md`、`jyut-ocr-detailed-rules.md`、`packet-worker.md` 同 `structured-workflow.md` 補入上述泛化規則同多代理分片增量存盤 checkpoint／接縫核驗守則。
- 測試：`tests/test_ocr_markdown_scripts.py` 新增三組單元測試覆蓋 HTML 表格檢查、七項 final warning 同豁免例，以及 `survey_pdf` 尾頁偵測。

## Unreleased — 表格冇重建到唔再靜靜雞過：`--build-refused-tables`；表入面嘅標點唔理全形半形（issue #10）

- 背景：引擎寫咗表格、但格仔俾 script 否決嘅 block（合併格冇第二個來源證實、格入面有斷行），係交畀結構步（排版題）對住頁圖重建。兩個問題令個表成日冇咗：
  1. 答案完全冇寫表，照樣過晒檢查，頁面就封咗：個表變成一段冇數字嘅字，疑問清單乜都冇。
  2. 答案建咗表，但因為表入面一個標點俾拒絕：引擎 A 寫「：」、引擎 B 寫 `:`，半形嗰個唔算標點，所以「兩個引擎都讀到嘅標點」冇計佢。
- `proofread_pages.py` 加 `--build-refused-tables`（預設關；`ocr_batch.py` 對新書會加）：答案冇多寫一個表，就再問一次（`format-retry`，有自己嘅題目文字 `TABLE_NOT_BUILT_RETRY`）；再問嘅答案有表又過到檢查就用，否則留返第一個答案，並記新疑問 `table-not-built`（`finish_book.py` 喺 `doubts.md` 嘅表格一節列出）。淨係引擎嗰段 markup 本身係表（`refused_grid`：兩行有字、兩欄或以上）先會再問；封條記 `buildRefusedTables`，同時 `packet_request_config` 唔計呢個開關，保持既有題目 `request_id` 唔變。
- `--same-marks-any-form` 而家亦用喺呢度：計「兩個引擎都讀到嘅表內標點」之前，先將兩個引擎嘅寫法統一（`FORM_FOLD`），頁面寫全形。
- 量到嘅效果（一本現代簡體橫排書兩頁有表嘅頁；每條題各搵 12 個 Haiku 答，6 個 app 入面開、6 個 command line 開，答案全部放入流程核對）：

  | | 之前 | 而家 |
  | --- | --- | --- |
  | 第一次問，表留喺頁面（兩頁各 12 個答案） | 2 個、9 個 | 9 個、9 個 |
  | 第一次問冇寫表嘅答案 | 每頁 3 個，靜靜雞封頁 | 每頁 3 個，全部再問 |
  | 再問之後表留喺頁面（24 個答案） | 唔會再問 | 23 個 |

  定稿：表格由 3 個變 5 個，格入面嘅數字（6.59、87.4% 呢類）返晒嚟。
- 把關（用舊答案重播，冇答題波動）：28 案 50 頁輸出完全一樣、冇多問一條題，所以七項指標冇變；160 頁樣本淨係多咗嗰兩頁嘅兩條題，其餘 158 頁一樣。
- 冇解決嘅：答案有表但行列排錯（28 案入面嗰個直排單頁表格案，同一條題答 8 次，表格內容 F1 由 0 到 89）。呢個開關淨係管「冇表」，唔管「表錯」。
- 測試（`tests/check_tables.py`，新 2 組）：冇開關就同以前一樣；再問有表就用；再問冇表或者俾檢查拒絕，第一個答案照留、有疑問；一行嘅「表」唔會再問；`packet_request_config` 開關前後一致；半形冒號喺開咗 `--same-marks-any-form` 先當兩個引擎都讀到，淨係一個引擎讀到嘅照舊唔准。

## Unreleased — HunyuanOCR：死過一次嘅 llama-server 熄咗 CUDA graphs 先重開

- 問題（M1，llama.cpp 391fac1）：查清咗上一項嘅 abort 點嚟。一個服務讀兩種大小差好遠嘅頁，例如 MH1408 嘅 5306×7643 同 MH1442 嘅 1410×2079，就會 abort；兩本書各自讀（1 個 slot 或者 16 個）都冇事，兩本嘅頁交叉送入同一個服務，76 頁一頁都讀唔到。熄咗 llama.cpp 嘅 CUDA graphs（`GGML_CUDA_DISABLE_GRAPHS=1`）就兩次都讀齊。CUDA graphs 係將上一頁 GPU 做過嘅步驟錄低再重播嘅加速；熄咗喺一個細測試慢大約 15–20%（MH1442，40 頁，16 個 slot：64 秒對 55 秒，連開服務），40 頁讀出嚟有 38 頁一模一樣，兩頁差一個字。單係重開救唔到：M1 上面重開三次都喺幾秒內再 abort。
- 改動：`HunyuanServer.recover`：死咗之後第一次重開就設 `GGML_CUDA_DISABLE_GRAPHS=1`，嗰個服務之後一直熄住；預設照開。用戶自己設咗就唔郁。
- 測試：`tests/test_hunyuan_shared.py` 加一個：假服務開住 graphs 讀到某頁就死、熄咗就讀得到；兩個 client 四頁全部讀到，第二次開服務嗰陣 graphs 熄咗。dedicated 嘅例子亦檢查第二次開係熄咗。改之前唔過。

## Unreleased — HunyuanOCR：llama-server 中途死咗就重新開，冇答案嘅頁再讀

- 問題（M1 大批 OCR，llama.cpp b10969、H200）：llama-server 間中喺讀圖嗰步 abort（「CUDA error: an illegal instruction was encountered」，backtrace 喺 `clip_image_batch_encode`），兩個節點都有。Shared broker 一見服務死咗就連自己一齊收工，嗰條線上面兩本書未讀嘅頁全部 failed：MH1408（1,155 頁）同 MH1442（40 頁），driver 重試兩次都喺服務開咗 7 至 20 秒之後再 abort，兩本都失敗。嗰兩本可疑嘅頁（MH1408 第 125–160 頁、MH1442 全本）喺另一張卡上面 1 個 slot 同 16 個 slot 單獨讀都冇事，所以唔係頁嘅問題。
- 改動：`run_hunyuanocr.py` 嘅 `HunyuanServer`：自己開嘅服務死咗（或者唔答又唔走，等 `CRASH_WAIT` 15 秒），就喺同一個 port 同 GPU lease 下重開，請求再送一次；600 秒內最多 3 次（`MAX_RESTARTS`、`RESTART_WINDOW`），之後照舊報失敗。服務仲答緊而嗰頁自己失敗（例如 HTTP 500）就唔重開。`_hunyuan_shared.py`：broker 見服務死咗先試重開，重開唔到先收工。Dedicated runner 用同一個 class，一樣會重開。外部 `--endpoint` 唔郁。
- 測試：`tests/test_hunyuan_shared.py` 加四個：假服務讀到某頁就即刻死，兩個 client 共四頁全部讀到、服務開咗兩次；一直死嘅服務開到第四次就放棄，嗰頁失敗；服務仲答緊嘅失敗頁唔重開；dedicated 嘅 `HunyuanServer` 一樣重開。改之前三個唔過。

## Unreleased — 共用 HunyuanOCR：答案送出之前先放返個位（`_hunyuan_shared.py`）

- 問題（M1 大批 OCR，每條線兩本書共用一個 Hunyuan 服務，16 個 slot，每個 client 16 個 worker）：broker 送出一頁嘅答案之後，先將嗰個 membership 未完成嘅頁數減一。client 一收到答案就即刻送下一頁，broker 有時仲未減，就拒絕新嗰頁（「too many outstanding pages for one shared OCR membership」）。嗰頁當 failed，成本書嘅 Hunyuan exit 1（MH0925：870 頁有 1 頁）。續跑淨係重讀嗰頁，所以讀得返；冇人續跑，本書就停喺度。
- 改動：`_Broker._page` 先清走個請求同減數，再送答案；等答案嘅部分搬咗去新嘅 `_await_page`，三種答案（讀到、過咗期限、服務停咗）都喺放返個位之後先送。
- 測試：`tests/test_hunyuan_shared.py` 加 `PageSlotTest`：一個 slot，client 一收到答案就送下一頁，兩頁都要讀到；改之前第二頁被拒，錯誤同 M1 一樣。

## Unreleased — PaddleOCR-VL 用 GPU 可以排自己一條隊（`OCR_PADDLE_GPU_RESOURCE`）

- 問題（M1，H200）：兩個引擎排同一個 GPU 隊 resource（`OCR_GPU_RESOURCE`），一本書行 Paddle 嗰陣，同一張卡嘅 Hunyuan 要停低等佢。但 Hunyuan 16 個 slot 得 28 GB，Paddle 嘅 VL 識別又交咗外部伺服器，本卡淨係版面偵測，張卡其實夠位兩個一齊行。
- 改動：`run_paddleocr_vl.py`：設咗 `OCR_PADDLE_GPU_RESOURCE`，Paddle 用 GPU 就排呢個 resource，唔再同 Hunyuan 爭；`--gpu-resource` 照樣蓋過佢。冇設就同以前一樣。`references/local-ocr.md` 講幾時先好設（張卡要裝得落兩個引擎），`references/cluster-bulk-ocr.md` 加入配方。
- 實測：M1 一個 OCR 節點（7 條線），每條線同時兩本書，14 本 4,420 頁 OCR 37.2 分鐘，每粒鐘 7,124 頁；之前一條隊嗰陣約 6,300 至 6,700 頁，快咗約 9%。
- 測試：`tests/test_local_ocr_runners.py` 加一個例：Hunyuan 佔住張卡嗰陣，用自己 resource 嘅 Paddle 照行；第二個 Paddle 等第一個。

## Unreleased — `--answerer-command`：用真模型量過；唔完嘅答題者會停（issue #7）

- `ocr_batch.py`：script 自己開嘅答題者，過咗 `--answer-grace` 仲行緊又未寫齊答案，先停咗佢（連埋佢開嘅 process）再派嗰份，當一次失敗，log 最尾寫明。寫齊答案之後 60 秒（`ENDING_SECONDS`）仲未完嘅 process 一樣停，成批完咗就即刻停；答案照計。停之前會認返個 process 係唔係當初開嗰個（開始時間），唔會停錯同號嘅另一個 process。
- 點解：28 案評測 288 個 command line 答題者有 1 個企咗 13 分鐘；160 頁樣本 201 個有 5 個寫齊答案、印咗結果但 process 冇自己完，其餘全部喺寫完最後一個答案檔 15 秒內完。
- **樣本實跑**（Haiku 5.5，`claude -p`，8 冊現代簡體橫排書各 20 頁，同之前一樣嘅 OCR 稿，`--share-tokens 50000`）：1,101 條題、201 個答題者，由第一次派題到 8 本交齊 17.3 分鐘，`--round` 跑咗兩次；有題排隊嗰陣 20 個位一直用滿，每分鐘 88.5 條。1,243 個請求冇一個過 10 萬線（答題者由 7,600 token 開始，最大 91,209）。答題成本 $3.36，每條題 0.30 美仙；主 agent 嘅成本得返兩次 call。
- 對比之前由 agent 開答題者嗰次（同一批頁，嗰陣未有 `--same-marks-any-form`）：1,615 條題、384 個答題者、46 分鐘、264 次 `--round`，答題者 $14.70 加主 agent $7.39，每條題 1.37 美仙。每頁成本由 13.8 美仙變 2.1 美仙，時間係之前嘅 38%；當中題數少咗 32% 係快路嘅功勞，其餘係開法。
- **28 案評測**（全新答案，同一個及格線）：CER 寬容 15.06%（app 入面開 15.03%）、不計標點 15.15%（15.13%）、逐案平均 9.89%（10.27%）、註釋 F1 68.4（27.6，嚟自 #8 嘅改動）。表格結構 81.9（89.2）同表格內容 F1 76.8（84.1）低過及格線：七個表格案有五個一樣或者高咗，差喺兩案，兩案嘅表格分數都跟住排版題嘅答案變：一案嘅表淨係靠排版題由答題者對住圖重建；另一案有專門嘅重建題（`table-rebuild`），但排版題一變，分數照樣跳。
- 兩案同樣本入面兩頁表格，同一批題交畀兩種開法各答幾次（共 28 個答題者）：一案 app 開嘅四次有三次 0 分、command line 開嘅四次都有分；一案兩邊都係兩次 23、兩次 6；樣本兩頁，app 開嘅 6 個有 5 個重建到，command line 6 個有 4 個。表格重建唔穩定係答題模型嘅問題，兩種開法都有；及格線入面嗰兩項由一兩個答題者嘅答案決定。
- `--allowedTools` 淨係畀 `Bash(python3:*)` 嗰次，答題者好多 command 俾拒絕，CER 寬容 15.25%，過咗線；文件同 `env.sh.example` 嘅例子改為成個 `Bash` 放行（量到上面嗰組數嘅 command），並講明放行幾多由用戶決定。
- 樣本定稿同之前嗰次對比（冇標準答案，淨係睇差異）：之前嗰次卷六有一段 15,563 個「□」嘅亂碼，今次冇；一個答題者冇重建兩頁表格（見上）；一頁頁尾多咗一個句號，令跨頁嗰句冇接返；方括號 856 個變 890 個。
- 測試（`tests/test_ocr_batch.py`，新 2 個）：企住唔答嘅答題者同佢開嘅 process 都停咗，三個之後 exit 5；寫齊答案但唔完嘅，答案照計、書照交付、process 停晒。
- 未做（搬咗去 #10）：份量估算重新校準（中位數準，最差嗰份實際係估算嘅 1.77 倍）；`claude -p` 嘅 cache 寫入係 1 小時嗰種（貴一倍），佔答題成本一半，未試過改短；快路由 book profile 開關同抽查；排版題重建表格唔穩定。

## Unreleased — 批量：答題者可以由 script 自己開（`--answerer-command`，issue #7）

- `ocr_batch.py --round --answerer-command CMD`（或者用戶喺 `env.sh` 設 `OCR_BATCH_ANSWERER_COMMAND`）：`--round` 自己為每一份題行一次 CMD（獨立 process），答案寫好就補派，唔再以 exit 10 叫 agent 開答題者；agent 淨係喺 exit 75 再跑同一條。冇設就同以前一樣。
- 點解：160 頁樣本實跑量到，主 agent 開答題者嗰陣每個答題者差唔多行一個請求，個個重讀成個 context（平均 45 萬 token），由第 7 個請求起一直喺嗰個模型貴五倍嘅線以上：每個答題者 1.9 美仙，係答題者本身成本嘅一半。呢個係結構問題，調等候時間改唔到。
- Process 完咗而冇寫齊答案，嗰份即刻再派（唔使等 `--answer-grace`）；連續 3 個都冇答、中間冇一個答到，嗰個 call 以新嘅 exit 5 完，印條 command 最尾幾行。條 command 開唔到（冇呢個程式）係 exit 2。
- `SKILL.md`：設咗嗰陣 agent 唔好自己開答題者；exit 5 照實話畀用戶知。`env.sh.example` 有例子。
- 測試（`tests/test_ocr_batch.py`，3 個，替身答題者）：兩本書由頭到交付都冇出過 exit 10、每一份一個 process；唔答嘅 command 以 exit 5 完而且之後接得返；開唔到嘅 command 即刻講。
- **未用真模型驗證**：寫嗰陣呢部機嘅 command line 未登入。用呢個方法答題嘅準確度、成本同速度都未量；要登入之後跑 28 案評測同一次樣本批先知。

## Unreleased — 現代印刷書少問標點：`--same-marks-any-form`；逐輪輸出精簡（issue #7）

- `proofread_pages.py` 加 `--same-marks-any-form`（預設關；`ocr_batch.py` 對新書會加）：兩個引擎喺一個 block 讀到同一批字，標點同印刷字淨係形式唔同，就當一致，照用引擎 A 嘅標點唔問。摺埋比較嘅形式：半形同全形嘅 `,` `:` `;` `?` `!`、直引號同彎引號、方括號嘅三種寫法（`[`、〔、【）、「☐」同「□」，同引擎 B 喺標題行頭寫嘅 `#`。尖括號唔摺。
- 取用嗰陣照引擎 A 嘅形式寫，連埋兩個引擎都讀到嘅引號、方括號、破折號同間隔號（`KEPT_PRINT`）：冇咗呢步，唔問嘅 block 會甩晒呢啲（一冊 15 頁上面，淨係寫標點嘅話方括號由 96 個剩 14 個）。數字、拉丁字、「□」同註號照舊由後面嘅規則寫。
- 得一處標點嘅 block：嗰頁其他取用咗嘅 block 合共有 20 處標點（`ANY_FORM_PAGE_PLACES`）就一齊取用。兩個引擎都冇標點嘅 block 照舊問：舊書上面呢類 block 62 個有 15 個其實有標點。
- 點解：160 頁現代簡體橫排書（8 冊各 20 頁）實跑，1,615 條題有 1,011 條係標點題；對到嘅 745 條入面，558 條兩個引擎本身已經一致（形式之外），當中 88% 答題者寫嘅同引擎一模一樣，其餘主要係答題者漏咗兩個引擎都讀到嘅方括號（97 處）。
- 量到嘅效果（同一批樣本，舊答案重播，文字有變嘅 36 頁重新答排版題）：校對題 1,375 條變 761 條（少 45%）；定稿 124,391 個字有 88 個唔同，【】由 322 個變 366 個，疑問 539 項變 490 項。
- 28 案評測（同一批答案重播，所以冇答題波動）：27 案輸出完全一樣；一案 CER 3.63% 變 2.81%；合計 CER 寬容 15.03% 變 15.01%，其餘六項一樣。舊書幾乎唔受影響：566 條題淨係少咗 13 條。
- `ocr_batch.py --round`：第二次派題開始淨係印兩行短嘅（邊幾個 worker 檔、RESULT），成段指示同 command 喺每個 caller 第一次、之後每 25 次、同帶一次性選項（`--answerers-gone`、`--retry-failed`、`--not-started`）嗰次先印。160 頁實跑量到，呢啲輸出每次 1,300 字，佔主 agent context 增長嘅 35%，而主 agent 每個請求都要重讀成個 context：佢嘅成本去到每條題 0.46 美仙，係答題者嘅一半。
- 同一次實跑量到 `c6e84fc` 嘅效果：題目排緊隊嗰陣 20 個位平均用到 18.2 個（之前 14.2），每分鐘答 38.8 條（之前同樣份量 27.5）；主 agent 冇因為成本自己停；384 個答題者有 1 個俾平台拒絕、之後重派。按 token 裝份量嘅估算喺現代書偏低（估 3.6 萬、實際 5.0 萬），45% 嘅答題者仍然過咗加價線，未改。
- 未做：快路仲未由 book profile 量度一致率嚟開關，亦未有抽查；有出入嘅 block 仍然成段問；排版題仍然每頁一條。
## Unreleased — PaddleOCR-VL 伺服器要用技能嘅 chat template（`runtime/paddleocr-vl-server-chat-template.jinja`）

- 問題（M1，SGLang 0.5.23 serve PaddleOCR-VL-1.6，`--vl-backend sglang-server`）：成條流程喺 M1 跑 28 個 bench 案，28 案 CER 23.00%、表格儲存格 F1 33.1（Mac 底稿 15.09%、83.4）。模型自己嘅 chat template 淨係喺 `{"type": "image"}` 放圖；PaddleX 送嘅係 `{"type": "image_url"}`，圖放錯位，答案頭一個 token 走樣：表格第一格冇咗 `<fcel>`，轉 HTML 嗰陣跌咗一格、成行錯位；文字 block 開頭多咗換行或者一個字（正文開頭多咗字或者表開頭冇咗「表」）。伺服器單獨答同一個請求、同時答幾百個都一樣，問題唔係並行。
- 改動：新檔 `runtime/paddleocr-vl-server-chat-template.jinja`：同模型嘅 template 一樣，淨係 `image_url` 都當圖。`references/local-ocr.md` 講伺服器一定要用佢（SGLang `--chat-template`），換 template 要改 `--vl-server-revision`。`paddleocr_vl_worker.py`：伺服器後端嘅 block 文字開頭嘅換行刪走（防護；冇用呢份 template 嗰陣先會出現）。
- 實測：換 template 之後底稿同 Mac 嘅 MLX 底稿逐頁相似度 0.977 至 1.0；28 案 CER 18.69%、儲存格 F1 82.3。仲差嘅主要係一份雙欄排印本嘅雙語書目頁（校對答案漏咗西文書目，修書補唔齊；974 錯，Mac 底稿三輪都係 110），未查清。
- 測試：`tests/test_paddle_vl_server.py` 加伺服器 block 開頭換行嘅兩個例。

## Unreleased — 新文件：叢集大批 OCR 嘅配方（`references/cluster-bulk-ocr.md`）

- 喺 H200 Slurm 叢集（Ubuntu 22.04、driver 570、冇 root）大批 OCR 嘅配方，2026-11-19 喺 M1 逐件量度：一個 OCR 節點（GPU 0 serve PaddleOCR-VL、GPU 1–3 三條 HunyuanOCR 線、GPU 4–7 兩個校對 replica）加其餘校對節點（每個 4 × TP2 + MTP 嘅 Qwen3.8-Flash-Next）。
- 記低每個引擎同校對模型每張卡嘅吞吐、點解校對要大部分 GPU（每頁 GPU 時間約係 OCR 嘅 13 倍）、`ocr_batch.py` 嘅 http 路線點解唔啱叢集、點樣將單本 command 並排行。成條流程頭尾嘅吞吐未量度，量完補。
- `SKILL.md` 十嘅參考清單加呢份。

## Unreleased — PaddleOCR-VL 可以交外部 VL 伺服器讀（sglang-server／vllm-server）

- 問題（M1 叢集 H200，Paddle 3.3.1 CUDA 12.6 wheel，driver 570）：native PaddleOCR-VL 生成卡死：版面偵測做完，VL 逐 token 生成嗰陣一個 CPU 核 100%、GPU 0%，一頁永遠讀唔完（worker 900 秒超時）。試過 `CUDA_LAUNCH_BLOCKING`、`CUDA_MODULE_LOADING=EAGER`、stream-safe allocator、naive allocator，全部冇用；喺 embedding 外面包一層 Python 有時會好、有時唔會，靠唔住。
- 改動：`run_paddleocr_vl.py --vl-backend sglang-server|vllm-server`，配 `--vl-server-url`、`--vl-server-model`、`--vl-server-revision`（必填）、`--vl-concurrency`（各有 `OCR_PADDLE_VL_*` 環境變數）。版面偵測照喺 `--device`；VL 識別交 PaddleX 嘅 genai client 送去伺服器。底稿身份記住後端、本機權重 SHA-256、模型名、部署版本同並行數；worker 載入後核實 URL、模型、並行數同版面裝置。伺服器由用戶開同管。
- 實測：M1 一張 H200 跑 SGLang 0.5.23 serve 固定版本嘅 PaddleOCR-VL-1.6，8 頁全部封好（skill 自己嘅 runner）；吞吐每粒鐘 2,258 頁（1 個版面 process）、6,476（4）、9,201（8）、11,341（16）。
- 文件：`references/local-ocr.md` 新一節。測試：新檔 `tests/test_paddle_vl_server.py`（身份、worker 環境、核實、參數）。全套測試失敗同改之前一樣。

## Unreleased — 安裝：預編嘅 llama.cpp 喺舊 Linux 行唔到，就用源碼建置同一個 commit

- 問題（M1 叢集，Ubuntu 22.04，glibc 2.35，driver 570）：安裝器淨睇 driver 揀預編嘅 `llama-b10969-bin-ubuntu-cuda-12.8-x64`，但呢個二進制要 glibc 2.38（`GLIBC_2.38`、`GLIBCXX_3.4.32`），`llama-server --version` 一行就失敗，安裝停喺度，冇行文件講嘅源碼後備。
- 改動：預編二進制核驗失敗而原因係載入器搵唔到 C 函式庫版本或者共享庫（`version ... not found`、`cannot open shared object file`），就印一行講缺乜，改行同一個 pinned commit 嘅源碼建置（要 `nvcc`、`cmake`、C++ 編譯器，同之前一樣）；其他核驗失敗（例如報錯 commit）照舊停低。新函數 `unrunnable_reason()`。
- 測試：`tests/test_setup_dual_engine.py` 加三個：行唔到嘅預編檔轉去源碼建置；行到但 commit 唔啱嘅照停；讀載入器訊息。成個檔嘅失敗同改之前一樣（11 個環境問題）。
- 實機：M1 一個 H200 節點（CUDA 12.8 toolkit、gcc 11、cmake 3.31）由源碼建置成功，HunyuanOCR 試跑通過。

## Unreleased — 修書 N9：正文註號同頁底註錯開一個就唔寫；頁面寫漏一位嘅註號寫返成個數

- 問題一（jyut-ocr-bench `doukeoi-zingkeoi/s0315-0318-table`，Qwen3.8-Flash-Next 3608580）：315 頁頁底嘅註係 193–196（193 嘅正文註號喺上一頁），正文註號係 194–198，兩個引擎讀每個正文註號都係 19，即係 193–197 一樣啱。3608580 嘅 note-numbers 答案將正文註號同頁底註按次序配，畀咗 193–196，N9 照寫，定稿每個註都配錯位；淨係第一頁變咗腳註，後面幾頁冇，CER 由 78 錯變 650。
- 改動一：正文註號嗰串數，如果頁面同引擎嘅讀法對前移一個或者後移一個都一樣啱（`_shifted`），N9 唔寫：模型答案係按次序配嘅，分唔到。冇引擎讀到嘅正文註號照舊用答案（issue #8 第 4 項）。`_layout`（按正文註號替冇讀法嘅頁底註編號）亦唔再將答案當做引擎讀法，同佢個 docstring 講嘅一致。
- 改動二（`doukeoi-zingkeoi/s0327`）：頁面寫咗 `㉔`，但印本係 240。頁面嘅註號如果讀落係定咗嗰個數漏一位，而另一個讀法（引擎或者答案）讀到成個數，就寫返做 `②④⓪`；之前淨係 `⑦3`、`⑦⁴` 呢類先會寫返。
- 測試：`tests/check_repair_notes.py` 加 315 頁同 327 頁兩個例，舊版兩個都 FAIL。全套測試嘅失敗同改之前一樣（22 個環境問題）。
- 驗證（重放，唔使模型）：七輪已封頁（Flash-Next 3608580、41a0a78、cb0cb06、1568019 第二輪；27B 41a0a78、cb0cb06、7e6532b）用 3608580 同今版各行一次 `finish_book.py`，冇一案變差。Flash-Next 3608580：28 案 CER 16.94% → 15.09%，27 案 8.90% → 6.33%，註 F1 47.4 → 64.9（表格案 650 → 81 錯；327 頁註 0 → 100）。`s0075` 喺三輪由 75 → 100。

## Unreleased — 註號還原唔到、註文切開句子（issue #8 五項）

- 第 1 項（重現同分類）：新 script `scripts/note_census.py BOOK ...`（淨係讀，唔使 GPU）逐頁點每個註號位：頁面有、兩個引擎讀到一樣（頁面冇）、得一個引擎讀到、兩個引擎位置差少少、兩個引擎讀到唔同、頁底註兩個引擎都冇讀到註號；另計兩三位數，書做完就連 `finish/footnotes.json` 嘅腳註數。jyut-ocr-bench 有註嘅 6 案（五輪 Qwen 已封頁）：約 19 條註入面，頁面有 2–8、兩個引擎一樣 4–6、得一個引擎 5–7、讀到唔同 2–4；0 分嗰 4 案全部係兩三位數圈號。8 本書嗰 504 頁要重新 OCR 樣本頁先數到（工作目錄已清），工具已經準備好。
- 第 2 項（用頁面結構配對）：N9 將 B4 標咗未配對、兩個引擎都冇讀到註號嘅頁底註當做冇讀法嘅註位；讀法定唔到嗰陣，用正文註號順序編號：正文註號數目同頁底註一樣、每個正文註號最少一個引擎讀啱（一個引擎都得，因為頁底有佢嘅註）、係連續數、頁底註有讀法嘅唔可以矛盾；N9 喺 B4 個標記度寫返註號。
- 第 3 項（多位數圈號照數字讀）：N9（上一版）已經讀 `⑥④`、`⑦3`、`⑦⁴`、`^{66}`、`\textcircled{240}` 做數字、按同頁連續定號、定稿寫 `[^n]`；今版加前後頁：逐頁照次序讀，上一頁最後一條註之後嗰個數，每個位都讀到（啱或者漏一位）就優先用（兩個引擎讀 82 同 85 嘅一條註，上一頁去到 81：寫 82）。Phase 3 嘅 `CIRCLED_RUN` 仍然成頁唔放，由修書嘅 N9 補返。
- 第 4 項（規則定唔到先問模型）：phase 3 新題 `note-numbers`：頁上印咗註（engine A 嘅 footnote block，或者引擎讀到行頭註號），而且引擎讀到嘅註號多過頁面有嘅、或者有引擎寫咗連續圈號，先問；畀整頁原圖，逐條答註號、註文開頭幾個字、正文註號前面嘅幾個字（JSON）；答案封入頁面紀錄 `noteNumbers`。N9 當佢係多一個讀法（「m」）：註用開頭幾個字對行，正文註號用前面嘅字搵位（全頁淨係一處先算），引擎冇讀到嘅正文註號都會開位。`note-numbers` 加入 effort 階梯同 `references/subagent-orchestration.md`。
- 第 5 項（真係配唔到）：新規則 B4（brackets 階段，B3 之後、joins 之前）：頁尾嗰段入面 engine A 讀做 footnote block、行頭冇註號嘅註（同埋夾喺中間嘅西文行）寫做 `[^unpaired-SCAN-N]: `；joins 同定稿當佢係頁腳註，所以跨頁句子會接埋，註擺喺接好嘅段落後面；N9 定到號就喺標記度寫號；定稿仍然配唔到嘅寫做引用段 `> `（評分唔計 `>`），出 `note-unpaired` 疑問。engine A 冇讀做 footnote 嘅行照舊唔接（保守）。對位唔到嘅 footnote block 用頭四個字對（同 `note_labels` 一樣）。
- 測試：`tests/check_repair_notes.py` 加：前一頁連續定號；頁底冇註號嘅註由一個引擎讀到嘅正文註號編號、定稿配成 `[^1]`；冇正文註號嘅留做未配對、定稿寫引用段；答題者讀法開正文註號位、冇答案就唔寫。新檔 `tests/check_note_question.py`：phase 3 問一次、封入 `noteNumbers`、冇註唔問、JSON 解析。`tests/check_repair_boundaries.py`：頁尾 footnote block 冇註號嘅註而家會接（B4），engine A 冇讀做 footnote 嘅照舊唔接。修書相關測試、`check_documented.py`、`check_benchmark_snapshot.py` 都過；全套 `tests/run_all.py` 照舊係嗰 22 個環境問題檔失敗（同改之前逐行比過，淨係暫存路徑同時間唔同）。
- 驗證（重放，唔使模型）：五輪 Qwen 已封頁用 41a0a78 同今版各行 `finish_book.py`：41a0a78 兩輪用 41a0a78 重放逐 byte 一樣；今版淨係改咗表格案（13 條頁底註變做未配對引用段）同 27B cb0cb06 嘅 75 頁（3 條），七項分數全部冇變、冇一頁差咗。Phase 3 嘅新題同答案嘅效果要真模型重跑先量到（未跑）。41a0a78 正式兩輪（N9，未有今版）：Flash-Next 27 案 CER 6.42%、28 案 15.14%、註釋 F1 52.9；27B 7.73%、16.13%、64.9。

## Unreleased — 修書 N9：全書連續編號嘅註（兩三位數註號）寫返，定稿先配得成腳註

- 問題：成本書連續編號嘅註（64、72、240……）印成兩三位數嘅圈號，Unicode 冇呢啲字，兩個引擎各自亂讀：引擎 A 寫 `⑥④`、`⑥5`、`⑦3`、`⑳`（240 讀漏一位）、正文嘅註號有時得一個 `⑧`；引擎 B 正文寫 `^{64}`……`^{69}`、註寫 `⑥④`……，`$\textcircled{240}$` 讀啱，但 77 頁將 72–74 嘅註號讀成 `\textcircled{2}`、`{3}`、`{4}`，註本身讀成 `{7}`、`{8}`、`{9}`。修書嘅 N1 淨係喺「由 1 開始逐頁數」嘅位寫註號，所以呢啲一個都冇寫返，`_footnotes.convert` 冇嘢可以配。jyut-ocr-bench 75、77、327 頁同埋表格案嘅 21 條註，Gemini、Haiku 同兩個 Qwen 全部一條都冇變腳註（註釋配對 F1 21–28）。唔係模型嘅問題：合併淨係睇漢字，註號由技能處理。
- 改動：新階段 `notes`（規則 N9，`scripts/_repair_notes.py`），喺 `runs` 之後、`forms` 之前行，修改照樣過 `edge_safe`；`--no-notes` 關。每頁讀晒兩個引擎喺對齊位讀到嘅註號：行入面嘅係正文註號，行頭嘅係註（下一個字嗰行，或者 gap 入面嘅成行西文註，用字對返頁面嘅行）。每個讀法變做一個數：圈號逐個連埋（`⑥④` 64），後面跟住嘅普通或者上標數字都算（`⑦3`、`⑦⁴`），上標同 LaTeX 用 `marker_key`（engine B 嘅 `\t` 變咗 tab 都得），最多三位數。「漏一位」：讀法係個數拎走一位（B 嘅 4 係 74、A 嘅 20 係 240）。
- 定號：每頁嘅註先定做一串連續數：揀最多位置啱讀（exact）嘅起點，打和就睇兩個引擎有幾多讀法（啱或者漏一位）支持（77 頁 B 嘅 7、8、9 同 A 嘅 72–74 一樣咁多 exact，淨係 72 解釋到兩邊）；要最少兩個 exact 位，而且最少一個位兩個引擎都支持（表格頁 A 讀 ⑫ ⑬、B 讀 19 19，印本係 193–205：唔寫）；淨係一條註就要兩個引擎都讀、每個讀法係個數或者漏一位。兩串一樣咁好就唔寫。正文註號先試用註嗰串（每個位最少漏一位支持），唔得先自己定。以下情況成頁唔寫：註定唔到；註同正文註號定出嚟冇一個數重疊；有一條註寫唔落（定稿會將其他變腳註、佢留做段落夾喺中間，評分仲差過全部唔轉）；冇一條註搵到佢嘅正文註號。
- 寫法：一個圈號寫到 50（同印本一樣），50 以上寫圈號數字（`⑦②`、`②④⓪`），`marker_key` 讀得返個數；正文註號寫喺引擎讀到嘅標點之間（頁面嗰度嘅標點同數字要同引擎一樣，唔係就唔寫），註寫喺行頭。頁面已經有嘅註號照留，但 N9 讀到係個數、`marker_key` 讀唔到嘅（`⑦3`、`⑦⁴`）會改寫。淨係靠漏一位寫嘅位出 `note-markers-unverified` 疑問，寫低兩個引擎嘅讀法。逐頁由 1 數嘅註仍然由 N1 寫，N9 淨係補 N1 冇寫嘅。
- 測試：新檔 `tests/check_repair_notes.py`（64、65 嘅註同正文註號；77 頁 B 全部讀錯但連續；三位數 240；表格頁 ⑫⑬ 對 19 19 唔寫；頁面已有嘅唔郁；`--no-notes`；連埋 `_footnotes.convert` 配到 `[^64]`）。`tests/cases/doukeoi-zingkeoi.json` 加三條 `regression` assertion（75 頁 `[^64]:`、77 頁 `[^72]:`、327 頁 `[^240]:`）。`references/long-book-workflow.md` 加 `--no-notes`。修書相關測試、`check_documented.py`、`check_benchmark_snapshot.py` 都過；全套 `tests/run_all.py` 喺呢部 Mac 照舊係改之前就失敗嗰 22 個檔。
- 驗證（重放，唔使模型）：五輪 Qwen 已封頁（Flash-Next 1568019 兩輪、cb0cb06、7e6532b，27B cb0cb06）用 7e6532b 同今版各行一次 `finish_book.py`；7e6532b 嗰輪用 7e6532b 重放同原本逐 byte 一樣。今版淨係改咗 75、77、327 頁，冇一頁分數變差。註釋配對 F1 由 27.6 升到 43.8–68.4；Flash-Next cb0cb06 三頁嘅註全部配到（5、3、1 條），呢三頁錯字 50 → 11，27 案 CER 6.55% → 6.38%；27B cb0cb06 77 頁 14 → 2 個錯、三條註全配，327 頁配到 `[^240]`。

## Unreleased — 修書 B3：註腳行嘅開頭照兩個引擎改返（冇 bullet，註號喺括號前面）

- 問題一（jyut-ocr-bench `doukeoi-zingkeoi/s0077`，Qwen3.8-Flash-Next 三輪有兩輪）：模型將頁腳註寫成 Markdown 清單（`- 〔明〕…`），而且冇註號；兩個引擎都讀到 `⑦3〔明〕…`。清單唔算頁腳註（`_ocr_markdown.foot_notes`），於是頁嘅最後一行係註腳嗰行，`edge_safe` 將會改變嗰行種類嘅修改全部扣起：第 73 註嘅 44、6、13b-14a 同埋第 74 條英文註，共 125 字，淨係寫咗做疑問（withheld at the page's edge: the page's last line would change kind）。冇寫清單嗰一輪、Gemini、Haiku 同 27B 都冇呢個問題。
- 問題二（`doukeoi-zingkeoi/s0014`，Qwen3.8-27B）：模型將第 2 註嘅開頭寫成 `[②荷]`，即係註號放咗入括號入面；兩個引擎都讀到 `②〔荷〕`。B1 將方括號改成 `〔②荷〕` 之後，`finalize_page_markers.py` 配唔到註號同正文嘅 ②，嗰條註變咗普通段落，正文留低 ②；評分將成條第 1 註當係搬咗位（172 個錯）。
- 改動：新規則 B3（`scripts/_repair_brackets.py` `note_line`，喺 brackets 階段、B1 B2 之後、joins 之前行）。兩個引擎都用註號開一行（engine B 嘅 LaTeX `$ \textcircled{7} $` 都算，連佢個 `\t` 變咗 tab 嘅情況），而頁面嗰行：(1) 用 bullet 開頭，就拎走個 bullet；(2) 將同一個註號寫咗喺跟住嘅括號入面，就搬返去括號前面。兩邊引擎有一邊冇對齊、冇讀到註號、或者自己都讀到 bullet，就唔郁。P8 照舊處理頁面保留咗註號嘅清單項；B3 處理頁面連註號都冇咗、要靠引擎先知係註嘅情況。
- 測試：`tests/check_repair_brackets.py` 加 B3 嘅 case（冇註號嘅清單註腳行、engine B LaTeX 連 tab、註號喺方括號入面），同埋唔郁嘅 case（engine B 冇讀到註號、兩個引擎都讀到嘅真清單）；`tests/cases/doukeoi-zingkeoi.json` 加兩條 `regression` assertion（77 頁要有 `Sow-Theng Leong, Migration`，14 頁要有 `〔荷〕Anton Blok`）。修書相關測試全部過，`check_benchmark_snapshot.py` 過；全套 `tests/run_all.py` 喺呢部 Mac 照舊係同改之前一樣嗰 22 個檔失敗。
- 驗證（重放，唔使模型）：四輪已封頁（Flash-Next 1568019 兩輪、Flash-Next cb0cb06、27B cb0cb06）用 cb0cb06 同今版各行一次 `finish_book.py`；cb0cb06 嗰兩輪用 cb0cb06 重放同原本輸出逐 byte 一樣。今版淨係改咗有清單註腳或者註號入括號嘅頁，冇一頁分數變差：Flash-Next cb0cb06 77 頁 129 → 16 個錯，27 案 CER 7.08% → 6.59%（28 案 15.66% → 15.30%）；27B 14 頁 172 → 2 個錯、該頁註釋配對 66.7 → 100，27 案 8.60% → 7.85%（28 案 16.75% → 16.21%），全集註釋 F1 21.4 → 27.6。另外 s0075、s0315-0318-table 嘅清單註腳行冇咗 bullet（照印本），分數冇變。

## Unreleased — 修書：一條西文書目斷開兩行又差一個撇號，唔再令成段西文行漏晒

- 問題：jyut-ocr-bench 嘅書目頁 `doukeoi-zingkeoi/s0583`（即 609 頁現代書掃描 583 頁）用 HTTP 本機模型校對，Qwen3.8-Flash-Next 兩輪同 Hy4-flash 兩輪都漏咗第二段九條西文書目，缺 966 字；Gemini 4 Hydrogen 同 Claude Haiku 5.5 嗰幾輪淨係缺約 110 字。兩個引擎都讀到嗰啲行，修書亦知道（`run-no-place`：runs both engines read left out, no rule writes them），但冇寫返。
- 原因：引擎 A 將 Dardess 嗰條斷做兩行，引擎 B 讀成一行，兩邊仲差一個撇號（引擎 A T'ai-ho，引擎 B Tai-ho）。`line_pairs` 要合埋嘅字完全一樣先肯將兩行配一行，於是嗰條一對一咁配，之後每一行都配錯，配唔到尾，N2 就成段放棄（`whole lines`）。2026-10-06 修過同一頁，嗰陣嘅底稿冇呢個撇號差異。引擎 B 將書名包喺 `*斜體*` 入面唔係原因：比較本身已經唔計 markup。
- 改動（`scripts/_repair_runs.py`）：`line_pairs` 搵唔到字完全一樣嘅合法，就用「差唔多」再試一次（長度差唔過一成、九成字相同，`PAIR_RATIO`）；呢個淨係影響點配對，寫唔寫照舊逐對判。`_whole_lines`：一對行淨係差撇號、字入面嘅連字號或者大細楷（N7 `slightly` 嘅定義，但唔包差一個字母：差一個字母嘅西文註照舊係疑問），照引擎 A 寫，疑問寫低兩邊讀法同差咗邊個字（`run-engines-differ: N whole line(s) apart in apostrophes, hyphens or case; written as engine A reads them`）。差一個標點或者數字嘅照舊留做疑問。
- 測試：`tests/check_repair_runs.py` 加兩個 case（一條斷兩行又差撇號：照引擎 A 寫，後面嘅條目照配照寫；一條斷兩行又差數字：嗰條唔寫，後面照寫）；`tests/cases/doukeoi-zingkeoi.json` 加兩條 `regression` assertion（583 頁要有 `Dardess, John W.` 同 `Watt, John R.`）。修書相關嘅測試全部過（9 個 check、1 個 unit test 檔），`check_benchmark_snapshot.py` 過。全套 `tests/run_all.py` 喺呢部 Mac 有 22 個檔失敗，改之前（1568019）一模一樣，同呢次改動無關（例如期望嘅暫存路徑係 `/var/…`，macOS 寫 `/private/var/…`）。
- 驗證（重放，唔使模型）：將 Qwen3.8-Flash-Next 兩輪（技能 1568019，jyut-ocr-bench 28 案）嘅已封頁重新行 `finish_book.py`。用 1568019 重放，輸出同原本逐 byte 一樣；用今版重放，淨係 s0583 有變：缺字 966 → 103，九條寫返八條（Ho, Ping-ti 嗰條兩邊差一個標點，照舊係疑問）。CER 寬容（評分器 samples-4.0.0）28 案 18.76% → 16.03%、18.17% → 15.44%；唔計 11 頁嘅 seoi-cing-cyun 嘅 27 案 11.39% → 7.59%、10.60% → 6.80%；表格同註釋分數冇變。

## Unreleased — 派題唔空等；每個答題者攞幾多題按模型定（issue #7）

- `ocr_batch.py --round` 有位即派。8 本書實測（2026-11-18）題目一直有得派嗰陣，一個 session 20 個位平均得 14 至 16 個做緊嘢，其餘係 script 自己等。改咗五樣：答案寫齊之後佔位由 30 秒減到 6 秒（279 個答題者量到中位數 2 秒就返回，99% 喺 6 秒內）；唔再等四分一嘅位空咗或者空咗 30 秒先補派（`--top-up-wait 秒` 可以加返）；冇人做緊嘢而有步驟行緊嗰陣唔再等 90 秒；每秒睇一次書（以前 5 秒），bundle 同答案檔讀過就記住；答題者自己個檔入面有齊 `references/packet-worker.md` 嗰頁指示，唔使再開，少行一輪。
- 新 `--not-started FILE`：平台拒絕開嘅答題者（同時太多），下一次 `--round` 講明係邊個 worker 檔，嗰份即刻再派，唔計入派咗幾多次。佔位減到 6 秒之後偶然會撞到，以前要等 `--answer-grace`（600 秒）先再派。
- 每個答題者攞幾多題唔再係固定 4 條，按 `--answered-by` 嘅模型定（`packet_dispatch.py` 嘅 `share_tokens()`）：冇加價線嘅模型一份最多 10 條；prompt 過咗某個大細就加價嘅模型（`PRICE_STEPS`，而家得一個，過 10 萬 token 貴五倍）按估計 token 裝，校對嗰步一條題一個 bundle。點解：答題者每開一個都有固定開銷，題越多每條題越平越快；8 本書嘅實數照未加價單價計，10 條每條題 0.43 美仙、4 條 0.60 美仙，一個 session 每分鐘答 47 條同 28 條。之前嘅 4 條係為有加價線嗰個模型定嘅，其他模型用佢係又貴又慢。`--items-per-worker N`、`--share-tokens N` 可以人手定。
- `packet_dispatch.py`：`--answered-by` 而家亦用嚟定每份幾大（以前淨係 `--apply` 用）；新 `--share-tokens N`。按 token 裝嗰陣唔會為咗遷就 `--max-workers` 而將一份加長，一次最多派咁多份，其餘下次再派。計劃入面每份同每個 bundle 多一個 `tokens`（估計）。
- 估算：bundle 檔每 byte 0.9 token，加裁圖每個 token（闊乘高除 750）計 1.84；用回歸評測 148 個答題者嘅實數擬合，一半喺 2,700 token 之內、九成喺 6,800 之內。現代書嘅題冇得驗（嗰批 bundle 已經清走）。
- 未用真模型量過改咗之後嘅速度同成本，亦冇重跑 28 案評測：改嘅係幾時派、每份幾大同答題者由邊個檔讀指示，題目同指示嘅內容冇變。按 8 本書嘅實數推算，每個位每次交替嘅等候由約 45 秒減到 10 秒左右。
- 測試：`tests/test_ocr_batch.py` 加三個（平台拒絕嘅答題者即刻再派而且唔計次數；一份唔超過畀佢嘅 token、worker 檔有齊指示；有加價線嘅模型一條題一個 bundle），`tests/test_packet_dispatch.py` 加一個（模型名點認、按 token 裝、超過 `--max-workers` 嘅留返下次）。

## Unreleased — 幾個 PDF 一齊做：`ocr_batch.py`（issue #5）

- 新 script `scripts/ocr_batch.py`，三條 command 做晒一批書：`ocr_batch.py A.pdf B.pdf ...` 開批（每個 PDF 一個普通嘅 `BOOK`，render 喺背景做，即刻返回）；`--ocr BATCH` 一個 call 逐本讀晒所有書；`--round BATCH` 逐輪行每本書嘅 profile、校對、定稿同清走，直到全部交付。
- 點解：逐本做嘅話，本機模型每本書（前景分輪就每一輪）都要停、開一次，agent 亦要等完一本書嘅 OCR 先可以答另一本嘅題。實測一本 639 頁嘅書 OCR 66 分鐘、校對連定稿 39 分鐘，後者唔使 GPU。而家 OCR 成批一個窗口；一本書讀完即刻校對，同下一本嘅 OCR 重疊；所有書嘅題目夾埋一輪派。
- `--round` 一個 call 最多等 540 秒（shell call 嘅 600 秒上限之內），淨係印幾行；佢開嘅步驟喺背景行、唔跟 call 結束。exit 10 有題等答（`BATCH/plans/round-NN/worker-KK.txt`，每個答題者一個檔）、75 未有題、4 OCR 嗰個 call 完咗但仲有書未讀、0 全部交付、1 有書失敗。同一時間最多 `--max-workers` 個答題者、每個大約 `--items-per-worker` 條題（預設 20 個、4 條），未派嘅等有位再派：唔會因為書多而令一個答題者一次過攞幾百題。點解 4 條：8 本書實測每個答題者 10 條題，context 由 4.7 萬 token 升到 14.4 萬；嗰個模型 prompt 過 10 萬 token 就貴五倍，99% 嘅答題者過咗線，每條題 1.55 美仙；改做 4 條之後係 0.85 美仙（40% 過線）。代價係一個 session 嘅答題速度由每分鐘約 48 條跌到 26 至 36 條。
- 開批嗰陣印一行 `[batch] ESTIMATE`（大約幾多題、幾多 input 同 output token，由量過嘅書推出嚟嘅範圍）：agent 用一行話畀用戶知就即刻做落去。`SKILL.md` 講明：成本、題量、時間、自己嘅 token 預算都唔係停低、暫停、問用戶或者改路線嘅理由，rate limit 同 API 錯誤等一陣再試，`--pause` 係用戶嘅掣。8 本書實測入面，做主 agent 嘅細模型因為覺得貴自己停咗四次。
- 一本書失敗（render、OCR 或者任何一步）淨係嗰本標 `failed` 連原因，其他書照做；`--retry-failed` 再試。背景步驟俾人殺咗，下一輪自己再開。答題者唔使等齊先派下一批：每一份派出去都記低，答案檔寫齊就當做完，有位就補派（主 agent 開晒答題者就即刻再跑 `--round`，冇位佢自己等）；同一包唔會派兩次。8 本書實測，等齊 20 個先派嗰陣每分鐘得 27 條題，慢過 OCR 出題。`--pause BATCH`／`--resume BATCH`：暫停期間唔派新題（`--round` exit 3，agent 收工），做緊嘅答題者照做完，續跑唔使重做：成本要人決定嗰陣用。幾個 session 可以同時答同一批（各自 `--round … --caller 名`，各有自己嘅位，同一包唔會派畀兩邊）：一個平台同時開到嘅答題者有上限，題多嗰陣答題先係樽頸。派題次序係「呢一輪淨返最少題嘅書先」，唔係照書嘅次序：8 本書實測，第四、五本嘅廿幾條 profile 題排喺頭三本幾千條題後面等咗成個鐘。答題者寫齊答案之後仲佔位 30 秒（`--settle`），等佢返回咗先補派，唔會撞平台嘅同時上限；答題者全部冇咗（失敗、斷線、換 session）就加一次 `--answerers-gone`。冇人答嘅 bundle 派 4 次就停。
- `run_ocr_phase.py` 識得 `ocr_batch.py --ocr BATCH`：按批嘅路線估顯存同啟動時間，所以成批可以包喺一個交接窗口（`--background` 或者前景分輪）。
- 冇改任何單本書嘅 script：每本書行嘅就係 `SKILL.md` 四嘅 command，`BOOK` 入面冇批嘅檔。`BATCH/timeline.jsonl` 記每步嘅開始、結束同秒數，`BATCH/batch-status.json` 記每本書去到邊。
- `SKILL.md` 四加〈幾個 PDF 一齊做〉、八加一批書點續；`references/long-book-workflow.md` 加同名一節。
- **8 本書實測（2026-11-18，4,244 頁，細嘅睇圖模型做主 agent 同答題者，單張 32 GB 顯示卡）**：由開批到 8 本全部交付 13 小時 33 分。Render 8.2 分鐘；OCR 一個 call 294.9 分鐘（每頁 4.17 秒），開批後 4 小時 56 分讀晒；第一本喺 3 小時 44 分交付。答咗 32,969 條題（每頁 7.8 條），用咗 4,648 個答題者；input 35.2 億 token（31.0 億係 cache 讀取）、output 1.71 億；照嗰個模型嘅官方 API 價錢約 494 美元，即每條題 1.5 美仙、每頁 11.6 美仙。樽頸係答題唔係 OCR。當中有約一個鐘係主 agent 因為覺得貴自己停低、同一次人手暫停；後段加咗第二個 session 一齊答。
- 測試 `tests/test_ocr_batch.py`（41 個，離線）：真嘅 `ocr_batch.py`、`packet_dispatch.py`、PacketClient 同 `apply_packet_answers.py`，每本書嘅步驟用守住同一個契約嘅替身。涵蓋：兩本書由 PDF 到交付而且同一輪答題者服務兩本書；第一本校對嗰陣第二本仲讀緊；OCR、render、校對失敗各自唔影響另一本；時間預算用完喺兩本書之間停；一輪唔超過平台嘅答題者數；冇寫答案嘅答題者嗰幾包下一輪再派；http 後端等 OCR 完、一次一本、唔問人；單引擎路線；交接識得批嘅 command。
- 實機驗證（2026-11-18，真引擎，細嘅睇圖模型做答題者）：由真書切出嚟嘅兩個 PDF（8 頁同 6 頁）。開批之後 render 各 2 秒；一個交接 call 逐本讀晒（68 秒同 70 秒；本機模型當時冇開，所以冇嘢要停）；第一本一讀完就出 profile 題，第二本仲讀緊；7 輪共 124 條題，一輪最多 5 個答題者；兩本都交付去 PDF 隔籬，`BOOK` 自動清走。答題者每條題平均 6.4 萬 input token。

## Unreleased — GPU 交接：唔係本機模型嘅 agent 可以一個窗口做晒成個 OCR（issue #3）

- `run_ocr_phase.py --background`：畀「唔係用嗰部本機模型諗嘢」嘅 agent（雲端 agent）用。呢個 call 冇時間預算，agent 將佢放背景、用 `wait_for_run.py` 等；成本書一個窗口，本機模型淨係停一次、開一次。以前一律要前景分輪（`OCR_PHASE_MAX_SECONDS`，通常 540 秒），每一輪結束都開返模型、下一輪再停：script 自己記低嘅實測係一本 609 頁嘅書，兩個引擎合共 7.5 分鐘用喺分段度；一本 639 頁嘅書今次分咗 9 輪。
- 點解前景分輪仍然係預設：agent 本身用緊嗰部本機模型嘅話，指令一返回佢就要個模型諗下一步，所以每輪都要開返；放背景會令 agent 自己斷線（2026-09-23 實測）。所以 `--background` 喺呢種 agent 會被拒絕（exit 2，乜都冇停），三個訊號任何一個中都算：`JYUT_OCR_AGENT_IS_LOCAL_MODEL=1`；Qwen Code 行緊校對用嗰個模型；行呢個 call 嘅 process（shell、agent）有連線去模型 server。
- 安全網冇變：看門程序照樣喺 release 之前啟動，個 call 俾人殺咗就開返模型。`SKILL.md` 四.2、五同 `references/local-ocr.md`〈GPU 交接〉加咗呢條路同邊個唔准用；背景嗰個 call 俾 harness 殺咗就改返前景分輪。
- 測試（假嘅停、開模型指令同假引擎）：背景 call 喺預算遠細過所需嗰陣一次做晒、模型只停開一次；本機模型 agent 被拒絕而且乜都冇停；祖先 process 有連線就當係本機模型 agent；一本細書經背景交接由頭行到尾，`wait_for_run.py` 由 RUNNING 等到 DONE。
- 實機驗證（2026-11-18，本機模型開住，雲端 agent 行）：一本 50 頁嘅書，機器設定嘅預算係 540 秒，背景交接 511 秒一個窗口做晒；模型服務自己嘅紀錄顯示佢只停咗一次（00:09:48）、開返一次（00:18:14），期間 stand-in 喺佢個 port 答 503；兩個引擎 50 頁都封好，完咗健康檢查通過。同一個工作量用前景分輪要兩輪。

## Unreleased — HunyuanOCR：server 唔再佔住系統內存；首次安裝按張卡定 slot 數（issue #3）

- `run_hunyuanocr.py` 啟動 llama-server 嗰陣加 `--cache-ram 0 --ctx-checkpoints 0`。Server 預設會留 8 GiB prompt 緩存同每個 slot 32 個 context checkpoint，OCR 每頁都係獨立請求，完全用唔著，但照樣佔系統內存：一個 32 slot 嘅 server 去到 13.6 GB，令部機內存用盡。實測（同一本書，顯存同時間不變）：4 個 slot 讀 40 頁，server 常駐內存峰值由 10.7 GB 跌到 2.6 GB，40 頁底稿文字一樣；16 個 slot 讀 96 頁係 4.4 GB。兩個選項各自要 server 嘅 `--help` 有列先會加，舊版 llama-server 照樣起到。
- 安裝器：`env.sh` 未有 `OCR_HUNYUAN_SLOTS` 嗰陣，唔再一律寫 1，改為按張卡嘅總顯存定：每個 slot 約 1.5 GiB、另留 2 GiB、最多 16 個（8 GiB 卡 1 個、12 GiB 4 個、16 GiB 7 個、24 GiB 12 個、32 GiB 或以上 16 個）；CPU、Metal 或者讀唔到顯存仍然係 1 個。用呢個數試跑過唔到，就改返 1 個再試一次。已經寫咗數嘅機器唔郁，試跑失敗亦唔會自動改佢。
- 點解最多 16 個：32 GiB 卡實測 16 個 slot 幾乎全程都滿、GPU 使用率中位數 100%，一本 639 頁嘅書 17 分鐘；32 個 slot 冇快到。

## Unreleased — 橫排書嘅書眉按位置學，唔使逐頁問

- 問題：book profile 以前淨係識「同一行字喺一半嘅頁出現」嘅書眉，啱舊書每葉版心都印同一個書名。橫排嘅現代書一邊頁印書名、另一邊印章名，冇一行過半，所以乜都學唔到，逐頁校對就每頁問一題書眉。實測一本 639 頁嘅橫排書：第一輪 830 條裁決題，606 條（606 頁每頁一條）係淨係引擎 A 讀到嘅頁頂書眉；書名喺 638 頁入面嘅 304 頁，差少少先夠一半。
- 而家 `learn_heads()`（`book_profile.py` 寫入 `layout.learnedHeads`，冇 profile 記錄嗰陣 `proofread_pages.py` 自己學）多一種量法 `placed_heads()`：頁頂嗰行（成頁最高、上面隔籬冇其他字、版面標 `header` 嘅短行）喺單數或者雙數頁有一半以上企喺同一個高度，嗰度就係書眉帶；喺帶入面出現三頁或以上嘅字串就係書眉。記喺 `learnedHeads.placed`（`stems`、`bands`）。
- 按位置學到嘅書眉淨係喺帶入面先丟：block 要一半高度喺帶入面、字號同書眉一樣；淨係引擎 A 讀到嘅書眉亦要喺帶入面嘅 block 先唔問就丟（`learned-head-reading`），兩個引擎都讀到嘅成個 block 丟（`learned-head-block`）。章首頁印喺下面嘅章題（就算字號一樣、版面標咗 `header`）、書名頁嘅書名、正文重複嘅表名都照留；淨係引擎 B 讀到嘅冇位置可對，照舊問。
- `layout.runningHeads` 冇改：嗰度係唔理位置淨睇字嘅，章名同時係章首頁嘅標題，唔可以放入去。按重複量到嘅書眉同以前一樣；按位置乜都學唔到嘅書（包括冇書眉嘅書），`learnedHeads` 冇 `placed` 呢個 key，成份記錄逐個 byte 冇變。
- 喺另外 14 本書嘅引擎 A 底稿上重算（只讀、冇問模型）：13 本嘅記錄同以前逐個 byte 一樣；一本 63 頁、頁頂橫印刊名同篇名嘅舊期刊，以前按重複一個書眉都量唔到，而家學到 4 個（都係真書眉），未用真模型重跑驗證。
- 離線重放嗰本書（抄一份底稿、用測試嘅假模型跑真嘅 merge，冇問模型）：學到 22 個書眉，603 頁有書眉帶；606 條入面 600 條唔使問，第一輪裁決題由 830 條跌到 230 條。有改動嘅 605 頁，每頁少咗嘅啱啱好係頁頂書眉嗰行；17 個章首頁（連結語）嘅標題全部留低。仲要問嘅 6 條：4 條係淨係出現一兩頁嘅書眉，2 條係讀法連埋正文第一個字（照舊問，書眉之後成個 block 丟）。
- 已經有 profile 嘅書要重跑 `book_profile.py` 先會學到；profile 同 `learnedHeads` 嘅 hash 入咗 packet 題嘅 request id 同封條，所以重量之後嗰本書之前答過嘅校對題同封咗嘅頁都要重做。
- 新測試：`tests/check_learned_heads.py` 加一本虛構嘅 26 頁橫排書（書名／章名隔頁交替、章首標題、重複表名、引擎 B 獨讀、讀錯一個字）同兩本學唔到嘢嘅書。

## Unreleased — 成套測試由六分幾鐘減到一分鐘內

- `tests/run_all.py` 幾個檔一齊跑：`--jobs N`，預設係部機嘅核心數、最多 8 個；`--jobs 1` 同以前一樣逐個跑。實測（16 核心）成套由 380 秒減到 52 秒，`--jobs 16` 係 42 秒，連跑三次結果一樣；測試數目冇變（755 個 unit test、54 個 check）。
- 時間本來集中喺幾個檔，唔係因為檔多：96 個檔入面 72 個加埋得 42 秒，`test_handover.py` 一個就佔 130 秒（佢好多測試要等真實嘅秒數）。呢個檔而家逐個 test class 分開跑（`SPLIT_BY_CLASS`），class 清單由 unittest 自己嘅 loader 攞，唔會漏咗淨係繼承測試嘅 class。
- 兩個一向唔過嘅測試修好：`test_setup_dual_engine.py` 有一個測試會去睇部機 home 目錄有冇 Hunyuan 權重，裝咗權重嘅機先會唔過；`check_wait_for_run.py` 用 `--effort` 做「錯嘅選項」，但有咗 `--effort-policy` 之後 argparse 當佢係縮寫。
- 部機 `env.sh` 嘅設定唔再漏入測試：`env.sh.example` 列嘅名同 `PROOFREAD_*` 一律唔帶入去。之前喺 source 咗 `env.sh` 嘅 shell 跑，會因為引擎名同裝置設定有五個檔唔過。
- 刪走 `tests/check_review_launcher.py`：佢 13 個測試全部喺 `tests/test_review_launcher.py` 入面有（後者仲多兩個）。

## Unreleased — PaddleOCR-VL 幾個 worker：同一張卡同時讀幾頁（issue #3）

- `run_paddleocr_vl.py --workers N|auto`（`OCR_PADDLE_WORKERS`）：幾個 PaddleOCR-VL worker 由同一條隊攞頁，一頁嘅底稿同邊個 worker 讀佢無關。預設 `auto`：空閒顯存放得落兩份 14 GiB（另留 2 GiB）就開兩個，否則一個；CPU、MLX 後端、或者每個 worker 分唔夠 8 頁都係一個。`OCR_PADDLE_WORKERS=1` 即係以前嘅行為。
- 幾個 worker 嗰陣每個有顯存上限（`FLAGS_gpu_memory_limit_mb`），因為 Paddle 嘅配置器有幾多攞幾多：一個 worker 讀一本書，實測由 12 GB 升到 27 GB。一個 worker 照舊冇上限。
- 一個 worker 死咗，佢嗰頁交畀另一個重讀；全部都死晒，就由一個冇上限嘅 worker 讀餘下嘅頁，所以開多個 worker 唔會令單 worker 讀到嘅頁失敗。交接嘅顯存估算跟住明確嘅 worker 數計；`auto` 只要求一個 worker 嘅顯存，唔會為咗開第二個而停本機模型。
- 實測（一張 32 GB 卡）：現代橫排書 120 頁，一個 worker 503 秒、兩個 321 秒（1.57 倍）；舊書 50 頁，334 秒對 202 秒（1.65 倍）。兩批頁嘅文字同座標框，兩個 worker 同一個 worker 逐頁一樣。GPU 使用率由 53% 升到 93%，顯存峰值 28 至 30 GB，所以 `auto` 最多開兩個。一本 639 頁嘅現代書，OCR 實測 66 分鐘，當中 Paddle 佔 47 分鐘。
- HunyuanOCR 冇改：同一本書實測 16 個 slot 幾乎全程都滿，GPU 使用率中位數 100%，86% 時間係生成文字。32 個 slot 冇快到，而且 `llama-server` 多食好多系統內存（實測 13.6 GB，部機因此內存用盡）。所以唔開多份 Hunyuan，亦唔同 Paddle 同時跑：佢哋爭嘅係同一張已經滿載嘅卡。
- 額外 worker 嘅 process 由 runner 嘅主線程啟動：worker 跟父 process 一齊收工嗰個訊號係跟啟動佢嘅線程，由短命線程啟動嘅 worker 會跟住嗰條線程一齊死。新測試用假 Paddle 蓋住分頁、底稿一致、worker 死亡、時間預算同 worker 數計算。

## Unreleased — 通用標點融合：packet／HTTP 共用拆分及融合開關

- `proofread_pages.py` 加 `--punctuation-mode auto|split|fused`；`auto` 保留 packet 融合、HTTP 拆分嘅原本預設，兩條路線都可以明確選擇。共用融合 prompt、解析及格式檢查，唔綁定模型或者 agent 平台；舊 packet prompt、request ID 同答案格式保持可重播。
- 缺格式、符號數讀唔到或超出上限就回退獨立 census／punctuate；字元、標點數量、雙圈、導引點、重對齊及重試守門不變。HTTP 題型 `census+punctuate` 支援既有 effort policy；重試仍然係獨立題型。
- 每段、每頁及 run report 記錄融合嘗試、首次成功、回退及原因；封頁以實際模式判相容，模式轉咗唔可以混用封頁。舊封頁按記錄嘅 transport 推斷；未記 transport 嘅舊封頁只保留 `auto` 相容。
- 新合成離線測試覆蓋兩種 backend、格式回退、無標點、改字、計數、雙圈、導引點、重對齊、effort、歷史答案重播及封頁模式。呢啲測試驗證程式契約；實際速度同品質要由固定案例嘅對照實驗另外報告，唔因此更改通用預設。

## 2026-11-17：精簡 macOS 路線

- macOS 只支援原生 Apple Silicon，移除 Intel Mac 同 Vision＋Hunyuan 混合路線。
- 安裝同跑書統一 `--engine apple-vision`／`--engine hunyuan-paddle`；Linux／WSL2 保留 Hunyuan-only。移除跑書嘅 `--engines` 同公開路線名 `auto`、`vision`、逗號組合；舊設定須明確重選，新路線用新工作目錄。
- 純 Vision 唔下載 HF 模型，唔啟動其他 OCR 模型或做 GPU 交接；保留座標修正、單引擎校對、續跑及封條。雙引擎保留 MLX 加速。
- 以下舊版本紀錄及已封存評測保持原樣，唔代表退役路線仍然可用。

## Unreleased — 新書預設用兩個慳題開關；答題者數目寫入步驟；刪走舊禁令（issue #6）

- `SKILL.md` 一、四：A 套平台開到 subagent，就開工嗰陣定 `N`，第 3、4 步加 `--max-bundles N`；未有封好頁嘅書，第 4 步再加 `--same-print-marks --unmarked-page-sample`。已經有封好頁嘅書照返之前嗰條 command，唔中途加減（開咗同關咗嘅封頁互相唔算 current）。B 套未量度過，唔加。
- 根據（28 案 50 頁，現成 OCR 底稿，只做校對）：一個強模型做主 agent，照派工單每輪開最多 24 個同模型嘅答題者，兩個開關全開：520 題、11 輪、由頭到尾 33.6 分鐘（腳本自己 168 秒），CER 寬容 14.13%、表格結構 90.2、表格內容 85.6。同配置嘅細模型係 14.88%、89.7、82.9；冇開關嘅細模型基線 14.79%。即係開關冇令強模型或者細模型跌準確度。
- 同一次實測帶出、已經寫入六：主 agent 幾次未等齊答題者就行下一輪，多咗兩輪空轉，所以講明等齊先行、唔好中途停答題者；平台唔畀 agent 睇到 API model ID，`--answered-by` 就寫平台顯示嗰個名。尾三輪得 9 條重試同排版題，用咗 8.3 分鐘（全程四分一）：尾巴仍然係最大嘅剩餘時間。
- 刪走「Tesseract／Tessdata 硬性禁用」（`SKILL.md` 二、`references/local-ocr.md`）：流程已經寫死，agent 冇機會揀 OCR 工具。留返「引擎冇裝好就照 ERROR 講畀用戶知，唔好換引擎或者其他 OCR 工具」。

## Unreleased — macOS MLX 同 Vision 雙引擎優化（issue #4）

- Apple Silicon macOS 14+ 嘅 Paddle 預設用獨立、hash-locked `mlx-vlm==0.8.0` 做 Metal 識別，CPU 保留版面分析；沿用原裝未量化權重。明確 native／CPU 設定保留，舊 auto-CPU 可遷移。正式 runner 管服務啟動、模型 readiness、GPU 隊列、限時同父程序死亡清理，失敗唔會靜默 fallback。
- 新增 `--engine vision,hunyuan`／`ocr_book.py --engines vision,hunyuan`：Hunyuan B → 草稿 → Vision A → 補讀。Vision 統一左上角座標、確定次序同 TXT／JSON 契約，保留原始 observations／line IDs，家具候選唔會自動授權刪字；Hunyuan 表格候選經聯合裁圖或全頁圖處理，包含數字表。
- 下游封條記語義指紋，換引擎／後端或 block 文字／座標／label／次序會 stale；未變嘅 legacy native 封頁照收。macOS 可回收記憶體估計統一用 free + inactive + speculative，唔重複加 purgeable；順序引擎取峰值需求，保留手動預算同 Hunyuan slots。
- CER 唔作啟用門檻，通用 Paddle 記憶體預算保持。評測成品統一存喺 benchmark repo，效能量度同驗收紀錄見 [issue #4 結案回覆](https://github.com/laubonghaudoi/jyut-ocr-proofread/issues/4#issuecomment-6952908371)。

## Unreleased — 派工單：答 packet 題可以一次過開多個答題者（issue #6）

- **新 script `packet_dispatch.py PACKETS_DIR [...]`**：一步停低等答案（exit `10`／`11`）就跑佢。佢讀最新嗰個 wave，將未答齊嘅 bundle 分成幾份 assignment，每份有一段現成嘅 `prompt`，主 agent 每份開一個 subagent、原文照貼就得。`--max-workers`（預設 24）係平台同時開到幾多個，`--items-per-worker`（預設 10）係每個答題者大約幾多題。答題者返晒之後再跑一次：仲有未齊嘅包就再列出嚟；齊晒就加 `--apply --answered-by MODEL` 交晒答案，然後重跑同一條步驟 command。
- **幾本書一齊派**：一次過列幾個 packets 目錄，佢哋嘅 bundle 放埋一齊分，一個答題者可以服務幾本書。
- **`references/packet-worker.md`**：答題者淨係讀呢一頁，唔載入技能、唔跑 script、唔計 hash。
- **`SKILL.md` 六改寫成一個循環**：攞派工單 → 一次過開晒 subagent → 再跑派工單交答案 → 重跑步驟。冇 subagent 嘅平台自己照同一頁逐包答。答 packet bundle 嘅並行上限由「預設最多 8 個、由 2 個開始」改成「有幾多份 assignment 就開幾多個，唔超過平台嘅 slot」；分段 writer 同 auditor 嘅上限照舊。
- `proofread_pages.py` 同 `book_profile.py` 停低等答案嗰陣，`[packets]` 多一行寫成條派工 command。
- 點解：之前每包開一個會載入成個技能嘅 worker，一個大模型每題要約 245 秒，長 session 係約 35 秒；而一個細模型嘅答題者淨係攞自己嗰幾包同一頁指示，127 條題每題 6.4 秒、6.2 萬輸入 token，一案一個長 session 係 7.6 秒、9 萬。
- 成本書實測（一本 26 頁嘅直排舊書，現成 OCR 底稿，`--max-bundles 20`，主 agent 照 `SKILL.md` 六派工，答題者係一個細模型）：7 個 wave 共 564 題，由第一步到全部頁封好 22 分鐘；69 個答題者嘅工作時間加埋 103 分鐘。頭三個 wave（511 題）用咗 9 分鐘，尾四個 wave 得 53 條重試同排版題，但每個 wave 都要等齊先行下一個，所以仲用咗 9 分鐘。
- 實測帶出嘅兩點已寫入：一個 wave 分幾多包係行步驟嗰陣定（`--max-bundles`，預設 8），想開多過 8 個答題者要嗰陣加；答題者見到「已定」嘅文字（排版題、標點題）係照抄再加結構，唔係逐字重讀成頁，`references/packet-worker.md` 講明。未講明之前，一個答題者成批排版題唔肯答。
- 新測試 `tests/test_packet_dispatch.py`。

## Unreleased — `--unmarked-page-sample`：無標點嘅頁抽樣問；裁決答「睇唔到」就用成段大圖再問（issue #6）

- **`--unmarked-page-sample`（預設關）**：成頁兩個引擎各自讀到嘅標點都唔多過兩個，而且引擎 A 讀成淨係得字嘅 block 多過三個，就先問最長三個 block 嘅標點題；三個都答冇標點，其餘 block 照原文寫、唔問（`censusNotAsked` 記 `page-unmarked-sample`），否則其餘 block 喺下一個 wave 照常問。Profile 見到雙圈或者旁點嘅書唔用。封條記 `unmarkedPageSample: "on"`，入 provenance。
- 點解以頁為單位：有標點嘅頁上面，兩個引擎都冇讀到標點嘅 block 唔代表真係冇標點。Benchmark 28 案入面，唯一一本無標點古書以外有 62 個噉嘅 block，校對喺其中 15 個補咗標點。
- 離線重播（benchmark 28 案，用三輪已封存嘅答案）：14 頁有抽樣，9 至 10 頁成頁放行，少問 174 至 177 條標點題，約佔全部題目四分一，幾乎全部嚟自嗰本無標點古書；4 至 5 頁嘅抽樣見到標點，其餘 block 遲一個 wave 先問。放行嘅 block 入面，一輪答案寫過 13 個標點，另外兩輪係 0 個；嗰本書 8,760 字嘅人工答案總共得 6 個標點。
- **裁決答「睇唔到」就再問一次**（`widened_answer`）：估位裁出嚟嘅圖（glyph、band）答「睇唔到」，就用嗰個位所在成個 block 嘅圖再問一次，題目講明。第二次嘅答案作準；仲係睇唔到就保留引擎讀法，紀錄 `unseen`。紀錄多一個 `widened`（第一次嘅答案、大圖檔名 `page-NNNN-adjudication-wider-NN.png`）。成個 block、表格、整頁同跨 block 邊界嘅裁圖唔會再問。
- 實測（一個細模型，九個 benchmark 案）：66 條裁決答案有 9 條答「睇唔到」，其中 6 條係估位裁圖，第二個 wave 出咗大圖重問；5 條有答，1 條仍然睇唔到。可以對人工答案嘅兩條都答錯，同引擎 A 嘅讀法一樣，即係細模型喺大圖上冇改善；大圖本身睇得清、搵到個位。
- **成段、成個表格、成頁嘅裁圖會講明**（`WHOLE_CROP_NOTES`）：呢類裁圖冇裁走任何部份，爭議位一定喺入面，題目而家講明，叫答題者照上文下文搵返個位。原因係加咗「睇唔到」之後，細模型喺成個表格嘅圖上好易咁答：一本書嘅表格 29 條裁決題有 14 條答「睇唔到」，加咗呢句之後得返 2 條；可以對人工答案嘅位，答啱數由 1 條返到 4 條（未有「睇唔到」之前係 5 條）。
- 全集實測（benchmark 28 案 50 頁，一個細模型答題，`--same-print-marks` 同 `--unmarked-page-sample` 都開，未有上面嗰句提示）：對比舊 code 冇開關嘅一輪，題目 730 → 501，其中標點題 466 → 234；校對時間同輸出 token 各少約兩成。七項指標喺逐次波動之內（CER 14.79% → 14.88%，不計標點 14.82% → 15.06%，表格結構 83.0 → 89.7）。無標點古書嗰案由 261 條題跌到 54 條。逐個裁決位對人工答案（101 個可以對）：寫啱嘅由 65 個變 72 個。
- 新檢查喺 `tests/check_agreed_marks.py`（抽樣）同 `tests/check_adjudication.py`（再問一次）。

## Unreleased — 裁決題：裁圖跨欄、影唔到就答「睇唔到」、每題都要答（issue #6 實測帶出）

- **裁圖跨欄**（`crop_glyph` 嘅 `across`，逐頁校對嘅裁決題用）：直排 block 入面，估計喺欄尾四個字以內嘅爭議位，裁圖而家連埋下一欄嘅頭；喺欄頭四個字以內就連埋上一欄嘅尾。幾段由右至左並排，中間隔一條紙色空白，題目會講明裁圖係拼出嚟（`COLUMN_BREAK_NOTE`）。原因：每欄字數係由墨跡長度估出嚟，喺欄尾差一兩個字就落咗隔籬欄，而裁圖以前唔跨欄，爭議字唔喺圖上，答題者只能估。實例：一個 548 字、15 欄嘅 block，爭議字印喺下一欄第二個字，裁圖喺上一欄尾就完咗。
- 影響範圍（benchmark 28 案第一波，重出題目對比）：168 條裁決題有 16 條變成拼圖，其餘 152 張裁圖逐 byte 一樣，其他題型嘅圖全部一樣。Book profile 嘅異體字裁圖同 coverage 盲讀冇傳 `across`，照舊。橫排（轉向量度）嘅裁圖唔拼。
- **裁圖影唔到爭議位**：裁決 system prompt 叫答題者寫 `定案：睇唔到`，唔好靠上下文估。呢類答案（連模型自己寫嘅「無法判定」「無法由圖判定」等，`UNSEEN_VERDICTS`）唔當裁決：保留 fallback 引擎嘅讀法，紀錄係 `adjudicator-invalid` 加 `unseen: true`。以前呢啲字長過兩邊讀法兩個字以上先會作廢，所以兩個字或以上嘅爭議，「無法判定」會當成讀法寫入正文。
- **裁決答案最尾兩行**：system prompt 講明先 `定案：<字>`，最後一行 `RESEARCH: yes/no`；題目唔再話定案嗰行係「最後」。以前兩邊各自話自己嗰行係最後一行。
- **每題都要答**：bundle 嘅 `answer_format_instructions`、`SKILL.md` 六、`references/subagent-orchestration.md` 同 Codex packet worker 講明，圖睇唔清或者定唔到，唔係留空一題或者成包唔交嘅理由：照嗰題自己講嘅做法答，冇講就寫最有根據嘅讀法。實測一個細模型答題，40 個案次有 5 個因為「唔肯估」成包唔答。
- 已封嘅頁唔受影響。裁決題嘅 prompt 改咗，所以未封嘅頁入面，已答而未用到嘅裁決答案對唔返新題目 ID，會再問一次。
- 新檢查喺 `tests/check_crops.py`（跨欄）同 `tests/check_adjudication.py`（睇唔到、最尾兩行）。

## Unreleased — `--same-print-marks`：兩個引擎印刷字一致嘅 block 唔問標點（issue #6）

- `proofread_pages.py` 加 `--same-print-marks`（預設關）：規則 agreedMarks 因為空位有其他印刷字（數字、拉丁字、註號）而唔收嘅 block，如果兩個引擎喺每個空位讀到嘅印刷字一樣，就照用引擎 A 嘅標點，唔出 census／punctuate 題。比較時唔計註號：兩個引擎寫法同讀錯都唔同，而註號另外還原。
- 記錄：封條記 `samePrintMarks: "on"`，入 provenance，開咗同關咗嘅頁互相唔算 current；放行嘅 block 喺 `censusNotAsked` 記 `engines-agree-print`。
- 實測（`--backend packet`，Claude Haiku 5.5 答題，benchmark 28 案 50 頁）：一本現代橫排書嘅 8 案（基線同開關各跑兩次）第一次問嘅標點題 59 → 36，校對時間同輸出 token 各少約 24%，純正文頁少約 45%。直排書同無標點古書基本上冇 block 放行，全集問題 730 → 709。七項指標都喺逐次波動之內（CER 14.79% → 15.00%，不計標點 14.82% → 14.85%）。
- 新檢查喺 `tests/check_agreed_marks.py`。

## Unreleased — `--effort-policy`：按題型指定第一次問用邊一級 effort（量度用）

- `proofread_pages.py` 同 `book_profile.py` 加 `--effort-policy KIND=RUNG,...`（`PROOFREAD_EFFORT_POLICY`），淨係 `--backend http` 用，預設冇：唔設就同以前一模一樣，每條題都由 `--reasoning-effort` 嗰級問起。設咗之後，列出嘅題型（例如 `census=low`）第一次就用嗰級；`--reasoning-effort` 仍然係上限，冇列嘅題型同所有重試照用上限，撞 `max_tokens` 照舊向下一級。寫錯題型或者級別即刻 exit 2。
- 記錄：每頁封條多 `effortPolicy`（只喺有設嗰陣），run report 亦有；run report 每類題多一個 `efforts`，數每一級問咗幾多次。同 `reasoningEffort` 一樣只係記錄，唔入 provenance、唔入 packet 題目 ID，所以唔會令已封嘅頁過期。
- 修正 run report 漏咗合計每頁 `calls.*.efforts`：逐題型、逐級相加，仍然只計今次完成嘅頁；續跑略過嘅頁唔重計，舊記錄冇 effort 資料就唔推算。
- 呢個係 issue #2 第 2 步嘅機制，**未有建議值**：邊類題可以調低而唔掉字、掉標點，要喺有本機模型嘅機器逐類對 golden 量度先知。
- 新檢查 `tests/check_effort_policy.py`（本機 stub server，包括由頭跑一次 `proofread_pages.py`）。

## Unreleased — macOS 首次安裝先問用戶揀引擎

- Mac 有內置嘅 Apple Vision，所以 macOS 首次安裝（冇傳 `--engine`，`env.sh` 又未有上次揀嘅結果）喺下載任何嘢之前先問：`--engine auto` 由 Hugging Face 下載 HunyuanOCR 同 PaddleOCR-VL（Apple Silicon 約 5.7 GB），定係 `--engine vision` 淨用 Apple Vision（唔下載模型，約 0.2 GB）。喺 terminal 跑會列出兩個選擇等你答 `1` 或者 `2`；冇 terminal（agent 嘅 shell、pipe）就停低，exit `2`，`ACTION REQUIRED` 行講明兩個 `--engine` 同各自要幾多磁碟，agent 問咗用戶先加 `--engine` 重跑，唔代用戶揀。`--check` 未揀之前報同一段。
- 揀過一次記入 `env.sh`，之後唔再問；Linux／WSL2 照舊唔問、兩個都裝；`--engine hunyuan` 照舊係明確覆蓋。
- `SKILL.md` 三講明 agent 見到嗰行要問用戶；四〈單引擎〉補返淨用 Apple Vision 嗰陣第 2 步跑 `run_apple_vision_ocr.py`。`references/local-ocr.md` 嗰條 Vision command 原本漏咗 runner 一定要嘅頁範圍，照抄會 exit 2：兩處都寫返 `--first-page 1 --last-page N`。
- 實機驗證（Apple M5，macOS，2026-11-16，由清空狀態開始）：`--engine vision` 32 秒裝好、佔 27 MB；`--engine auto` 下載同核驗約 9 分鐘、試跑 32 秒、佔 5.8 GB（包括約 1 GB uv cache）；兩條路線 `--check` 都報 `ready`，唔傳 `--engine` 重跑唔再問。

## Unreleased — GPU 交接：模型本身停咗就唔郁；顯存唔夠就講邊個佔住

- 停咗模型之後顯存仍然唔夠（exit 1，RESULT 行 `GPU FULL`）：照舊即刻開返模型，另外逐個列 GPU 上面嘅 compute 程序（pid、使用者、程序名、MiB、systemd unit）同釋放佢嘅命令（`systemctl --user stop UNIT`、`kill PID`）。技能乜都唔殺、唔停，由用戶決定。
- 修正：call 開始嗰陣模型本身冇開（例如用戶 `systemctl --user stop` 咗），以前交接照樣行一次停模型，最尾仲會開返佢（`OCR_PHASE_SWAP=always`、GPU 俾其他程序佔住、或者讀唔到顯存都會中；安裝程式嘅 GPU 測試行同一條路）。而家先睇模型開咗未（health 網址答到、個 port 有嘢 listen 緊，或者 GPU 上面有佢嘅程序）：冇開就唔停、唔開、冇暫代服務、冇 watchdog，GPU 夠位就直接跑，唔夠位就 exit 1 `GPU FULL`，列出佔住嘅程序。`status.json` 寫 `left-down`，下一次 call 唔會當係交接留低而開返佢。RESULT 行尾 `[gpu: …]`、`status.json` 同 `timings.json` 嘅 `lastWindow` 記低模型本來開咗未、有冇停過同開返。冇 `OCR_PHASE_HEALTH_URL` 就分唔到，照舊。時間預算、exit 75 同 `--round` 照舊。

## Unreleased — 唯一通用技能：併入 qwen 版、雙引擎安裝、GPU 交接

- 併入 jyut-ocr-qwen：搬入佢嘅回歸測試（54 個 check）同 `run_ocr_phase.py`、`wait_for_run.py` 等工具，`tests/run_all.py` 一個入口跑晒 unit test 同 check。qwen 版封好嘅頁同舊底稿照收，唔使重新校對。
- 安裝程式預設裝兩個引擎：HunyuanOCR 用 llama.cpp b10969 官方 CUDA 版，PaddleOCR-VL 用鎖定嘅 wheel 同權重；有 GPU 就用 GPU，冇先轉 CPU；Python 3.12 由 uv 提供。改 `env.sh` 嘅值會將舊行留做註解，唔改檔案權限。唔支援原生 Windows，用 WSL2。
- agent 本身係本地模型時做 GPU 交接：等用緊模型嘅 client 做完先停模型；停咗期間由暫代服務回 503 叫 client 等；任何退出都還原模型（watchdog、lease）；GPU 忙或者時間唔夠就用 exit 75／76 叫 agent 遲啲再跑。
- 校對預設由當前 agent 答，亦可以經 HTTP 交本地模型（`--backend http`）。完整性 v2 改做開關，預設關。封頁照記 script hash，但唔用嚟判封頁失效。
- 刪走五個 release 工具。
- 按 jyut-ocr-bench 嘅標準答案修 phase 3：截斷咗嘅 Hunyuan 讀數切走重複迴圈之後照合併；結構步自己寫嘅直排表按引擎 B 嘅閱讀次序轉向，每次轉都出疑點；被拒格網嗰張表入面兩個引擎都讀到嘅標點，唔再畀標點鎖拒絕；黏住括號嘅圈碼註號唔再整唔見；表格格仔入面用 LaTeX 包住嘅註號寫返淨註號。
- 驗證：全套測試（35 個 unit test 檔共 658 個測試，加 54 個 check）通過。jyut-ocr-bench 28 案（評分器 samples-4.0.0）：本地 qwen3.8-27b 實測上一版，CER 寬容異體字計標點 15.98%，qwen 版 18.25%；本版用同一批模型答案重放係 14.42%。

## Unreleased — 完整性 v2 與可靠 packet 續跑

- 統一全頁區域與保留 features 契約；家具留喺區域證據，表格支援完整標籤或原圖格網與候選格位映射。疑點按 ID 逐項覆核，原圖、inventory、候選同契約版本共同綁定；v1 僅供歷史讀取。
- inventory／review 各最多一次格式修正，附原回答雜湊、錯誤代碼及欄位。未解視覺疑點維持未完成；被拒表格保留候選與原因，唔以純文字回退冒充成品。
- packet wave 不可改寫、答案原子套用、相同答案可重播、衝突拒絕；用量按請求／回覆身份去重，未知值保留 null。並行預設上限 8，使用者可指定 16，受平台容量約束。
- 程式與合成頁驗收同真實識別準確率分開；今次修復唔宣稱 CER 改善。

## Unreleased — 通用技能與跨平台安裝

- 將唯一技能來源移到 repo 根目錄，保留 `SKILL.md`、`scripts/`、`references/` 同可選 metadata；移除 Codex plugin／marketplace 包裝。更新現行文件、相對引用、安裝入口及測試，保留既有內容完整性修正。
- 新增 `install.sh` 統一入口，分開技能安裝、可選 custom-agent adapters 同 OCR 環境初始化；區分 Gemini CLI／Antigravity 位置，支援指定技能集合目錄、只讀檢查、備份及舊版 manifest 復原。
- 固定 Hunyuan BF16 模型／projector 同 llama.cpp commit；Linux／WSL2 支援 CUDA／CPU，macOS 可選 Vision 或 Hunyuan Metal／CPU。獨立 venv、下載校驗及實際單頁 OCR smoke 決定就緒狀態，新環境預設一個 slot，保留已有配置。
- 核心按讀圖、檔案、腳本同可選子 agent 能力選流程；普通 worker／順序執行毋須安裝特定角色。校對明確使用 packet，外部 HTTP 服務須顯式選擇；同一 writer 重讀唔當成獨立審核，人工標註介面係可選整合。
- 新增真實單引擎嘅免 profile packet 校對及 `finish_book.py --single-engine --ocr-a` 定稿入口；唔偽造第二底稿，唔跑依賴雙引擎嘅 repair，跨頁接續疑點同工作證據保留。
- 跨平台支援、fake-runtime 測試、真實引擎 smoke 同各 agent 技能發現分開驗證；唔以安裝／搬遷成功宣稱 CER 改善。

## Unreleased — 2026-11-12

- 表格方向改用原頁與格位證據，檢查右側標題格；`auto` 代表邏輯紀錄方向，`as-printed` 只保留物理格網。未知軸向／紀錄先後唔再按合計或者語意猜定，單行／單列表頭亦須有圖像依據。
- 加完整頁圖區域普查、排版前內容補讀及排版後 coverage 覆核，涵蓋兩底稿同漏、零漢字頁、數字／外文、算式、原印項號與註號。補讀綁定當前候選及原圖；未解覆蓋問題唔封成完成頁。
- 跨頁表格發現夾住嘅註文、續表題名及未知正文，嚴格檢查同表證據、相鄰頁、方向政策及精確表頭；保留註文與題名，模糊歸屬留待核圖，同欄數本身唔授權拼接。
- 共用受限數字註號 parser，識別數字上標、全形數字及巢狀圓圈包裝，保留原號；數學式、普通列項、損壞包裝同 HTML 表內未能安全配對嘅註號唔自動升格成 Markdown 腳註。
- 新增合成正反例同內容完整性驗證指引；程式測試、實書核圖與同條件識別錯誤率比較分開報告，唔將測試通過當成準確率改善。
- 修正測試發現嘅 Hunyuan broker 原有啟動競態：首個子程序釋放 owner lock、但未退出時唔再重複啟動，保留原程序退出碼及日誌錯誤。

## Unreleased — 2026-11-11

- 新增 checkpoint-replay 批次問答傳輸層（`_packet_client.py` 同 `apply_packet_answers.py`）：`book_profile.py` 同 `proofread_pages.py` 預設採用 `--backend packet`（`EXIT_PACKETS_PENDING = 10`），將雙底稿異字裁決、融合式 `census+punctuate` 標點切片、表格重建同整頁結構排版自動去重打包成 `wave-NN/bundle-01..KK.json`，交由 Antigravity／Codex／Claude Code 嘅並行 subagent 批次作答；重跑時 `0ms` 重播並通過所有確定性校驗（`verify_characters`、標點計數、表格字元校驗），同時保留 `--backend http` 兼容本機 OpenAI 介面端點。
- 補齊雙底稿確定性管線工具鏈：`survey_pdf.py`（新增 `--images-dir` 純圖片目錄模式同 `source_kind: "images-dir"` provenance 驗證）、`run_hunyuanocr_second_look.py`（接上 `SharedHunyuanClient` 同 `GPUQueue`，寫入 `schemaVersion: 2` 封條並為 `< 3` 頁短樣本過濾外邊界頁碼）、`book_profile.py`（新增 `<= 4` 頁短篇零校準快速路徑、單一區塊 `survey_notation` 去重與雙頁書眉偵測）、`proofread_pages.py`（新增 `--table-orientation {auto,as-printed}` 與 `U+16FE6 -> 。。` 規範化）、`stitch_tables.py`（新增無損 `--output-dir` 同尾頁留空邊界支援）、`repair_book.py`（確定性跨頁修復，包含 `U+16FE6 -> 。。` 同散文／管道表內原印 `* -> \*` 轉義，保護 HTML `<table>` 同多項 `* ` 清單）以及 `finish_book.py`／`clean_book.py`。
- 收緊腳註與圖版忠實保留預設：`_footnotes.py` 預設保留原印數值編號（如 `[^240]`，`--renumber-footnotes` 為 opt-in），支援單頁部分配對同跨 1 頁孤立註號回溯；`finalize_page_markers.py` 預設將腳註留喺頁底原位（`--relocate-footnotes` 為 opt-in）；`audit_ocr_markdown.py` 識別已有定義嘅行內 `[^label]` 腳註引用，唔再誤報 `low-confidence-bracket`；`finish_book.py` 預設關閉 `_figures.py` 裁圖刪字（`--extract-figures` 為 opt-in）。
- `install_local_adapters.py` 升級至 schema v3，新增 `gemini-skill`（`~/.gemini/config/skills/jyut-ocr-proofread`），連同 Codex／Claude 共 7 個本機目標支援安裝、備份、回滾同 `--check` 驗收；單元測試擴充至 214 個，並加 `test_generic_text.py` 防止特定語料或評測書名滲入通用技能。

## Unreleased — 2026-10-25

- 補底稿→校對稿差異覆核同人工回饋指引：分開可見讀法退步與可證實因果、字位疑點與整句／同名提示、actionable closure 與未定字形。疑點交接綁定版本及精確出現位置，錯框後重核；保留無需修改嘅對照，唔新增實書字詞替換或聲稱跨材料準確率提升。
- Hunyuan／Paddle owned runners 預設使用同一個每用戶 FIFO GPU queue，以穩定 guard 同有 kernel lock 嘅順序 tickets 協調跨 session 載入。Lease 只覆蓋 OCR 批次，子程序退出先交棒；繼承 descriptor 防止 parent 突然消失時提早釋放，Linux parent-death 設定亦補檢查同競爭窗口。
- 加 queue directory／resource／wait-timeout 設定、等待狀態同 report；排隊超時／載入前 deadline 到期記 deferred，冇其他錯誤時 exit 75，cache-only resume 同外部 endpoint 唔入 owned queue。排隊唔接管 NInfer 或其他未參與程式。
- Hunyuan 新增預設共用 broker：私有 Unix socket、自動啟動／同設定發現、session 輪流分派、全局 slot 上限、單 client 取消隔離、最後 client 離開後卸載。其他 backend 等候時停止新 membership，現有批次完成先 FIFO 交棒；可退回 dedicated 模式。
- 新增 `--server-slots`／`--workers`，每 slot 保留相同 context，並封入底稿設定身份；request 固定 seed／關閉 prompt cache，並行輸出仍須核圖。Hunyuan backend 改由 fresh interpreter 安裝 parent-death signal，避免多線程 broker 使用 fork preexec callback。
- 加獨立多程序共用、首 client 被殺、失敗／截斷、deadline／啟動競爭、Paddle 交棒、16 client 上限與 parent-death 測試；本機 GPU 實測另保存，唔將單次吞吐或正常完成當成全書準確率。
- 借鑑 OCR pipeline 嘅通用失效機制，收窄文類詞表、語法黐段、套語拆 heading、金額單位同家具刪除嘅固定假設；引擎一致／通順只作線索，容許兩候選皆非，裁圖缺字同重複字串要返完整頁圖判斷。
- 補逐書標點／版式觀察、標點漏／增及數字／外文／註號核對；普通網格與 HTML 合併格分流，保留複合 list item，明確既有 deterministic auditor 嘅格式覆蓋限制。
- 將完整 release sequence 搬到專用 reference，釐清 basic 逐頁記錄同多 agent／封存要求；測試規則須有正反例，冇跨材料驗證唔宣稱準確率普遍提升。

- 明確智能校對／最終內容審核層跟隨調用技能嘅當前 agent session；OCR backend 只提供底稿，唔綁定本機 Qwen、NInfer、GPU 型號或個人路徑，方便跨 Codex／Claude Code 同不同電腦重用。
- Ubuntu／Linux 預設以 HunyuanOCR 產生逐頁底稿，PaddleOCR-VL 按疑點／版面需要選頁補讀；保留 macOS Apple Vision、agent 逐頁視覺校對、分段寫稿同獨立覆核。
- 從本機 OCR pipeline 移植獨立 runners，保存原始模型結果、render／tool／profile 身份同 JSON／TXT 完成記錄；加強 Hunyuan 路徑設定、截斷回覆拒收同續跑設定核對。
- 補本機模型路徑、單 GPU 順序執行、共享服務 ownership、各引擎座標／confidence 語義同部分頁第二底稿嘅 coverage 指引；runner 唔會自動接管 NInfer。

## Unreleased — 2026-08-25

- 將長書 LLM 並行整理成平台中立 role contract：主 agent 先封互斥 scan assignments，segment writer 只寫自己輸出並封 SHA 後 STOP-WRITE；另一個 read-only visual auditor 逐頁核原圖同 segment interfaces，任何 audit 後 patch 都要重新 seal 同重核受影響頁／相鄰頁。
- 新增 Codex／Claude Code custom-agent adapters。Codex 刻意省略 `model`／`model_reasoning_effort`，由 explicit spawn override、`[agents]` default、parent value 依次解析；Claude 明寫 `model: inherit`，同時保留平台級／per-invocation override 嘅較高優先權。通用 skill 唔 hard-code 平台 model ID，LLM children 同時數量亦唔超過 runtime slots 同八個兩者中較小者。
- 新增 `run_apple_vision_ocr.py` 同 `apple_vision_ocr.swift`：最多八個本機 Vision process、連續或選頁 selector、逐頁 staging／atomic promotion、hash-bound resume、partial-failure report 同顯式 overwrite；Apple Vision 只係 draft，亦無任何 Tesseract fallback。
- 新增 recoverable local installer：Codex／Claude skill 同兩個 Claude agents 用 exact symlink；因目前 Codex custom-agent sensitive read 拒絕 leaf symlink，兩個 Codex TOML 用 SHA-256／mode 封存嘅 regular-file projection。所有被替換 object 先移入 timestamped backup，manifest 封 absolute paths、regular-file hashes／modes、directory modes、nested symlink link text 同 projection bytes，source／target／backup overlap、安裝失敗 rollback、restore drift 全部 fail closed。
- 補 selector／worker cap／resume／partial failure／adapter model policy／link-copy drift、backup、rollback 同 restore regression；macOS 真 Vision performance 只作 opt-in smoke／benchmark，唔用易波動時間門檻代替正確性。Codex auditor 保留 forward-compatible `sandbox_mode = "read-only"` 宣告，但已記錄目前版本唔套用 role-level sandbox override，只有 effective read-only parent／child 先可當成硬只讀 audit。

## 0.2.5 — 2026-08-06

- 將星號題名／題注整理成 optional source-apparatus gate：`audit_title_notes.py` 按 configurable main heading level 比對 `*`／`**` marker arity，列 missing／orphan，同時封 target／boundary、generator toolchain hash 同 invocation profile；逐 heading ownership 仍由人核圖。
- Cross-page note audit 將所有 note→running-body transition 預設列為 actionable，刪除任何特定語言或來源句式嘅自動 exception；只有 project-local transition review exact seal 目前 target、boundary、前後文字、獨立 reviewer 同 visual evidence 先接受。
- 新增 `build_notation_inventory.py`，逐 scan 記錄通用 residual symbols，並封目前 target／authority、可選 literal-only inventory profile、generator toolchain hash 同 invocation profile；metadata、code、單行 HTML comments 同 Markdown links 唔當來源 notation，星號題名註只喺明確啟用時記錄。
- 新增 canonical `ocr-review-amendment` v1 schema同 `validate_review_evidence.py`：唯一 pass status、safe relative paths、current content／render／evidence hashes、非空而相異嘅 before／after rows、writer／verifier independence、changed＋immediate-neighbor coverage同零 unresolved全部 fail closed；before／after 嘅 baseline/current 語義關係仍由 reviewer 證明。
- 新增 `plan_release_rebuild.py`：重算 active included artifacts，找 direct hash mismatch／可由 discovery 重建嘅 missing，再沿 reverse dependency graph 傳播 transitive stale，輸出 dependency-first rebuild order；scope 外 missing path 直接 invalid，JSON output 亦拒絕 plan、declared artifacts 同工具嘅 hard-link／symlink／case alias；exit 0／1／2 分別代表 clean／stale／invalid。
- 補充跨冊 byte-identical evidence import、projection→amendment→current chain、typed dependency方向，以及 annotated／final audit CLI profile；final mode唔再誤傳 annotated-only expected-count／boundary options。
- 補 generator snapshot／typed dependency 規則，避免 planner 因工具未入 plan 而將舊 schema report 誤當 current；JSON report path 亦拒絕 target／authority／boundary 嘅 symlink、hard-link 同 case alias。
- 新增 profile routing 同核心更新門檻：單一材料嘅書名、scan、專名、符號同來源句式留喺 project contracts；核心只吸收跨材料重現或格式無關嘅資料遺失／fail-open／provenance 問題。Regression tests 全用 synthetic fixtures，實書只作外部 forward test。

## 0.2.4 — 2026-08-05

- 長目錄 reconciliation 新增明確 `reconciliation_scope` 同分冊／分卷 partitions；合訂本只交付其中一冊時，範圍外目錄項仍要逐項記錄 disposition。只要 active closure 出現 TOC reconciliation 專用 evidence type 就強制要 scope，唔可以靠省略欄位繞過。
- 新增 typed release evidence lifecycle：sidecar 明分 `active`、`historical`、`superseded`，active artifact 唔可以係 provisional／failing；dependency graph 要無環，並用 canonical subset hash 避免 ledger 互相封完整 hash 形成循環。
- 新增 `assemble_ocr_segments.py`，按 sealed page manifest、ordered segment SHA-256、連續 scan ranges 同 LF-only separator 重建 combined annotated；逐段同整體 markers 都要 exact 對上 `marker_expected`，dry-run 可 byte-for-byte replay 現有輸出。
- 新增 `validate_release_evidence.py`，驗證 `release-plan.json` 對所有 discovery 命中嘅 release artifacts 做 `classification: include`，或者 `exclude` 加理由；同時驗 dependency closure、singleton roles、supersession、typed scope evidence、partitions 同 canonical subset hashes。
- Finalizer receipt 升級至 schema v2，強制唯一 `release-plan` role並自動封存 plan-order active closure；output／receipt 唔可覆寫任何 classified evidence或新命中 pre-final globs。Final audit 會重驗目前 plan、全部 classified hashes同 exact closure；legacy v1 receipt只可作遷移診斷，唔可再封關。
- Annotated audit 會喺提供 page manifest 時即時核對 `marker_expected`，唔再等 final receipt 先發現 marker policy 錯配；notation policy 可逐類 inventory 全形方括號、六角括號、角括號、缺字方框同明示存疑符號。
- `audit_ocr_markdown.py` 新增 opt-in `--json-report`，輸出穩定嘅 machine-readable errors、warnings、accepted exceptions、issue counts 同 exit status，方便 release plan 封存及重播。
- 測試由 35 個增至 69 個，新增 assembly／release-plan／receipt v2、schema spoof、path alias、TOCTOU、legacy migration 同 adversarial closure regressions。

## 0.2.3 — 2026-08-05

- 長目錄先按每個 TOC scan 建立末端印刷頁碼 inventory，保存黐入題名／日期嘅 recovery，同時逐頁獨立核對全部引用；唔再用 parser 輸出或總項數代替目錄完整性。
- 將目錄條目、目錄分組同正文結構分成獨立 ledger：只比較有明確納入／排除理由嘅 TOC-eligible headings，並以正文分隔頁、running header 同首項互證分組起點。
- `source-verified-*` 新增獨立 challenge gate：除純註號等 routine apparatus 外，題名、日期、人名、官職、數字、機構等實質異文，以及所有頁碼差異／異序，都要由另一 reviewer 同時核對 TOC 同正文頁圖。
- 頁碼差異用 `page-exact`、`fixed-ocr-reference`、`source-printed-ref-differs-from-body-start` 等細分類，保留原目錄頁碼、正文實際起頁同鄰頁證據；唔再壓成普通文字異文或靜靜改正來源。
- Independent review 後嘅 late patch 要重新核對受影響頁，或者用包含具體 before/after、scan/render hash、獨立 verifier 同 evidence hash嘅 amendment ledger封存；before/after內容關係由 reviewer同 evidence證明，所有 release decision sidecars都要加入 assembly receipt upstream。
- `audit_ocr_markdown.py` 同 `finalize_page_markers.py` 新增 opt-in `--summary-only`，長書封關時可隱藏逐頁 context，同時保留 deterministic issue counts、exit code、write／receipt 摘要；預設 verbose 行為不變。
- 測試由 33 個增至 35 個，新增兩個 summary-only regression。

## 0.2.2 — 2026-08-03

- Boundary contract 新增反向保護：open list item 後嘅縮排續文或 nested item 若誤標 `join`，annotated audit 同 finalizer 都會拒絕，避免 `.strip()` 吞走 `list-cont`／`list-new` 語義後輸出 hard break 或黐爛子項；receipt 同時保留經視覺確認嘅 paragraph／structural list break，唔放寬無證據嘅 loose-list audit。
- Final audit 使用嘅 notation policy、source-exception ledger 同 page manifest 必須由 assembly receipt 封存；另傳未記錄 interpretation 檔會報 `assembly-audit-input-unsealed`。
- 新增 `--page-manifest`／`--source-pdf` provenance gate，逐次重算目前 PDF SHA-256、完整 `1..N` scan sequence 同每個 render hash；帶 receipt 時亦會對賬 sealed annotated markers，刻意略去頁必須以 sealed `marker_expected: false` 聲明。
- Review evidence 改成單向狀態：sidecar 重建唔可以將 `independent-checked` 降級，並要求 reviewer 同 writer 分開、覆核 range 同 evidence ledger／report hash 可追溯。
- 長目錄對賬要求逐項 disposition；TOC／正文數量相同唔代表文字完全一致，圈號省略、跨頁日期、題名／頁碼／排序真差異要各自按圖保留，唔可以留低未關閉嘅泛稱 `review`。
- 測試由 27 個增至 33 個，新增 list-cont／nested list 誤標、獨立 lists 結構邊界、unsealed final-audit input、PDF/render provenance 同 sealed marker coverage regression。

## 0.2.1 — 2026-08-03

- 將長書實作經驗整理成可選 release gate：先讀來源凡例、收齊所有 writer／auditor、凍結 segment hash，最後一個 patch 後重新 assemble、finalize 同完整 audit。
- 新增 `notation-policy.json` 支援；凡例證明 ASCII `[ ]` 係來源編者訂正時，final audit 唔再將佢無條件當 OCR confidence residual。
- 新增 stack-aware 全形 delimiter audit，同 duplicate-safe exact line／column／symbol `source-exceptions.json`；原書本身未閉或錯序標點要保存 scan page、整行文字同核圖說明，失效 exception 會報錯。
- Finalizer 新增 SHA-256 assembly receipt，同 ordered `--upstream` dependencies；final audit 可以偵測 late segment／manifest／annotated patch同 receipt 後 final 修改，並由目前 annotated + boundary contract byte-for-byte 重建 final，唔靠 mtime。
- 結構 audit 擴充至 tight-list item／continuation 空行、Markdown table 欄數、space-before-tab、missing final newline 同 EOF 多餘空行。
- 新增高信心 correction propagation、同名／相似平行篇章互校同 multi-pass OCR disagreement 工作法；全部只產生逐頁核圖候選，絕不自動同步或投票定稿。
- 收緊一次性 front matter／正文 apparatus 判斷：全書轉錄通常保留扉頁、英文題名、CIP／版權、凡例、圖說同來源資料，唔同重複頁面家具混為一談。
- 測試由 14 個增至 27 個，覆蓋 hash freshness、hard-link／case alias 防護、notation policy、精確來源例外、delimiter nesting、nested list、table 欄數、code fence 同完整 EOF whitespace。

## 0.2.0 — 2026-08-03

- 單一長篇 PDF 可以按連續頁段分派，但每個 writer 只寫獨立 segment；主 agent 獨佔最終 Markdown、assemble、頁段交界同 final audit。
- 新增 long-book workflow，分開記錄 PDF scan page 同 printed page，覆蓋 spread、旋轉、空白、完整／局部重複、缺失印刷頁同 page provenance。
- 明確規定原 PDF 真缺頁時嘅同版復原流程：先排除漏 OCR／重複／render 缺檔，再以可靠同版來源交叉核實，保存 evidence manifest，披露未能視覺核實嘅標點同行款。
- Apple/macOS Vision 可批量建立底稿，但直排閱讀次序、淡字、表格、註腳同跨頁內容仍須逐頁對 PDF。
- page marker 新增 `start`／`join`／`paragraph`／`structural`／`list-cont`／`list-new` 顯式 contract；finalizer 無完整 JSON manifest 或另一個 output path 會拒絕寫入，唔靠空行猜邊界，亦唔覆寫 annotated 底稿。
- final pass 新增結構 invariants：條號連續、人名或項目實數、A–J 類枚舉、表格列數及總數。
- 新增純 Python stdlib `audit_ocr_markdown.py` 同 `finalize_page_markers.py`，用 deterministic 檢查代替反覆即席腳本。
- 補充 untracked 新檔令 `git diff --check -- TARGET.md` 可能成為 no-op 嘅盲點，要求另做 trailing-whitespace audit。
- 更新 skill description、UI metadata、plugin metadata、README 同詳細 reference 導航。

## 0.1.2

今版將 `jyut-ocr` repo 入面近期香港中文 OCR 校對守則整理成可重用 Codex plugin skill，重點係令 agent 做長文校對、缺 OCR 補漏、表格整理同 final pass 時有一致嘅流程。

### 2026-06-16 同步摘要

本 repo 版本已同本機已安裝嘅 `jyut-ocr-proofread` 0.1.2 skill 對齊；主 `SKILL.md`、plugin metadata、詳細規則 reference 同常見陷阱 reference 都保持一致。呢次文件同步將最近新增同收緊嘅工作約束整理成以下幾類：

- Skill metadata 維持穩定 ID `jyut-ocr-proofread`，方便 Codex 以 skill 名稱載入；目標 repo 如有 `AGENTS.md` 或其他本地指示，仍然要先讀並優先跟本地指示。
- 多份互不重疊 Markdown/PDF pair 時，可以用並行 subagent 分工；主 agent 要先列出檔案、PDF 頁數或頁段、page marker 狀態、表格/尾頁/缺 OCR/跳頁風險，並避免多人同時改同一份 Markdown。
- 主 agent 要 review subagent 輸出、處理衝突同遺漏，並統一做完成前驗證；未逐頁視覺對照 PDF，唔可以話全文已完成校對。
- OCR 來源次序收緊：缺 OCR、補漏 OCR 或 page marker 跳頁時，即刻用 PDF render 圖像做視覺轉寫，唔為任何 OCR 服務停低。
- GJ.cool 只作背景或極短時間輔助：有 token、API 正常、未爆 quota，而且 5 秒 timeout 內即時返到，先用作底稿再核 PDF；timeout、quota、auth 失敗、服務不可用就直接繼續視覺識別。
- 如果 GJ.cool API 回覆 `wait for ... seconds`，本輪 OCR/校對任務餘下部分即刻停止自發重試 GJ.cool；之後即使用其他 token 或 IP 可能重置用量，都唔主動再試，除非用戶另開要求測 API/token。
- Apple/macOS OCR、Gemini、Tesseract、OCR JSON 同外部資料都只係提示；PDF 圖像仍係最終依據，外部研究唔可以覆蓋 PDF 原文。
- 深入改文前要查 page continuity：page marker 是否順序、有冇覆蓋最後非空白頁；無 marker 就用 render 圖或 contact sheet 建立 section-to-page map。
- 缺 OCR、跳頁或 residual scan 乾淨但內容唔連續時，要先當成內容缺漏處理，找本地底稿或直接從 PDF 圖像補回，唔可以只做格式 pass 就當完成。
- Markdown 結構規則更具體：page comments 只作臨時定位，完成前要刪；普通正文應一段一行；heading、bullet list、table、跨頁續文同 list spacing 要按 PDF 版面整理。
- 表格、票據、圖、淡字空白頁、尾頁印章等都要 render 放大檢查；直排表格要按版面理解欄列，必要時轉成 Markdown table，唔可以照 OCR 行序硬排。
- 常見高信心錯字 reference 擴充到官職、人名、地名、套語、數字符號、條文清單、heading、舊式標點、尾頁視覺陷阱同合法拉丁詞，供長文同 final pass 使用。
- 完成前驗證固定化：掃低信心括號、page comments、OCR notice、拉丁殘留、常見頁面家具同高信心錯字；刪 page comments 後檢查 soft wrap；最後跑 `git diff --check`。
- 最終回覆要交代 PDF 頁數、page continuity、是否逐頁視覺對照、有冇補 OCR、有冇疑難字、有冇殘留 brackets/page comments/OCR notices、舊標點/合法拉丁詞處理、一段一行檢查，以及 `git diff --check` 結果。

Bundled references 亦保留長文、表格、尾頁、章程、速記錄、講義合訂同常見高信心錯字陷阱，供 final pass 或高風險頁段再讀。
