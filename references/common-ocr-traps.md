# 常見 OCR 陷阱

長文、雜訊多、表格多、或者語義可疑時讀呢份 reference。用嘅人係睇圖嘅校對者：A 套答 packet 題嘅 agent、人手轉錄嘅 writer（`structured-workflow.md`），同對住 `doubts.md` 核書嘅用戶。B 套（本機模型校對）嘅 agent 喺對話入面唔讀頁圖：呢度講嘅核圖由流程問模型，定唔到嘅寫入 `doubts.md` 交用戶。

## 高信心錯字

無括號都要按語境同 PDF 查：

以下例子只展示失效類型，唔係固定替換表，亦唔表示左邊必定正確。單一材料新見到嘅人名、書脊、頁面家具或錯詞寫入 project-local `correction-ledger`／`project-suspects.txt`；唔好因為完成一本書就繼續擴充核心例詞。

- 部件近似或缺筆，例如 `文/女`、`中/巾`；對照可見筆畫，唔只睇邊個詞較熟。
- 同音、搭配或上下文令錯字仍然讀得通；通順本身唔係保留底稿嘅證據。
- 淡字、標點黐字、欄邊截斷造成漏字／多字；候選可能涉及整段位置，唔只係一字換一字。
- **活字 180° 倒植**：鉛印書偶有一至數枚鉛字上下倒置排印；因筆畫清晰但正向無此字，OCR 常強配極生僻漢字或古文字。凡遇文義不通之生僻怪字而原圖字模清晰者，應將局部裁圖旋轉 180° 比對常見漢字；確認係常用字倒植後按正字轉錄，並於校勘筆記註明原印倒植。
- **空心缺字框 `□` 與漢字 `口` 之鉛模比對**：原印鉛字磨損漏墨、避諱空框或待填字格常被誤讀為漢字 `口`（如連續 `口口`），反之姓名或地名中真漢字 `口` 亦偶被誤標為 `□`。判別時須取同頁確定無疑之漢字「口」（或帶「口」部件字）比對字框大小、線條粗細與起收筆鋒，切勿單憑語境猜改。

兩引擎、既有成品同 reviewer 可以一致錯。比較候選時容許「兩者皆非」或暫未能判讀；唔凍結一致字，亦唔因某候選較古、較常見或由某引擎提出就優先採用。每本書重新觀察字形、標點、版面同各引擎嘅實際漏讀；觀察只用嚟安排核圖，唔變成全書自動裁決。

確認一個高信心錯字後，將錯形、正字、scan page 同圖像證據加入 correction ledger，再搜尋全書同形同一字之差嘅人名、官名、機關名同固定搭配。每個命中仍要逐頁核圖；唔好因為第一處已確認就全域替換。

## 校對稿亦可能新增錯字

- 有保留 OCR 底稿時，交付前比較底稿同校對稿嘅實質字詞、數字、標點差異；唔只比較兩個 OCR 引擎。將改動區塊交畀現有覆核者優先核圖，尤其原先通順、改後亦通順嘅讀法。呢個係核對次序，唔縮窄原有逐頁範圍，亦唔要求多開一輪模型。
- 對齊時分開 Markdown 包裝／閱讀次序重排同來源文字改動。漏段、多欄錯序或重複字串令對齊不唯一時，列為區塊疑點，唔用最近字串匹配冒充精確字位。
- 底稿同人工定案一致而校對稿唔同，可以記做「底稿→成稿退步」；呢個唔代表底稿永遠優先，亦唔證明 writer 曾讀過嗰份底稿。改字與否仍按來源筆畫，唔以原稿／多數票自動回退。
- 覆核過程要留意實際畫面能否分辨字形；先用完整頁圖核對閱讀次序同文字，若顯示縮小後睇唔清，按欄／橫帶放大並保留接界上下文。唔好只放大已有疑點，而將其餘細字當成已清楚判讀。

## 來源校勘符號同異常

- 先讀本書凡例。`[]`、`［］`、`〈〉`、`□`、`（？）`、註號同刪節號可能係編者訂正、增補、存疑或原件缺損，唔一定係 OCR residual。
- 同一系列各冊可以用唔同半形／全形政策；唔好將上一本 notation policy 複製過去。
- 日期矛盾、重複字、文句不通、未閉引號／括號先放大核圖。若原書確實如此，原樣保留，唔為語意或符號平衡改書。
- 原書用 `□` 表示破損時保留 `□`；唔由人名、日期或上下文猜補。
- 記錄 scan 同視覺依據；若適用嘅 deterministic gate 需要機械接受來源異常，先按下面嘅 notation policy／source exception 格式建 exact-location 記錄。Exception 只承認原書異常，唔係忽略未核對 audit issue，亦唔單憑來源符號升級 profile。

