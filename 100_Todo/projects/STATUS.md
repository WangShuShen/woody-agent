# 保單資料庫自動化流水線狀態

> 更新：2026-06-08

---

## 整體架構

```
pipeline.py               ← 總指揮（串接以下三個階段）
  ↓ Phase 1
scraper.py                ← TII 爬蟲（含 CAPTCHA 自動解）
  ↓ Phase 2
analyzer.py               ← Claude API 分析 PDF → analyzed/{uuid}.json
  ↓ Phase 3
push_to_sheets.py         ← 建立 Google Sheet + 上傳 PDF 到 Drive
  ↓
uuid_registry.json        ← 全域狀態登記（status 欄位是唯一 source of truth）
  ↓
Review UI（Next.js）       ← 顯示待審核佇列，顧問逐一審核後歸檔
  ↓
push_to_sheets.py --archive ← 歸檔：Sheet + PDF 移至正式資料庫資料夾
```

---

## uuid_registry.json 狀態流

```
no_pdf      ← 商品無 PDF，跳過
analyzed    ← Claude 分析完成，還未上傳 Drive（分析成功、上傳失敗時暫存）
uploaded    ← 分析 + Drive 上傳 + Sheet 建立完成，等待審核
archived    ← 審核完成，已移至正式資料庫
failed      ← 分析失敗（JSON 解析錯、API 錯）
```

---

## pipeline.py 執行模式

| 指令 | 說明 |
|------|------|
| `python3 pipeline.py --company 206` | 完整跑：爬蟲 → 分析 → 上傳 |
| `python3 pipeline.py --company 206 --skip-scrape` | 跳過爬蟲，用現有 products JSON |
| `python3 pipeline.py --company 206 --analyze-only` | 只跑 Claude 分析，不上傳 Drive |
| `python3 pipeline.py --company 206 --upload-only` | 跳過分析，直接上傳已有分析結果 |
| `python3 pipeline.py --company 206 --force` | 強制重新處理（忽略已上傳狀態） |
| `python3 pipeline.py --company 206 --limit 1` | 測試用，只處理 1 筆 |

> ⚠️ 已有 `analyzed/{uuid}.json` 的商品自動跳過 Claude API，不會重複呼叫。

---

## 各 Script 說明

| 檔案 | 輸入 | 輸出 | 職責 |
|------|------|------|------|
| `scraper.py` | TII 網站 | `{company}_products.json` + `tmp/pdfs/*.pdf` | 爬蟲 + 下載 PDF |
| `analyzer.py` | `*_products.json` | `analyzed/{uuid}.json` | Claude 分析（可獨立執行） |
| `push_to_sheets.py` | `analyzed/{uuid}.json` | Google Sheet + Drive | 建立審核 Sheet（可獨立執行） |
| `pipeline.py` | 公司代碼 | 全部 | 統籌三個階段 |
| `notifier.py` | `uuid_registry.json` | 通知訊息 | 審核提醒（LINE / Email） |

---

## Review UI 欄位對應

`uuid_registry.json` → `/api/review` → `ReviewProduct` 介面：

| registry 欄位 | API 欄位 | 說明 |
|--------------|---------|------|
| key（uuid） | `id` | pdfUUID，也是 `analyzed/{id}.json` 的檔名 |
| `planCode` | `planCode` | TII productId |
| `company` | `company` | 保險公司全名 |
| `productName` | `product_name` | 商品名稱 |
| `sheetUrl` | `sheetUrl` | Google Sheet URL |
| `pdfDriveId` | `pdfDriveId` | Drive PDF 檔案 ID（空 = 無 PDF）|
| `filename` | `filename` | 檔名（含標籤與日期） |

---

## 歸檔執行

```bash
python3 push_to_sheets.py --archive {sheet_url_or_id} --force --pdf-id {pdfDriveId}
```

Review UI 的「通過審核·歸檔」按鈕會自動呼叫 `/api/review/[id]` PATCH，由 Next.js 後端執行這個指令。
