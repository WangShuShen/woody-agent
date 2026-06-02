# 保單資料庫系統架構重設計

> 建立時間：2026-06-02
> 類型：改善優化 + 架構重組
> 狀態：待執行

---

## 你要做的事（一句話版）

把現有的「能跑但有缺陷」的流水線，升級成一套「乾淨、不重複、有審核選單、架構分明」的保單資料庫系統，並同步整理所有 Skill 的命名。

---

## 背景和動機

現有問題：
1. **爬蟲混入團險和延續條款**：人身保險資料庫不應該有這些，但目前沒有過濾
2. **審核 Sheet 無法選項化**：部分欄位是純文字，顧問審核要手動打字，容易出錯
3. **去重邏輯不完整**：用 planCode 去重，但不同公司相同 PDF（UUID）會重複分析，浪費 API 費用
4. **Skill 命名混亂**：15+ 個 Skill 沒有統一前綴，看不出哪個給顧問用、哪個給系統用
5. **架構缺少明確層級**：三個 Python 腳本各自為政，沒有一個 Agent 統籌

---

## 新架構

```
db-agent              ← 主 Agent（統籌全流程，知道狀態和邏輯）
├── db-scrape         ← Skill 1：爬蟲規則 + 過濾條件定義
├── db-extract        ← Skill 2：PDF 分析（舊名 policy-db-extract）
├── db-review         ← Skill 3：審核 Sheet 規範 + Dropdown 定義
└── db-archive        ← Skill 4：歸檔流程規則

Python 實作層：
├── scraper.py        ← 更新：加過濾邏輯 + 抽取 pdfUUID
├── analyzer.py       ← 更新：改用 UUID 去重
├── push_to_sheets.py ← 更新：加 Data Validation dropdowns
└── pipeline.py       ← 更新：整合 UUID registry

資料層：
├── uuid_registry.json    ← 新：全域 UUID → 分析狀態（取代 analyzed_state.json）
├── {company}_products.json
└── analyzed/{uuid}.json  ← 改：檔名從 planCode 改為 UUID
```

---

## Skill 重命名清單

### 保留不動
- `tool-brainstorm` ← brainstorm
- `tool-skill-builder` ← skill-creator
- `tool-ceo-advisor` ← ceo-advisor
- `tool-exec-mentor` ← executive-mentor
- `tool-pm` ← senior-pm

### 顧問工作（有實際保額，面向客戶）
- `advisor-analyze` ← policy-orchestrator
- `advisor-medical-fixed` ← policy-fixed-medical
- `advisor-medical-reimb` ← policy-medical-reimbursement
- `advisor-accident` ← policy-accident
- `advisor-cancer` ← policy-cancer
- `advisor-critical-card` ← policy-critical-card
- `advisor-critical-spec` ← policy-critical-specific
- `advisor-disability` ← policy-disability
- `advisor-ltc` ← policy-ltc
- `advisor-life` ← policy-life
- `advisor-restrictions` ← policy-restrictions
- `advisor-claim-docs` ← policy-claim-docs
- ~~`policy-analyze`~~ ← 退役（功能由 advisor-analyze 取代）

### 資料庫建置
- `db-agent` ← 新建
- `db-scrape` ← 新建
- `db-extract` ← policy-db-extract
- `db-review` ← 新建
- `db-archive` ← 新建

---

## 具體步驟

### Step 1：Skill 重命名
- 做什麼：在 `~/.claude/skills/` 下，將所有 policy-* 資料夾依照上面清單重命名，並更新每個 SKILL.md 內的 `name:` 欄位
- 產出：所有 Skill 名稱與新命名一致，`/advisor-analyze` 可以呼叫成功
- 注意：`policy-orchestrator` 內有寫死的路徑（Specialist Skill 路徑），重命名後需要一起更新

### Step 2：建立 `db-agent` SKILL.md
- 做什麼：新建 `~/.claude/skills/db-agent/SKILL.md`，定義整個資料庫建置的主流程
- 內容：
  - 全流程說明（爬蟲 → 過濾 → 去重 → 分析 → 上傳 → 審核 → 歸檔）
  - 每個階段呼叫哪個子 Skill
  - 全域狀態說明（uuid_registry.json 的格式與用途）
  - 如何判斷「這筆已經處理過」

