---
name: policy-analyze
description: 分析保險保單條款（PDF 或圖片），自動解析保障內容，輸出全險圖。當使用者說「幫我分析保單」、「/policy-analyze」、「分析這份保單」、「帶入全險圖」時觸發。
---

# policy-analyze

## 用途
分析保單條款（PDF 或圖片），自動解析保障內容，輸出全險圖。
目前有兩個版本：
- **Web App（主力）**：`/policy-analyzer`（Next.js + Turso DB），執行於 `localhost:3000`
- **Notion 版（舊）**：見下方舊流程，已改用 Web App

---

## Web App 架構（傳家知保）

### 路徑
`/Users/wangshusheng/Downloads/woody-agent/policy-analyzer`

### 主要檔案
| 檔案 | 用途 |
|------|------|
| `app/api/analyze/route.ts` | 核心分析 API，含 Claude SYSTEM_PROMPT 與 JSON schema |
| `app/api/pdf-proxy/route.ts` | 代理保發中心 TII PDF（需 session cookie） |
| `app/api/products/route.ts` | 商品查詢 API（含在售篩選） |
| `components/InsuranceChart.tsx` | 全險圖顯示元件 |
| `lib/policyCache.ts` | DB 快取、搜尋、修正邏輯 |
| `lib/db.ts` | Turso/LibSQL 連線 |

### 分析 API 行為
- 上傳 PDF 或圖片 → Claude 分析 → 回傳 JSON → 顯示全險圖
- `force=true`：跳過快取，強制重新分析（**開始分析**與**重新分析**均用此模式）
- `force=false`：查 DB 快取（若同公司+計畫代號+保額已有紀錄則直接回傳）

---

## JSON Schema 欄位規格（最新版）

### 輸出格式規則
所有欄位值使用 `金額｜限制條件` 格式：
- 金額只寫核心數字，例如：`1,000元/日`、`1,000~100,000元/次`
- `｜` 後面寫限制：`最高365日`、`限10次`、`年限1次`
- 附表有範圍只寫 `最低~最高`，不舉例
- 沒有限制不加 `｜`

### fixedMedical（定額醫療）特有欄位
| 欄位 | 對應條款類型 | 說明 |
|------|------------|------|
| `hospitalDaily` | 住院日額 | 支援年齡分層（75歲以上另計） |
| `icu` | 加護/燒燙傷病房 | 填合計後金額（住院日額＋加給） |
| `surgery` | 手術給付附表一 | 附表型填 `最低~最高元/次｜共N項` |
| `specificTreatment` | 特定處置附表二 | 附表型填 `最低~最高元/次｜共N項` |
| `woundClosure` | 創傷縫合附表三 | `N~N元/次（依傷口大小）` |
| `specialMedicalDevice` | 特殊醫材補助 | 支援年度分層，累積次數上限 |
| `annualLimit` | 累積給付上限 | 含第N條至第N條各項合計 |

### 全險圖顯示（InsuranceChart.tsx Row 元件）
每個 Row 顯示三欄：`標籤 | 金額 | 限制條件`
- 值中有 `｜` 時自動拆分：`｜` 前為金額、`｜` 後為限制（小字灰色）
- `highlight=true` 的欄位用琥珀色顯示（累積上限等重要欄位）

---

## 已知修正規則（SYSTEM_PROMPT 內建）
- 凱基人壽 QDHL2：加護病房 = 住院日額×2，非×1
- 凱基人壽 新康健93A 防癌：等待期90天，上限250萬/單位
- 原保誠人壽商品 → 凱基人壽承接

---

## 開發記錄

### 2026-05-28
- 新增 `specialMedicalDevice`（特殊醫材補助）、`specificTreatment`（特定處置）、`woundClosure`（創傷縫合）三個欄位到 fixedMedical schema
- 修正「開始分析」按鈕改為 `force=true`，與「重新分析」行為一致（不再回傳快取舊資料）
- Row 元件新增第三欄「限制條件」，解析 `｜` 分隔符
- SYSTEM_PROMPT 新增全域格式規則：`金額｜限制條件`，簡潔化所有輸出

---

## 觸發方式
使用者說「幫我分析保單」、「/policy-analyze」、「分析這份保單」、「帶入全險圖」時觸發。

---

## 核心原則
1. **完整重建，不附加**：每次分析完，刪除頁面所有舊內容，從頭重建整頁
2. **保戶為單位**：一個保戶一個 Notion 頁面，所有主約都在同一頁
3. **Database 屬性同步**：每次重建後同步更新保戶總覽 Database 的勾選狀態

---

## 執行流程

### Step 0：確認必要資訊
缺少就問清楚再繼續：
1. **保戶姓名**
2. **保單檔案路徑**（PDF 或圖片，可多個）
3. **保額**（對應哪個方案/投保單位）

