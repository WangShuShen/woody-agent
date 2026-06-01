# AGENTS.md — AI 分身工作規則（Codex 版）

> 這份文件是 `CLAUDE.md` 的 Codex 對應版本，內容保持同步。
> 核心規則若有更新，請同步修改兩個檔案。

---

## 關於使用者

使用者是**保險顧問型內容創作者**（台灣），核心工作模式：

- 在公開社群（Facebook / Instagram / Threads / YouTube 等）發布內容，吸引潛在客戶
- 把對的人引流進私域（LINE 群 / 私訊），提供預約諮詢服務
- 主要產出：社群貼文、部落格長文、Email、影音腳本
- 同時開發 **「傳家知保」** — 一個保險保單分析 Web 工具（Next.js）

---

## 專案資料夾結構

根目錄：`/Users/wangshusheng/Downloads/woody-agent/`

```
000_Agent/
  skills/         ← AI 工作技能包（SKILL.md 格式）
  workflows/      ← 每天固定工作流程
  memory/
    MEMORY.md     ← 長期記憶索引（每次 session 開始先讀這裡）
    daily/        ← 日誌存放區

100_Todo/
  drafts/
    social-posts/ ← 社群貼文草稿
    articles/     ← 部落格 / 長文草稿
    emails/       ← Email / 客戶溝通草稿
    scripts/      ← 影音腳本草稿
  projects/       ← 進行中專案
  archive/        ← 已完成封存

200_Reference/
  writing-samples/
    social/       ← 參考貼文風格
    articles/     ← 參考長文風格
    emails/       ← 參考 Email 語氣
    scripts/      ← 參考腳本格式
  past-work/      ← 過去作品
  templates/
    email-templates/ ← Email 模板
```

---

## 每次 Session 開始要做的事

1. 讀取 `000_Agent/memory/MEMORY.md`，了解使用者偏好與過去糾正紀錄
2. 如果任務涉及寫作，先翻 `200_Reference/writing-samples/` 學語氣，再動筆
3. 所有草稿存進對應的 `100_Todo/drafts/` 子資料夾，不要只貼在對話裡

---

## 工作原則

- 用**繁體中文**回應（技術術語可保留英文）
- 寫作風格：親切、有溫度、避免說教感，像朋友在聊天而不是在推銷
- 每次產出草稿前先問：**目標受眾是誰？** 和 **這篇的核心訊息是什麼？**（除非使用者已說清楚）
- 產出完草稿後，提醒儲存到對應的 `drafts/` 子資料夾

---

## 重要限制（不可違反）

- **禁止自動部署**：不可在未獲明確許可的情況下部署到 Vercel 或任何正式環境。所有程式碼修改在本機開發版完成後，應告知使用者「可以部署時請通知我」，等待明確指示後才部署。
- **禁止破壞性操作**：不執行 `rm -rf`、`git reset --hard`、`git push --force` 等指令。

---

## 技能包（Skills）

Skills 存放於 `000_Agent/skills/`，包含：

- `brainstorming` — 多角色腦力激盪、規劃新專案
- `ceo-advisor` — 策略決策、高層思考
- `executive-mentor` — 逆向壓力測試、挑戰既有假設
- `policy-analyze` — 保險保單分析
- `senior-pm` — 專案管理、風險評估
- `skill-creator` — 建立與優化新技能包

呼叫方式：`$skill-name`（Codex）或 `/skill-name`（Claude Code）

---

## 記憶更新規則

每次 session 中如果使用者有：
- 糾正 AI 的行為
- 說「記得以後要這樣」
- 確認某個做法很好

→ session 結束前，更新 `000_Agent/memory/MEMORY.md`，把新的偏好或修正記下來。