### Step 3：建立 `db-scrape` SKILL.md
- 做什麼：定義爬蟲的過濾規則，讓 AI 和 Python 腳本都遵循同一套標準
- 過濾規則（排除以下）：
  - 名稱含「團體」「團險」「集體」「團保」
  - 類別為「健康保險（團體）」「傷害保險（團體）」等
  - 名稱含「延續條款」「延續」「停售後續」
  - 名稱含「附約」且主約非人身保險相關
- UUID 抽取：從 `pdfUrl` 的 `id=` 參數取出 UUID，作為全域唯一識別碼

### Step 4：建立 `db-review` SKILL.md
- 做什麼：定義審核 Sheet 的欄位結構和 Dropdown 選項
- Dropdown 定義：
  - `insuranceType`：定額醫療 / 醫療實支 / 意外傷害 / 防癌險 / 重大傷病卡 / 特定傷病 / 失能險 / 長照險 / 壽險
  - `baseType`：日額 / 單位 / 計劃別 / 固定
  - `unit`（每個給付項目）：/日 / /次 / /月 / （空白）
  - `reviewStatus`（新增欄）：待審核 / 確認正確 / 需修改 / 忽略此項 / 進一步核實
- 每個工作表的 row 都要加「審核狀態」欄，位置在最左側 A 欄

### Step 5：建立 `db-archive` SKILL.md
- 做什麼：定義歸檔的流程規則（現在分散在 push_to_sheets.py 裡）
- 內容：歸檔路徑規則（`保單審核資料庫 / {company} / {insuranceType} /`）、命名格式、前置條件

### Step 6：更新 `scraper.py`
- 做什麼：實作 Step 3 的過濾規則
- 具體：在 `scrape()` 函數的結果 list 中，過濾掉符合排除條件的商品
- 同時：在每個 product dict 加入 `pdfUUID` 欄位（從 pdfUrl 抽取 UUID）

### Step 7：更新 `analyzer.py`（UUID 去重）
- 做什麼：引入 `uuid_registry.json` 取代 `analyzed_state.json`
- 去重邏輯：以 `pdfUUID` 為 key，若已在 registry 中且 status != failed，直接跳過
- 輸出檔名：`analyzed/{pdfUUID}.json`（原本是 planCode）
- registry 格式：
  ```json
  {
    "08143f9d-148b-49a1-a8be-6f53ead68fe9": {
      "status": "uploaded",
      "planCode": "206317M11A30100",
      "company": "南山人壽保險股份有限公司",
      "productName": "南山人壽醫療給付團體保險",
      "analyzedAt": "2026-06-02",
      "sheetUrl": "https://..."
    }
  }
  ```

### Step 8：更新 `push_to_sheets.py`（加入 Dropdown）
- 做什麼：在 `build_coverage_sheet()` 等三個函數中，對可標準化的欄位加入 Google Sheets Data Validation
- 使用 `gspread` 的 `set_data_validation_for_cell_range()` API
- 加入「審核狀態」欄（A 欄），下拉選項：待審核 / 確認正確 / 需修改 / 忽略此項 / 進一步核實

### Step 9：更新 `pipeline.py`
- 做什麼：整合 UUID registry，確保 pipeline 以 UUID 為主鍵操作
- 更新摘要輸出格式

---

## 預計成果

做完以後你會得到：

- 爬蟲自動排除團險、延續條款，資料庫只有個人人身保險
- 跨公司、跨 planCode 的 UUID 去重，同一份 PDF 永遠只分析一次
- 審核 Sheet 有完整下拉選單，顧問審核更快、出錯更少
- 清晰的 `db-agent` 統籌架構，任何人看到 SKILL 就知道整個流程
- Skill 命名統一，`advisor-*` vs `db-*` 一眼區分用途

---

## 不包含在這次的範圍

- 顧問用 Skill（`advisor-*`）的內容修改，只做重命名
- 自動排程（crontab / 定時跑 pipeline）
- 多公司批次同時執行
- Web UI 或前端審核介面
- 傳家知保 App 的部署（維持現狀）

---

## 可能遇到的風險

- **gspread Data Validation API**：`gspread 6.x` 支援 `set_data_validation_for_cell_range()`，但 API 參數格式需確認，可能要查文件
- **Skill 路徑重命名**：`advisor-analyze`（舊 policy-orchestrator）內有寫死 Specialist 路徑，重命名後全部要更新，不能漏
- **uuid_registry 遷移**：現有的 `analyzed_state.json` 紀錄不會自動遷移，若要保留舊紀錄需要一次性轉換腳本（可選做）
- **scraper.py 過濾可能誤殺**：「延續」這個詞也可能出現在正常商品名稱中，需要測試確認過濾條件的精確度