---

### Step 1：讀取保單內容

**PDF（用 pymupdf）：**
```python
import fitz
doc = fitz.open('路徑.pdf')
text = '\n'.join([page.get_text() for page in doc])
```

**圖片：** 用 Read 工具讓 Claude Vision 解析

---

### Step 2：先查知識庫（讓分析更準）

**查產品規格庫，找是否有相同商品的已知規格：**
```bash
curl -s -X POST "https://api.notion.com/v1/databases/36c03f34-519d-8165-91d1-cb3eb94c7722/query" \
  -H "Authorization: Bearer ntn_m1060393373H1mMEAWnYw76EwIP0JkTfM02putDmBPu6SM" \
  -H "Notion-Version: 2022-06-28" \
  -H "Content-Type: application/json" \
  -d '{"filter": {"or": [
    {"property": "商品名稱", "title": {"contains": "商品關鍵字"}},
    {"property": "計畫代號", "rich_text": {"contains": "代號"}}
  ]}}'
```

**查修正記錄，看同公司同商品有沒有已知的錯誤規則：**
```bash
curl -s -X POST "https://api.notion.com/v1/databases/36c03f34-519d-81d0-9f6f-dfa9c014a07a/query" \
  -H "Authorization: Bearer ntn_m1060393373H1mMEAWnYw76EwIP0JkTfM02putDmBPu6SM" \
  -H "Notion-Version: 2022-06-28" \
  -H "Content-Type: application/json" \
  -d '{"filter": {"property": "保險公司", "rich_text": {"contains": "公司名稱"}}}'
```

- 若查到規格 → 以知識庫的「關鍵規格」為主，條款文字為輔
- 若查到修正記錄 → 主動套用修正規則，避免重複犯錯
- 若無記錄 → 純靠條款分析，分析完後若有修正要回存

---

### Step 2b：解析保單，整理成結構化資料

每份保單解析出：
```
險種分類：壽險 / 醫療實支 / 定額醫療 / 意外險 / 防癌險 / 重大傷病 / 長照 / 失能
保險公司：（注意保誠人壽已被凱基收購，保單歸凱基承接）
保單名稱：
計畫代號：
型態：終身型（幾歲）/ 定期型（幾歲）
繳費狀態：繳費中 / 已滿期 / 已停效
等待期：N天
主要給付項目：（依險種列出所有給付欄位與金額）
除外責任：（列出不賠的情況）
特殊條款：（豁免保費等）
```

**險種判斷：**
- 住院日額、加護病房、定額 → 定額醫療
- 收據、實支實付 → 醫療實支
- 身故、壽險、保障額度 → 壽險
- 意外、傷害、失能扶助 → 意外險
- 癌症、惡性腫瘤、初次罹癌 → 防癌險
- 重大傷病卡、7項重大疾病 → 重大傷病
- 長期照顧、1-6級失能 → 長照/失能

---

### Step 3：查詢或建立 Notion 頁面

**查詢保戶是否已存在：**
```bash
curl -s -X POST "https://api.notion.com/v1/databases/36c03f34-519d-8195-a86d-d63e7b85acad/query" \
  -H "Authorization: Bearer ntn_m1060393373H1mMEAWnYw76EwIP0JkTfM02putDmBPu6SM" \
  -H "Notion-Version: 2022-06-28" \
  -H "Content-Type: application/json" \
  -d '{"filter": {"property": "姓名", "title": {"equals": "保戶姓名"}}}'
```

- 若已有 → 取得頁面 ID，**刪除所有舊內容**，從頭重建
- 若無 → 在 Database 建立新 row

---

### Step 4：刪除舊內容（若頁面已存在）

**取得所有 block ID：**
```bash
curl -s "https://api.notion.com/v1/blocks/{PAGE_ID}/children?page_size=100" \
  -H "Authorization: Bearer ntn_m1060393373H1mMEAWnYw76EwIP0JkTfM02putDmBPu6SM" \
  -H "Notion-Version: 2022-06-28"
```

**逐一刪除：**
```bash
curl -s -X DELETE "https://api.notion.com/v1/blocks/{BLOCK_ID}" \
  -H "Authorization: Bearer ntn_m1060393373H1mMEAWnYw76EwIP0JkTfM02putDmBPu6SM" \
  -H "Notion-Version: 2022-06-28"
```

> ⚠️ Notion 一次最多回傳 100 個 block，若超過需分批刪除。

---

### Step 5：重建完整頁面

每次都按以下**固定結構**完整重建，確保清晰一致：

