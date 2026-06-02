# 保單分析品質升級：Skills 等級 + Web API 成本控制

> 建立時間：2026-06-01
> 類型：改善優化
> 狀態：待執行

---

## 你要做的事（一句話版）

把 Claude Code Skills 的分析品質帶進網站，同時用 Prompt Caching 讓多位業務員使用時 token 成本不爆炸。

## 背景和動機

現在的問題：
- **網站 API**：單一大提示詞、一次 Sonnet call、沒有分層，品質不穩
- **Skills 系統**：多層次（辨識→專科→驗證）、提示詞精細，品質好但在 Claude Code 跑有額外 overhead，每次吃 20,000–50,000 token
- **目標**：讓網站達到 Skills 品質，但每次分析成本控制在 $0.003–0.008 USD（而非 Claude Code 的 $0.06–0.15）

## 具體步驟

### Step 1：建立每個險種的專科 system prompt 檔

- 在 `policy-analyzer/lib/specialistPrompts/` 新增以下檔案，每個對應一個 SKILL.md：
  - `fixedMedical.ts` — 定額醫療
  - `medicalReimbursement.ts` — 醫療實支
  - `accident.ts` — 意外傷害
  - `cancer.ts` — 防癌險
  - `criticalCard.ts` — 重大傷病卡
  - `criticalSpecific.ts` — 特定傷病
  - `disability.ts` — 失能險
  - `ltc.ts` — 長照
  - `life.ts` — 壽險
- 每個檔案的內容：從對應 SKILL.md 提取核心分析規則（術語對照表、換算規則、JSON schema），去掉 Claude Code 角色扮演用語，改寫成純 API system prompt 格式
- 產出：每個 `.ts` export 一個 `SYSTEM_PROMPT: string` 常數

### Step 2：在 `/api/analyze` 加入「Step 0：快速辨識險種」

- 在現有分析流程最前面插入一個輕量 Haiku call（約 300–500 token）
- 目的：從文件快速判斷是哪個 category（定額醫療/意外險/防癌險/...）
- 輸出 JSON：`{ "category": "定額醫療" }`
- 邏輯：`prefill` 裡的 Haiku 已知可以抓 planCode/year，這裡同樣邏輯再抓 category
- 注意：若 `prefill` 結果已含 category，直接沿用，不重複呼叫

### Step 3：主分析改用「專科 system prompt」+ Prompt Caching

- 根據 Step 2 辨識出的 category，載入對應的 `specialistPrompts/xxx.ts`
- Anthropic API call 改為：

```typescript
const response = await client.messages.create({
  model: "claude-sonnet-4-6",
  max_tokens: 4096,
  system: [
    {
      type: "text",
      text: SPECIALIST_SYSTEM_PROMPT,
      cache_control: { type: "ephemeral" },  // ← 這行讓相同 system prompt 後續快取
    },
  ],
  messages: [{ role: "user", content: contentBlocks }],
  betas: ["prompt-caching-2024-07-31"],
});
```

- 機制：第一個使用者分析「定額醫療」保單時，server 付全額 token；之後 5 分鐘內任何人分析同類型，system prompt 直接從快取拿，省掉約 80–90% 輸入 token
- 注意：Prompt Caching 目前 Anthropic 免費提供，快取命中後輸入 token 費用降為 10%

### Step 4：驗證步驟（/api/verify）維持現狀

- `verify` API 現在是獨立的，不動
- 後續可以考慮用 Haiku 做 quick sanity check（但這次不做，先觀察品質再決定）

### Step 5：測試對比

- 取 5 份過去分析過的保單 PDF，分別用：
  - 舊的 `/api/analyze`（單一大提示詞）
  - 新的 `/api/analyze`（多步驟 + 專科 prompt + caching）
- 比對輸出 JSON 的欄位填寫完整率、格式合規率
- 確認 token 用量有下降（看 Anthropic console usage）

## 預計成果

做完以後你會得到：
- 保單分析品質比現在高，接近 Skills 系統的準確度
- 多位業務員同時分析同類型保單時，成本大幅下降（快取命中後系統提示詞 token 費用降 90%）
- 從長遠看：每份保單的 token 成本從 ~$0.01 降到 ~$0.003–0.006
- Skills 的提示詞邏輯和 API 可以對齊（兩邊都更容易維護）

## 不包含在這次的範圍

- 不改動 prefill API（Haiku 識別基本欄位，現在已 OK）
- 不改動 verify API（保持獨立）
- 不做多輪對話或 human-in-the-loop（下個版本再考慮）
- 不部署（你說先不要動部署版）

## 可能遇到的風險

- **Prompt Caching 快取時間只有 5 分鐘**：低流量時效果有限；可以加一個「保溫」機制（每 4 分鐘自動發一個空 request 維持快取），但這次先不加
- **專科 prompt 移植可能漏掉細節**：SKILL.md 有一些 Claude Code 特有的角色指令需要過濾掉，建議每個 prompt 移植完先用 1–2 份真實保單測試
- **category 辨識錯誤**：若 Step 2 Haiku 把險種判斷錯，後面用錯 specialist prompt 會讓結果更差；解法：辨識結果帶 confidence score，低於 80% 就回退用現有的通用大 prompt

## 執行優先順序

1. **先做 Step 1 + Step 3**（移植 2–3 個最常用的險種 prompt + 加 caching），就能看到品質和成本的明顯差異
2. 觀察 1–2 週後，再補齊剩下的險種
3. Step 2（Haiku 辨識）如果 Step 1+3 效果夠好，可以省略（讓使用者手動選類別也行）
