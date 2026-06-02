# 保單條款自動爬取＋分析＋歸檔系統

> 建立時間：2026-06-02 20:30
> 類型：新功能
> 狀態：待執行

---

## 你要做的事（一句話版）

手動下指令後，系統自動爬取指定公司的新保單條款、用 Claude API 分析、直接上傳到 Google Drive 正確的資料夾裡。

---

## 背景和動機

現在流程：人工找 PDF → 在 Claude Code 跑 `/policy-db-extract` skill → 手動跑 `push_to_sheets.py`。
這次要把中間那段全部自動化，讓你只需要下一行指令就好。

---

## 命名規則

**檔名格式：`[大類] {商品名稱}_{民國啟售日}~{民國截止日}`**
- 民國日期格式：`090/09/01` → 改為 `090-09-01`（斜線換短橫線，不轉西元）
- 現售中（無截止日）：`112-01-01~`
- 已停售：`090-09-01~094-05-31`
- 大類前綴：`[健]`健康保險 / `[壽]`壽險 / `[傷]`傷害保險

**範例：**
```
[健] 南山人壽終身醫療保險_112-01-01~
[傷] 南山人壽意外傷害保險_090-09-01~094-05-31
```

---

## 具體步驟

### Step 1：TII 爬蟲（`scraper.py`）
- 做什麼：爬 `insprod.tii.org.tw` 指定公司的商品列表，回傳 planCode + productName + saleDate + stopDate + category + PDF 連結
- CAPTCHA 處理：第一次手動在瀏覽器解 CAPTCHA，複製 session cookie 貼給腳本，之後自動翻頁
- 過濾：category 含「延續」或 productName 含「延續」的直接排除
- 產出：`{company}_products.json`（欄位同現有的 `tii_sanshang_products.json`）

### Step 2：狀態追蹤（`analyzed_state.json`）
- 做什麼：記錄每個 planCode 的處理狀態（`pending` / `analyzed` / `failed`）
- 邏輯：每次跑 pipeline 前比對，只處理 `pending` 或首次出現的 planCode
- 路徑：`100_Todo/projects/analyzed_state.json`

### Step 3：PDF 下載＋上傳 Drive
- 做什麼：取得 TII 條款 PDF → 下載到暫存 `tmp/pdfs/` → 上傳到 Drive 同一個資料夾
- Drive 結構：PDF 跟最終 Sheet 放在一起（`南山人壽/定額醫療/南山人壽終身醫療保險_20230101.pdf`）
- 待審核階段：PDF 先放 `待審核/`，歸檔時跟 Sheet 一起移動
- 注意：TII 的 PDF 連結需要 session cookie，跟 Step 1 共用同一個 session

### Step 4：Claude API 分析（`analyzer.py`）
- 做什麼：把 PDF 送給 Claude API，使用 `policy-db-extract` skill 的 system prompt 進行分析，輸出標準 JSON
- 模型：claude-sonnet-4-6
- 額度控制：`--limit N` 參數限制這次最多分析幾份，預設 10
- 產出：符合 `sample_nanshan.json` 格式的 JSON

### Step 5：上傳 Google Drive
- 做什麼：把 Step 4 的 JSON 餵給現有 `push_to_sheets.py` 的邏輯，建立 Sheet 並放入 `待審核/`
- 直接 import push_to_sheets.py 的函式使用，不重複造輪子

### Step 6：主程式（`pipeline.py`）
- 把 Step 1–5 串起來
- 指令：`python3 pipeline.py --company 南山人壽 [--limit 10] [--cookie "xxx"]`
- 跑完後印出摘要：分析了幾份、跳過幾份（已分析過）、失敗幾份

---

## 預計成果

- 一行指令搞定：爬取 → 分析 → 上傳 Google Drive
- Drive 結構自動正確：`保單審核資料庫/待審核/{商品名稱}_{日期}`
- 不重複分析：同一個 planCode 只分析一次
- 延續條款自動排除
- `--limit` 控制每次燒多少 Claude 額度

## 執行順序

先跑單筆測試確認流程沒問題，再開放批次：
1. `python3 pipeline.py --company 南山人壽 --limit 1 --cookie "xxx"` → 測試單筆
2. 確認 Drive 有 PDF + Sheet → 再跑 `--limit 10` 批次

## 不包含在這次的範圍

- 定時排程（之後可用 macOS crontab 加）
- CAPTCHA 自動解（需手動貼一次 cookie）
- 多公司同時批次跑（一次一家）
- 歸檔時自動移動 PDF（第一版先手動，之後再自動化）

## 可能遇到的風險

- **TII session 過期**：cookie 有時效，需要重新登入取得新 cookie。腳本檢測到 403 時提示重新貼 cookie
- **PDF 格式差異**：各公司 PDF 格式不同，分析品質可能有落差。`analyzed_state.json` 記錄 failed，方便重跑
- **Claude API 費用**：一份保單約 $0.003–0.008 USD，10 份約 $0.08。`--limit` 控制上限