### Notation policy 同 source exception

將「凡例嘅完整文字解釋」同「audit 需要嘅逐符號授權」分開保存：前者可以係詳細 project-local 記錄，後者用嚴格 `notation-policy.json`。每個 key 都只授權自己嗰種符號；例如凡例明確證明 `□` 同 `〔?〕` 係來源標記，可以寫：

```json
{
  "white_square": {
    "meaning": "source-illegible",
    "scan_page": 7,
    "note": "凡例明定每個空方框代表一個不可辨字。"
  },
  "explicit_uncertain_marker": {
    "meaning": "source-uncertain",
    "scan_page": 7,
    "note": "凡例明定六角括號內問號表示編者存疑。"
  }
}
```

支援嘅 key 係 `ascii_square_brackets`、`fullwidth_square_brackets`、`lenticular_brackets`、`angle_brackets`、`white_square`、`rectangle` 同 `explicit_uncertain_marker`；每條 record 必須恰好有 `meaning`、`scan_page`、`note`。ASCII `[]` 只接受 `source-editorial`；其他 key 接受嘅 `source-apparatus`、`source-editorial`、`source-uncertain`、`source-damage`、`source-illegible` 或 `source-omission` 組合由 audit schema 限定。未聲明嘅 `□`、`▭`、`〔?〕` 會 fail closed；聲明其中一種唔會順便放行另一種。

Policy 只喺 final audit 使用；annotated mode 唔會用 policy 豁免命中，傳入 policy 亦會報 `notation-policy-requires-final`。先清走真正 OCR confidence markup，再由 final mode 按 exact policy 接受來源 notation。未有可靠凡例證據就唔好建立 policy；residual scan 有命中唔代表一定要刪，逐項分類後先決定。

Audit 會對 `“”`、`‘’`、`「」`、`『』`、`（）`、`［］`、`〈〉`、`《》`、`【】`、`〔〕` 做 stack 檢查。命中先回 PDF；確認係 OCR 錯就修，確認原書如此就寫 exact-location exception：

```json
{
  "exceptions": [
    {
      "code": "unmatched-opening-delimiter",
      "line": 1284,
      "column": 9,
      "delimiter": "「",
      "text": "原書呢一整行只有「開引號而未閉合。",
      "scan_page": 297,
      "note": "普通圖同放大 crop 都見到原書未印閉引號。"
    }
  ]
}
```

支援嘅 code 係 `unmatched-opening-delimiter`、`unmatched-closing-delimiter` 同 `misnested-closing-delimiter`。`line` 同 `column` 都由 1 起計，column 以 Unicode character 計；佢哋連同 `delimiter`、`text` 必須同目前 final Markdown 完全一致，而且一條 exception 只承認一個 issue。位置、內容或 issue 一改變，audit 會報 `stale-source-exception`，避免舊白名單永久遮住新錯。

Exception 由 marker-free provisional final 建立，唔可以先寫白名單再搵位置迎合：先唔傳 exceptions 跑一次 final audit，逐個 delimiter issue 記 exact line／column／delimiter／text 同所屬 scan；由另一位 reviewer 打開對應 individual original／full-resolution render，確認係 source-as-printed，先寫 exceptions 再跑 final audit，要求 accepted set exact 等於已核 visual set。之後文字或 boundary 有任何漂移就重做；舊 exception 唔可以靠相似字串移位繼承。

```bash
python3 scripts/audit_ocr_markdown.py final.md --mode final \
  --notation-policy notation-policy.json \
  --source-exceptions source-exceptions.json
```

## 平行篇章互校

- 同一冊或同系列重收同名電文、條例、呈文時，可按題名、日期、來源同正文相似度對齊，將逐字差異加入核圖候選。
- 平行文本好適合浮出無括號嘅人名、套語、漏字同一字之差，但只係 suspect generator。
- 不同報源、版次、刪節同標點可以造成真差異；每個版本仍以自己 PDF 為準，唔用多數票或自動同步。

## 假異文同目錄參照

