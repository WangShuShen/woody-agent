---
name: db-agent
description: 保單資料庫建置主 Agent。統籌整個「TII 爬蟲 → AI 分析 → Google Drive 上傳 → 審核 Sheet 建立 → 歸檔」流程。當使用者說「/db-agent」、「跑資料庫流程」、「更新保單資料庫」時觸發。
---

# db-agent — 保單資料庫建置主 Agent

> 角色：全流程統籌指揮，知道每個階段在做什麼、狀態在哪裡、什麼情況要跳過。

---

## 整體流程圖

```
Phase 1：爬蟲（db-scrape）
    ↓ 過濾：去掉團險、延續條款
    ↓ 輸出：{company}_products.json（含 pdfUUID）

Phase 2：UUID 去重檢查
    ↓ 對照 uuid_registry.json
    ↓ 已存在且 status != failed → 跳過

Phase 3：PDF 下載 + AI 分析（db-extract）
    ↓ 輸出：analyzed/{pdfUUID}.json

Phase 4：上傳 Google Drive（待審核/）
    ↓ 上傳 PDF + 建立審核 Sheet（db-review 規範）
    ↓ 更新 uuid_registry.json

Phase 5：人工審核（顧問在 Google Sheet 操作）

Phase 6：歸檔（db-archive）
    ↓ 移至 保單審核資料庫/{公司}/{險種}/
    ↓ 更新 uuid_registry.json status → archived
```

---

## 全域狀態：uuid_registry.json

所有階段共用這個檔案作為唯一真實狀態。

```json
{
  "{pdfUUID}": {
    "status": "pending | analyzed | uploaded | archived | failed | no_pdf",
    "planCode": "206317M11A30100",
    "company": "南山人壽保險股份有限公司",
    "productName": "南山人壽醫療給付團體保險",
    "filename": "[健] 南山人壽醫療給付團體保險_060-02-04~095-06-30",
    "analyzedAt": "YYYY-MM-DD",
    "uploadedAt": "YYYY-MM-DD",
    "sheetUrl": "https://docs.google.com/spreadsheets/d/...",
    "pdfDriveId": "...",
    "archivedAt": "YYYY-MM-DD",
    "error": ""
  }
}
```

**狀態轉換規則：**
- `pending` → `analyzed`：PDF 下載成功且 AI 分析完成
- `analyzed` → `uploaded`：PDF + Sheet 已上傳到 Drive 待審核/
- `uploaded` → `archived`：顧問審核完畢，執行歸檔
- 任何階段失敗 → `failed`（附 error 訊息，下次可用 --force 重跑）

---

## Phase 1：爬蟲（呼叫 db-scrape 規則）

執行：`python3 scraper.py --company {company_code}`

輸出：`{company}_products.json`

每個 product 必須包含：
```json
{
  "company": "...",
  "productName": "...",
  "planCode": "...",
  "pdfUUID": "08143f9d-...",
  "pdfUrl": "https://...",
  "pdfPath": "...",
  "filename": "[健] ...",
  "category": "健康保險",
  "label": "[健]",
  "saleDate": "060/02/04",
  "stopDate": "095/06/30",
  "active": false
}
```

---

## Phase 2：去重檢查

```python
uuid = product["pdfUUID"]
existing = uuid_registry.get(uuid, {})
if existing.get("status") not in ("", "failed", None) and not force:
    skip  # 這份 PDF 已處理過
```

---

## Phase 3：AI 分析

執行：`python3 analyzer.py`（內部呼叫 db-extract Skill 作為 system prompt）

- 輸入：PDF base64
- 輸出：`analyzed/{pdfUUID}.json`
- 失敗時：status = failed，附 error，不阻塞其他筆

---

## Phase 4：上傳 Drive

執行：`python3 pipeline.py`

- PDF 上傳至 `保單審核資料庫/待審核/`
- 審核 Sheet 建立（參照 db-review 規範）
- Sheet 標題格式：`【待審核】{company} {productName} {YYYYMMDD}`

---

## Phase 5：人工審核

顧問在 Google Sheet 操作，不需要執行任何腳本。

---

## Phase 6：歸檔

執行：`python3 push_to_sheets.py --archive {sheet_id} [--force]`

- 移至 `保單審核資料庫/{company}/{insuranceType}/`
- 重新命名 Sheet（去掉【待審核】前綴）
- 更新 uuid_registry.json → status: archived

---

## 兩台電腦分工

| 機器 | 負責公司 | 指令 |
|------|---------|------|
| 主 Mac（Woody） | 南山人壽（206） | `python3 pipeline.py --company 206` |
| 副 Mac | 三商美邦人壽（211） | `python3 pipeline.py --company 211` |

`uuid_registry.json` 會自動同步到 Google Drive `保單審核資料庫/` 資料夾，兩台共用、不重複分析。

---

## 常用指令速查

```bash
# 爬取 + 分析 + 上傳（全流程）
python3 pipeline.py --company 206   # 南山（主 Mac）
python3 pipeline.py --company 211   # 三商美邦（副 Mac）

# 只跑指定筆數（測試用）
python3 pipeline.py --company 206 --limit 1

# 跳過爬蟲，重用現有 products.json
python3 pipeline.py --company 206 --skip-scrape

# 強制重新處理（已上傳的也重跑）
python3 pipeline.py --company 206 --force

# 審核完後歸檔
python3 push_to_sheets.py --archive {sheet_id} [--force]
```