```
【區塊1】📋 全險圖總覽表
  - callout 標題
  - table：每個險種一行
    欄位：險種 | 保險公司 | 計畫代號 | 保額/日額 | 型態 | 繳費狀態

【區塊2】🗂️ 主約與附約清單
  - heading_1
  - 每份主約一個 heading_2（色底：依險種）
    壽險→紅底 / 醫療→藍底 / 意外→橘底 / 防癌→綠底 / 重大→紫底 / 長照失能→灰底
  - 主約下方：完整給付項目（bulleted_list_item）
  - 主約下方：附約清單（若有）

【區塊3】⚠️ 理賠條件與除外責任
  - heading_1
  - 每份保單的等待期、上限、除外條款

【區塊4】🔍 保障缺口
  - heading_1
  - 列出所有未覆蓋的險種（❌ 開頭）
```

---

### Step 6：更新 Database 屬性

重建頁面後，同步更新保戶總覽 Database 的 row：
```bash
curl -s -X PATCH "https://api.notion.com/v1/pages/{ROW_ID}" \
  -H "Authorization: Bearer ntn_m1060393373H1mMEAWnYw76EwIP0JkTfM02putDmBPu6SM" \
  -H "Notion-Version: 2022-06-28" \
  -H "Content-Type: application/json" \
  -d '{
    "properties": {
      "壽險": {"checkbox": true/false},
      "醫療實支": {"checkbox": true/false},
      "定額醫療": {"checkbox": true/false},
      "意外險": {"checkbox": true/false},
      "防癌險": {"checkbox": true/false},
      "重大傷病": {"checkbox": true/false},
      "長照險": {"checkbox": true/false},
      "失能險": {"checkbox": true/false},
      "保障缺口": {"rich_text": [{"text": {"content": "缺口列表"}}]},
      "最後更新": {"date": {"start": "YYYY-MM-DD"}}
    }
  }'
```

---

### Step 7：輸出摘要確認

對話中輸出文字摘要讓使用者確認：

```
📊 全險圖更新完成 — [保戶姓名]
================================
【定額醫療】凱基人壽 QDHL2-1000 終身
  住院日額 1,000/日 | 加護 2,000/日 | 重大疾病 30萬
【防癌險】凱基人壽 新康健93A 終身
  初次罹癌 10萬 | 身故 30萬 | 住院 2,000/日

❌ 保障缺口：無實支實付／無意外險／無壽險／無長照失能

✅ Notion 已完整更新：[頁面連結]
================================
```

---

## Notion 設定
- **Token：** `ntn_m1060393373H1mMEAWnYw76EwIP0JkTfM02putDmBPu6SM`
- **保單分析根目錄：** `36c03f34-519d-800a-893f-efe320a82516`
- **保戶總覽 Database：** `36c03f34-519d-8195-a86d-d63e7b85acad`
- **🧠 保險知識庫頁面：** `36c03f34-519d-8158-8e96-e7a8415c7543`
- **📦 產品規格庫 Database：** `36c03f34-519d-8165-91d1-cb3eb94c7722`
- **🔧 修正記錄 Database：** `36c03f34-519d-81d0-9f6f-dfa9c014a07a`
- **Notion API Version：** `2022-06-28`

---

## 分析後回存知識庫

每次完成分析後，若有以下情況，**必須回存**：

**新商品（產品規格庫沒有）→ 新增一筆規格：**
```bash
curl -s -X POST "https://api.notion.com/v1/pages" \
  -H "Authorization: Bearer ntn_m1060393373H1mMEAWnYw76EwIP0JkTfM02putDmBPu6SM" \
  -H "Notion-Version: 2022-06-28" \
  -H "Content-Type: application/json" \
  -d '{"parent": {"database_id": "36c03f34-519d-8165-91d1-cb3eb94c7722"}, "properties": {...}}'
```

**使用者修正了任何欄位 → 新增一筆修正記錄：**
```bash
curl -s -X POST "https://api.notion.com/v1/pages" \
  -H "Authorization: Bearer ntn_m1060393373H1mMEAWnYw76EwIP0JkTfM02putDmBPu6SM" \
  -H "Notion-Version: 2022-06-28" \
  -H "Content-Type: application/json" \
  -d '{"parent": {"database_id": "36c03f34-519d-81d0-9f6f-dfa9c014a07a"}, "properties": {...}}'
```

---

## 注意事項
- 保誠人壽已被凱基人壽收購，保單公司統一顯示「凱基人壽（原保誠）」
- 定期型保單要標明保障到幾歲
- 同一份 PDF 可能含多份附約，每份分開解析後列在主約下方
- 刪除舊 block 前先確認 PAGE_ID 正確，避免誤刪其他保戶頁面
- 若頁面 block 超過 100 個，需分批刪除（先取第一批 100 個刪完，再取下一批）