- 目錄同正文兩邊都「勉強讀得通」仍可能係 OCR 漏字，尤其名稱、日期同固定稱謂。先同時核對兩邊頁圖；已啟用長目錄對賬 gate 時，涉及語義嘅異文須由第二位 reviewer challenge，唔好單靠字串差異封成 source variant。
- 圈號、註號、重複來源標籤等 routine apparatus 省略可以另行分類；呢個分類唔可以套用到名稱、日期、數字或正文實詞。
- 目錄末端印刷頁碼／參照容易漏抽、黐入題名、跨欄錯序或被正文頁碼覆蓋。逐 scan 核對原樣參照；長目錄對賬 gate 再按 `structured-workflow.md` 嘅〈對賬長目錄〉做 inventory 同 100% 獨立視覺 audit。TOC transcription 同 body mapping 分開，永遠唔改目錄原字去迎合正文。

## 官職、人名、套語

- 本書反覆出現嘅名稱、官職同套語可以產生核圖候選；唔將過往官文嘅詞表套用到其他文類。
- 例如 `令/合`、`已/己/巳` 都可以係真字；套語、語法或干支只幫助定位疑點，最後按頁上部件判字。原書真用罕見、過時甚至不通讀法亦保留。
- 人名、地名先查 PDF；其他出版物、現代字典同記憶只供查疑，唔直接改成本來預期嘅名稱。

## 數字同符號雜訊

- 拉丁字母、數字同符號既可能係真內容，亦可能由漢字、標點或污點誤讀而成；例如 `1`、`∵`、`≡` 唔可以按固定對照表替換。
- 日期、金額、單位、項號同註號逐處核圖；單位慣例唔能夠推翻印刷字形，例如原書印 `員` 就唔因金額語境改成 `元`。
- 條號連續性、欄列數同合計只用嚟查疑。原書可能有跳號、重號或算錯，唔為令數值吻合而補字、重編號或改金額。

## 清單同條文

- 章程同長議案要查項號連續性。
- OCR 常漏新 item 前嘅 `-`，或者將新 item 錯縮入上一 item。
- 縮入行以 `一...`、`甲...`、`乙...` 開頭時，要查 PDF 判斷係續文定新項。
- `第一條` 等項號只係 list 候選；是否獨立 item、附說或續文，要按縮排、對齊、留白同閱讀次序判斷。
- OCR 亦會將正文誤當 bullet；文義斷裂提示回到頁圖，唔直接還原成正文。
- 草案比較、原案／修正案等標籤可能係真結構，唔好未睇 PDF 就刪或重編號。

## 分段同 heading

- OCR 行尾語法未完只提示可能有軟換行；先核對閱讀次序、縮排、留白同鄰行，再決定合併。真換段、抬頭或來源半句唔可以因語法未完而黐合。
- 唔好只因兩行都長就合併；奏摺/呈文/請願入面 `督部堂`、`旨`、`上諭`、`皇上` 可能係版面抬頭。
- 速記錄發言人轉換係強分段訊號：`某號議員謂`、`督部堂代表謂`、`議長謂`。
- 套語重複可能係題名黐正文，亦可能係正文原樣重複。只有字級、位置、留白同篇章關係支持先拆 heading；唔由指定詞頭觸發，亦唔順手統一異體。
- 同頁可能交錯多份短文件；頁頂短句可能係新案，唔一定係上一段續文。
- 合訂講義通常以原書印出嘅課名／篇名作主要結構；逐處按版面判斷，唔好預設或硬編一套固定課目。
- 刪 `<!-- page_... -->` 後要查相鄰普通正文行，避免留下 Markdown soft wrap。

## 標點同外文

- 有啲 PDF 本身有舊式標點，唔係所有歷史文本都無標點。
- 保留 `。`、`。。`、`、`；唔好將 `。。` 自動正規化成 `。`。
- 每頁同時查漏標點、多加標點、形狀同位置；底稿完全冇標點都要核圖。符號可能被讀成漢字，亦可能由污點誤生，唔以全書密度或符號總數定對錯。
- **孤立圈號 `〇` 對句號 `。`、行側著重旁點與專名線**：舊式圓圈句號 `。` 常被成批誤讀為漢字零 `〇`；凡前後並非數字或紀年干支之孤立 `〇`，應核對原圖是否實為句讀圈 `。`。民國書刊常用行側密點（著重號）或人名旁波浪專名線，OCR 極易將旁點誤讀為逐字句號（如 `甲。乙。丙。丁。`）；須對照原圖字側位置清走假標點，只保留真正落於字下佔格或句讀位之標點。
- **直排內嵌西文專名與縮寫**：直排書括號內或行間夾排之西文人名、術語及縮寫（常順時針旋轉 90° 排印）極易被單引擎漏讀成空括號 `（）`、`（口口）`、`（□□）` 或拆成零碎字母。見空括號或可疑佔位符須返原寸圖逐字母核對大小寫與標點；若原書西文確有漏排字母或拼法訛誤，照原印保留並記於筆記，唔自動拼寫校正。
- **章末圓圈數字註標退化**：內文與章末／篇末採用圓圈數字（`①`、`②`……）對應之腳註，機校常將註標誤讀為實心黑點 `●`、`◎`、漢字 `一`／`自` 或無序列表 `-`，導致腳註配對斷裂；遇章末集中附註時須核對內文原印註標並還原標準腳註配對。
- 外文詞源、公式、縮寫同英文題名可以係真內容；唔需要先喺舊例詞白名單出現先保留。
- Japanese-style 法律/經濟借詞、音譯、貨幣名、人名要按 PDF 查，唔好現代化。
- 最後非空白頁若印刷本身停喺半句，而下一頁係空白或另一篇，保留半句並報告，唔好補完。

