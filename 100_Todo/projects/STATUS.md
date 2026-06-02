# 專案協調狀態

> 更新：2026-06-02

---

## 整體架構

```
[Session A] scraper.py          → 爬 TII 商品列表
[Session A] pipeline.py         → 串接爬蟲 + 分析 + 上傳
[Session A] analyzer.py         → Claude API 分析 PDF
[Session A] push_to_sheets.py   → 上傳 Google Sheets / Drive
            ↓
     analyzed_state.json         ← 兩個 Session 的橋梁
            ↓
[Session B] notifier.py         → 讀狀態，發送審核提醒
```

---

## 檔案所有權

| 檔案 | 負責 Session | 狀態 |
|------|-------------|------|
| `scraper.py` | Session A | 進行中 |
| `pipeline.py` | Session A | 進行中 |
| `analyzer.py` | Session A | 進行中 |
| `push_to_sheets.py` | Session A | 進行中 |
| `analyzed_state.json` | 共用（A 寫 / B 讀） | 橋梁 |
| `notifier.py` | Session B | 設計中 |
| `notifier_config.json` | Session B | 待建立 |

---

## analyzed_state.json 欄位合約

Session A 寫入的欄位：
```json
{
  "planCode": {
    "status": "uploaded",
    "uploadedAt": "2026-06-02",
    "sheetUrl": "https://docs.google.com/spreadsheets/d/...",
    "pdfDriveId": "...",
    "productName": "南山人壽終身醫療保險",
    "filename": "[健] 南山人壽終身醫療保險_112-01-01~"
  }
}
```

Session B 追加的欄位（不覆蓋 A 的欄位）：
```json
{
  "planCode": {
    "notifiedAt": "2026-06-02T14:30:00",
    "notifyChannel": "email"
  }
}
```

---

## 目前進度

### Session A
- [ ] PDF 從 TII 下載功能（進行中）
- [ ] pipeline.py 整合測試

### Session B
- [ ] notifier.py 設計與實作
- [ ] 通知管道設定（Email / LINE）

---

## 交接說明

Session A 完成後：
1. `git commit` 你的改動
2. 在這個檔案把 Session A 的 checkbox 打勾
3. Session B 就能接著整合測試
