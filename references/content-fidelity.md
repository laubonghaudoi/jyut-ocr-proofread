# 內容完整性與評測回歸

喺雙底稿同漏、表格／註腳／原印項號遺失、用戶要求開 `--content-fidelity on`，或者根據評測修改技能時讀本頁。原圖係文字依據；評測只幫手定位失效機制。

## 開關：`--content-fidelity on`（預設關）

`proofread_pages.py` 預設唔問下面三條整頁題，頁面照封、冇呢份紀錄。用戶要求先加 `--content-fidelity on`；冇 A／B 比較，唔使自己決定開唔開。開咗每頁多三條睇整頁原圖嘅題（A 套嘅 bundle 會多咗 `page-inventory`、`content-recovery`、`coverage-review`，每頁至少多睇三次整頁圖）：

1. **整頁清點（page-inventory）**：連引擎話空白嘅頁都問；列出區域、要保留嘅數字／外文／算式／註號（features）同疑點（issues）。
2. **排版前補讀（content-recovery）**：對住清點同原圖補返漏咗嘅內容，改動要精確定位。
3. **最後覆核（coverage-review）**：對住最後文字逐區核 coverage，表格要逐張映射。

佢哋點處理定唔到嘅嘢：

- 答案唔合契約，可以要求改一次（綁錯 hash、抄錯 contract 版本呢類可以改）；改完都唔得，或者覆核唔係 complete，嗰頁**照封**，記 `content-fidelity-unresolved` 疑點（`contentFidelityStatus: doubt`），唔當失敗頁，亦唔繼續問嗰頁後面嘅題。
- 清點答「空白」而引擎讀到字（或者讀到冇漢字嘅西文、數字），唔會清空嗰頁：照合併或者照圖轉錄，記疑點。
- 補讀同覆核每個改動記入封存頁 JSON 嘅 `decisionChanges`（`decision: contentFidelity`，`rule` 係 `content-recovery`／`coverage-review`，`evidence.adopted` 講有冇採用）；覆核唔完整而又會蝕 feature 或者清空內容嘅改動唔採用。
- 表格 placeholder（`⟦Tn⟧`）喺格式化期間保留，唔會俾補讀當字刪。
- 縫埋咗嘅跨頁表格頁（`tableStitch`）同帶住疑點封嘅頁，`finish_book.py` 照樣驗得過。
- 續跑：開住嘅 run 唔收冇呢份紀錄嘅封存頁（報 blocked，要 `--overwrite`），但開關出現之前封好、v2 紀錄完整嘅頁照收（見下面〈完整性契約 v2〉最尾）；關住嘅 run 兩種都收。

## 先分清失效階段

逐段比對原圖、兩份 raw OCR、合併稿、標點／排版輸入、逐頁成品同全書修理成品，定位第一次丟失或改錯嘅位置。上游已剝走嘅數字，唔歸咎最後格式化模型；結構錯序造成嘅 CER 同逐字誤認分開記。模型完成、決定論驗證通過、兩引擎一致，都唔代表視覺正確。

`verify_characters` 嘅舊有檢查只約束 CJK 新增，唔證明數字、外文、算式、標點、註號或者文字刪除正確。要逐項保留已核圖內容同位置；格式化可以改 Markdown 包裝，但唔可以吞掉原印項號或將算式移去另一格。修補漢字漏讀要先有圖像確認，唔直接放寬全局字元守門。

## 從整頁發現共同漏讀

- 先讀完整原頁，列出正文、題字、表格、圖說／樹圖文字、註文、外文及家具嘅區域；再比對底稿覆蓋。頁面邊緣同書口位置只係線索，一次性題記／出版資料唔自動刪。
- 對冇底稿嘅區域亦要睇原尺寸或清晰裁圖；裁圖失位、空白或者缺第二引擎唔係「原頁冇內容」。漢字零字頁仍可能有數字或外文。
- 圖像確認嘅補讀綁定目前原頁、候選文字及精確位置；模糊、定位不唯一或證據過期就記疑點。唔用另一頁、語意、常用語或連續編號補字。
- 定稿前再以完整原頁核對 coverage，包括兩底稿共同有但一齊認錯嘅字；區域清單同裁圖補讀本身唔代替逐頁文字覆核。

## 完整性契約 v2

開關開咗先有呢份證據。現行證據用 `schema_version: 2` 同 `contract_version: "content-fidelity-v2"`。
`regions` 包括家具及插圖；`features` 只記交付內容嘅每次出現。家具數字、頁碼、印章文字寫喺所屬 region 嘅 `evidence`，唔放入 features。違反時報具體欄位，唔靜默刪除。

表格 feature 嘅 `cell` 二選一：

- `kind: "labels"`：`row_label`、`column_label` 都係完整可見標籤格文字，包括細字同附加年限。空標籤唔係萬用匹配，簡稱或 substring 唔代替完整標籤。
- `kind: "grid"`：提供 `table_region_id`、從零開始嘅 `row`／`col`、原圖總格網 `rows`／`cols`，以及 `rowspan`／`colspan`。無表頭同合併格用呢條路；未能定位就記 ownership 疑點。

