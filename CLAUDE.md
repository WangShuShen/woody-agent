# CLAUDE.md — AI 分身工作規則

<!-- AI 分身起始助手紀錄:START -->
## 關於我

我是**保險顧問型內容創作者**。核心工作模式：
- 在公開社群（Facebook / Instagram / Threads / YouTube 等）發布內容，吸引潛在客戶
- 把對的人引流進私域（LINE 群 / 私訊），提供預約諮詢服務
- 主要產出：社群貼文、部落格長文、Email、影音腳本

## 我的資料夾結構

根目錄：`/Users/wangshusheng/Downloads/woody-agent/`

```
000_Agent/
  skills/         ← AI 工作手冊（symlink 到 ~/.claude/skills/）
  workflows/      ← 每天主動喊的固定儀式
  memory/
    MEMORY.md     ← 長期記憶（每次 session 開始先讀這裡）
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

## AI 每次 session 開始要做的事

1. 讀取 `000_Agent/memory/MEMORY.md`，了解我的偏好與過去糾正記錄
2. 如果任務涉及寫作，先翻 `200_Reference/writing-samples/` 學我的語氣，再動筆
3. 所有草稿存進對應的 `100_Todo/drafts/` 子資料夾，不要只貼在對話裡

## 工作原則

- 用**繁體中文**回應（技術術語可保留英文）
- 寫作風格：親切、有溫度、避免說教感，像朋友在聊天而不是在推銷
- 每次產出草稿前先問我：**目標受眾是誰？** 和 **這篇的核心訊息是什麼？**（除非我已經說清楚了）
- 產出完草稿後，提醒我儲存到對應的 `drafts/` 子資料夾

## 記憶更新規則

每次 session 中如果我有：
- 糾正 AI 的行為
- 說「記得以後要這樣」
- 確認某個做法很好

→ session 結束前，AI 要更新 `000_Agent/memory/MEMORY.md`，把新的偏好或修正記下來。

<!-- AI 分身起始助手紀錄:END -->
