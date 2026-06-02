---
name: db-review
description: 保單審核 Google Sheet 規範。定義每個工作表的欄位結構、下拉選單選項、審核狀態欄位。由 db-agent / pipeline.py 使用，確保審核 Sheet 格式一致。
---

# db-review — 審核 Sheet 規範

---

## 工作表結構

每份保單建立一個 Google Spreadsheet，包含三個工作表：

| 工作表 | 內容 |
|--------|------|
| `給付項目審核` | 每個給付項目一行，含公式/金額與審核狀態 |
| `除外責任與限制` | 等待期、除外清單、特別限制 |
| `理賠必要文件` | 各申請情境所需文件 |

---

## 工作表 1：給付項目審核

### 欄位定義（A 到 H 欄）

| 欄 | 欄位名稱 | 類型 | 下拉選項 |
|---|---------|------|---------|
| A | 審核狀態 | Dropdown | 待審核 / 確認正確 / 需修改 / 忽略此項 / 進一步核實 |
| B | 給付項目名稱 | 文字（鎖定） | — |
| C | 公式 / 金額 | 文字（鎖定） | — |
| D | 單位 | Dropdown | /日 / /次 / /月 / /年 /（空白）|
| E | 限制條件 | 文字 | — |
| F | 注意事項 | 文字 | — |
| G | 保額基礎類型 | Dropdown（只在第一行） | 日額 / 單位 / 計劃別 / 固定 |
| H | 險種 | Dropdown（只在第一行，可複選文字） | — |

> 第 1 行為欄位標題（粗體），第 2 行起為資料。
> A 欄預設填入「待審核」。
> G、H 欄只在第 2 行填入，其餘行留空。

### 資料驗證（Data Validation）設定

```python
# 審核狀態（A 欄，A2:A100）
review_status_options = ["待審核", "確認正確", "需修改", "忽略此項", "進一步核實"]

# 單位（D 欄，D2:D100）
unit_options = ["/日", "/次", "/月", "/年", ""]

# 保額基礎類型（G 欄，G2 單格）
base_type_options = ["日額", "單位", "計劃別", "固定"]
```

---

## 工作表 2：除外責任與限制

### 欄位定義（A 到 C 欄）

| 欄 | 欄位名稱 | 類型 | 下拉選項 |
|---|---------|------|---------|
| A | 審核狀態 | Dropdown | 待審核 / 確認正確 / 需修改 / 忽略此項 |
| B | 類別 | Dropdown | 等待期 / 除外責任 / 特別限制 / 給付觸發條件 |
| C | 內容 | 文字 | — |

---

## 工作表 3：理賠必要文件

### 欄位定義（A 到 C 欄）

| 欄 | 欄位名稱 | 類型 | 下拉選項 |
|---|---------|------|---------|
| A | 審核狀態 | Dropdown | 待審核 / 確認正確 / 需修改 / 忽略此項 |
| B | 申請情境 | Dropdown | 每次申請必備 / 住院申請 / 手術申請 / 出院後申請 / 重大疾病確診 / 身故申請 / 其他 |
| C | 必要文件 | 文字 | — |

---

## Sheet 標題命名規則

```
【待審核】{company} {productName} {YYYYMMDD}
```

例：`【待審核】南山人壽保險股份有限公司 南山人壽醫療給付終身保險 20260602`

---

## 審核完成確認

當以下條件都達到，視為「可歸檔」：
- `給付項目審核` 工作表的 A 欄，無任何「待審核」
- A 欄只剩「確認正確」或「忽略此項」（「需修改」和「進一步核實」視為未完成）

---

## gspread 實作參考

```python
from gspread_formatting import DataValidationRule, BooleanCondition, set_data_validation_for_cell_range

def add_dropdown(ws, range_name: str, options: list[str]):
    rule = DataValidationRule(
        BooleanCondition("ONE_OF_LIST", options),
        showCustomUi=True
    )
    set_data_validation_for_cell_range(ws, range_name, rule)

# 使用範例
add_dropdown(coverage_ws, "A2:A100", ["待審核", "確認正確", "需修改", "忽略此項", "進一步核實"])
add_dropdown(coverage_ws, "D2:D100", ["/日", "/次", "/月", "/年", ""])
add_dropdown(coverage_ws, "G2",      ["日額", "單位", "計劃別", "固定"])
```

> 注意：需安裝 `gspread-formatting` 套件（`pip install gspread-formatting`）
