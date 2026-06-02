---
name: db-archive
description: 保單審核歸檔規則。定義顧問審核完畢後，如何將待審核 Sheet 移至正式保單資料庫。由 db-agent 使用，或顧問執行 push_to_sheets.py --archive 時依照此規則處理。
---

# db-archive — 歸檔流程規則

---

## 前置條件（歸檔前必須確認）

1. Google Sheet 的「給付項目審核」工作表，A 欄無任何「待審核」或「需修改」或「進一步核實」
2. `uuid_registry.json` 中該 UUID 的 status 為 `uploaded`
3. 若帶 `--force` 旗標，跳過前置條件確認直接執行

---

## 歸檔目錄結構

```
保單審核資料庫/
├── 待審核/                          ← 新上傳的暫存區
│   ├── 【待審核】{company} {product} {date}.gsheet
│   └── {filename}.pdf
│
└── {company}/                       ← 公司名稱（完整法定名稱）
    └── {insuranceType}/             ← 第一個主要險種
        ├── {filename}.gsheet        ← 審核完的 Sheet（已去掉【待審核】前綴）
        └── {filename}.pdf
```

---

## Sheet 重命名規則

歸檔時重新命名 Sheet：

```
【待審核】南山人壽保險股份有限公司 南山人壽醫療給付終身保險 20260602
→
[健] 南山人壽醫療給付終身保險_060-02-04~095-06-30
```

規則：
1. 去掉 `【待審核】` 前綴
2. 去掉公司名稱前綴（`{company} ` 部分）
3. 使用 `filename` 欄位的值作為最終名稱

---

## 歸檔資料夾判斷邏輯

`insuranceType` 可能是陣列（如 `["定額醫療", "壽險"]`）。

歸檔資料夾使用**第一個元素**作為路徑：

```python
insurance_type = data["insuranceType"]
if isinstance(insurance_type, list):
    folder_name = insurance_type[0]
else:
    folder_name = insurance_type
```

---

## 歸檔步驟（由 push_to_sheets.py 執行）

1. 讀取 Sheet 的「給付項目審核」第一行 G 欄，取得 `baseType`
2. 讀取 `uuid_registry.json`，找到對應的 `company`、`filename`、`insuranceType`
3. 確認前置條件（或 --force 跳過）
4. 在 Drive 取得或建立目錄：`保單審核資料庫/{company}/{insuranceType}/`
5. 移動 PDF 至目標資料夾（`files().update()` with `addParents` / `removeParents`）
6. 移動 Sheet 至目標資料夾
7. 重新命名 Sheet
8. 更新 `uuid_registry.json`：status → `archived`，archivedAt → 今天

---

## uuid_registry 更新

```json
"{pdfUUID}": {
  "status": "archived",
  "archivedAt": "2026-06-02",
  "archiveFolderPath": "保單審核資料庫/南山人壽保險股份有限公司/定額醫療/"
}
```

---

## 執行指令

```bash
# 正常歸檔（會確認審核狀態）
python3 push_to_sheets.py --archive {sheet_id}

# 強制歸檔（跳過確認，適合自動化）
python3 push_to_sheets.py --archive {sheet_id} --force
```