## 尾頁同視覺陷阱

- 有印章、反白、修補塊、淡字嘅頁可能令兩段黐埋；先睇原圖，仍有疑點先用放大、反色或 crop，並返原圖確認處理冇製造假筆畫。
- crop 回報「冇字」前，先確認佢覆蓋正確 scan、目標位置同足夠上下文。OCR 漏讀、錯位裁圖或 prompt 忽略頁腳都唔證明原書無字；刪除候選內容前必須返完整頁圖核對。
- 重複字串可能係原書印兩次，亦可能係同一位置被 OCR 讀兩次（包括雙引擎對齊局部錯位，令同一段落內緊接重出十餘至數十字）；按實際位置、框交疊、閱讀次序同正文／家具角色區分，唔單憑相同文字或固定字數門檻刪重。
- **黏附欄首／欄尾之「書眉＋頁碼」複合串**：當版心或天頭書眉貼近正文欄時，OCR 可能未將書眉獨立切框，而將「篇名／書名書眉＋中文或阿拉伯頁碼」直接黏入該頁首行或末行，跨頁接句後遂夾死於句子中間。核對跨頁接縫時，須特別留意句中突兀插入之篇名與數字組合。
- **現代數位倉儲追加尾頁與「原書缺頁」佔位頁**：
  - 影子圖書館或數位館藏常於 PDF 最末頁（偶見首頁）附加現代英文聲明與 JSON 元數據頁（`survey-report.json` 之 `boilerplate_pages` 或 final audit 之 `repository-metadata-tail`），交付前必須對圖核實並剔除，唔當原書正文轉錄。
  - 掃描本夾有「原書缺頁」佔位頁時，須同時核對佔位頁前後兩頁之章節號、條文號與句意是否直接連貫：若前後文句或條號緊密銜接，實屬原書印刷頁碼跳號而非缺文，應於校勘記錄註明原印跳號，唔好誤報缺文。
- OCR 空白 notice 可能漏規則、表格、尾頁小字、圖。
- Hunyuan、Paddle、Apple Vision 同其他 OCR 都可能漏淡字、註腳、圖中文字、誤判空白或拆亂表格；只當提示。Layout block 嘅 confidence 唔等於字形準確度。
- 最後頁可能係另一題名或附錄；未確認屬當前 Markdown 前唔好硬併。
- 半頁版面有大空白唔代表漏字；按 PDF 查欄位。
- 日期或損壞呈文讀唔清時，保留最有根據讀法並報告 uncertainty，唔好由年代猜。

## 完成前搜尋

```bash
rg -n "\[|\]|<!--|gjcool|此頁OCR|[A-Za-z]|�" TARGET.md || true
rg -n "[[:blank:]]+$" TARGET.md || true
test ! -s project-suspects.txt || rg -n -f project-suspects.txt TARGET.md || true
```

`project-suspects.txt` 由目前文件嘅重複頁眉／頁腳、correction ledger、OCR disagreement、專名 inventory 同已核實錯形產生。所有命中都要分類成 `fixed`、`source-intentional`、`allowed-format` 或 `unresolved`。英文封面題名、CIP／ISBN／分類號、合法外文同 table `<br>` 可以係正文；乾淨 audit 唔等於要求搜尋零命中。

刪 page comments 後可用以下粗略計數定位 soft wrap；佢唔理解完整 Markdown／HTML，命中要回看結構，唔可據此自動合併或聲稱結構已驗證：

```bash
python3 - <<'PY'
from pathlib import Path
lines = Path("TARGET.md").read_text().splitlines()
def structural(s):
    t = s.strip()
    return not t or t.startswith(("#", "- ", "|", ">", "```")) or s.startswith("  ")
print(sum(1 for i in range(len(lines)-1) if not structural(lines[i]) and not structural(lines[i+1])))
PY
```