所有表格區域覆核都須提供 `table_mappings`，包括全中文、冇數字 features 嘅表格；逐個來源 table region 對應修改後候選嘅 `table_index`（從零起計）、`source_rows`／`source_cols`、`orientation` 同圖像證據。`identity` 保持原格網，`transpose` 只喺 `auto` 有圖像依據時可用。驗證器檢查格位後計算 `result_sha256`，綁定套用 edits 後嘅實際內容；模型毋須自行計算 hash，如有提供則必須吻合。同樣文字放錯格或另一張表唔會通過。

疑點用 `issues: [{id, region_id, kind, evidence}]` 表示。`kind` 可用 reading、crop、coverage、ownership、furniture；全頁範圍未明時 `region_id` 可為 null。覆核用 `resolutions: [{issue_id, status, evidence}]` 逐項回覆，status 係 resolved、unresolved 或 excluded。確認只涉及排除家具嘅疑點可關閉；正文疑字、未知範圍裁切、格位未解仍阻止完成。單寫 `verdict: "complete"` 唔會解除疑點。

覆核必須綁定 `source_image_sha256`、`inventory_sha256`、`candidate_sha256` 同契約版本。答案入面抄錯呢啲 hash 或者版本，算契約錯誤，可以用嗰一次修正請求改；已封存嘅證據屬於另一幅圖或者已經過期，就唔收，唔用格式修正掩蓋。inventory 同每次 review 各最多一個契約修正請求，保存原回答 SHA-256、錯誤 code 同欄位 path；真正視覺未解唔反覆問至得到 complete，記疑點。被拒嘅表格候選同原因保留，退回純文字底稿亦唔會繞過格位驗證。

舊 v1 證據可歷史讀取，但唔可以標成 v2 已核驗，亦唔沿用舊完成封條冒充新契約。開關出現之前，技能每頁都行 v2 三問，嗰啲封條有完整 v2 記錄、冇 `contentFidelity` 開關 key；`--content-fidelity on` 續跑睇證據本身，唔睇有冇呢個 key：證據核驗得過就當 current，核驗唔過先重做。完全冇 v2 記錄嘅頁，on 續跑照舊唔當 current。續跑時沿用已封存、身份一致嘅回答；改候選或契約須另開請求修訂。

## 註號與字面符號

數字註號可以有上標、圓圈、HTML 或 LaTeX 包裝。只解析受支援嘅純數字包裝，保留原印編號；包裝解析唔證明佢係註腳。數學次方、正文枚舉同註腳要按版面、引用位置同註文分清。

有註文但缺引用時，返正文行間／句末裁圖定位；唔憑頁底定義反推註號存在。跨頁孤立註亦可能真係引用喺範圍外，保留原註文及未配對狀態。Markdown 正文用 `[^n]`／`[^n]:`；raw HTML 表格唔插入唔會渲染嘅 Markdown 引用，未有可靠 HTML 配對時保留可見原號並報未解。

正文／Markdown 表格嘅字面星號寫 `\*`；raw HTML 格內用 `*`，唔加入會顯示出嚟嘅多餘反斜線。檢查算式時核對渲染文字、運算符、數字同所在格，唔只比較 Markdown 原始 bytes。

格式整理要保留已核圖算式嘅寫法。現有守門會拒絕部分等值上標改寫（例如 `x²` 改成 `x<sup>2</sup>`）；遇到呢類情況保留已核圖候選同拒絕紀錄，唔為咗通過格式整理而轉寫算式，更唔可以將次方轉成註腳。

## 技能更新點驗收

1. 每個修補先用最小合成正反例驗證：正例修到真失效，反例保留原印怪字、真空格、數學式、獨立表格同未解註號。測真正輸出／資料保留，唔只測提示詞含指定句子。
2. 再做獨立行為測試：只交技能、原圖同必要底稿畀執行者，唔畀標準答案、預期修法或者先前結論。做答案比較嘅評估者同產生識別結果嘅執行者分開。
3. 比較固定輸入／答案／評分器 hashes、校對模型與有效設定、OCR 底稿、輸出階段及表格方向。舊 metadata 改過時另外驗 output hash，唔冒稱原封不動嘅 run。設定由 `as-printed` 改 `auto` 嘅收益同程式修補收益分開量度；換模型唔能夠直接歸因技能。
4. 分開報文字錯漏、表格格位／跨頁接合、註釋歸屬、未解疑點、耗時同用量。舊版同新版用同一版評分器重評；正在開發嘅新指標另列，唔混入改善百分比。
5. 已看過嘅難例只係 development／regression 集。跨書泛化要另用未參與修規則嘅出版物驗證；未跑實書或未有同條件對照時，清楚寫「通過程式／合成測試，識別錯誤率改善未量度」，唔由 audit clean 推論準確率提升。
