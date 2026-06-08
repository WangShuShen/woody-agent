# 保單自動化流水線 — 新機器設定指南

> 適用情境：在另一台 Mac/Linux 機器上設定相同流水線，爬取不同保險公司的資料。

---

## 一、移轉哪些檔案

### 從這台機器複製過去（可公開，無敏感資料）

```
000_Agent/
  google_credentials.json   ← Google OAuth client secret（兩台共用同一份）
  skills/db-scrape/
  skills/db-extract/
  skills/db-review/
  skills/db-archive/
  skills/db-agent/

100_Todo/projects/
  scraper.py
  analyzer.py
  push_to_sheets.py
  pipeline.py
  notifier.py
  requirements.txt
  STATUS.md
```

### 不要複製（機器專屬）

| 檔案 | 原因 |
|------|------|
| `000_Agent/authorized_user.json` | OAuth token，新機器需重新授權 |
| `uuid_registry.json` | 狀態登記，兩台用 git 同步（見第四節） |
| `analyzed/` 資料夾 | 已分析結果，看需求決定要不要複製 |
| `tmp/` 資料夾 | 爬蟲下載的暫存 PDF，不需要 |

---

## 二、新機器安裝步驟

```bash
# 1. clone 整個 repo（或只 scp 上面的檔案）
git clone <repo_url>

# 2. 安裝 Python 套件
cd 100_Todo/projects
pip3 install -r requirements.txt

# 3. 安裝 Playwright 瀏覽器核心（必要，第一次要跑）
playwright install chromium

# 4. 複製 google_credentials.json 到 000_Agent/
#    （從這台機器 scp 過來或用隨身碟）
scp 這台IP:~/Downloads/woody-agent/000_Agent/google_credentials.json \
    ~/Downloads/woody-agent/000_Agent/

# 5. 建立 .env（Anthropic API Key）
echo "ANTHROPIC_API_KEY=sk-ant-xxxx" > ~/Downloads/woody-agent/policy-analyzer/.env

# 6. 首次執行時重新授權 Google（會開瀏覽器）
python3 push_to_sheets.py --help
# 如果 authorized_user.json 不存在，gspread 會自動打開瀏覽器要求登入
```

---

## 三、找三商美邦人壽的公司代碼

TII 的公司代碼在網址或查詢參數裡。有兩種方法：

### 方法 A：直接查 TII 網站

1. 開 https://www.tii.org.tw/tii/information/Information2/
2. 點選「壽險公司」或「三商美邦人壽」
3. 查看網址或 API 請求，找到 `companyCode` 參數

### 方法 B：用 scraper.py 的探索模式

在 `scraper.py` 裡，公司代碼是傳給爬蟲的 `--company` 參數。
目前已知：

| 公司 | 代碼 |
|------|------|
| 南山人壽 | `206` |
| 三商美邦人壽 | 待確認（推測 `208` 或查 TII 網站） |

找法：打開瀏覽器開發者工具 → Network → 在 TII 商品查詢頁選「三商美邦人壽」→ 查看 XHR 請求的 `companyCode` 值。

---

## 四、兩台機器同步 uuid_registry.json

`uuid_registry.json` 是全域去重表，防止兩台爬蟲重複處理同一份 PDF。

### 推薦做法：git 同步

```bash
# 跑完一批後 push
git add uuid_registry.json
git commit -m "sync: add batch results"
git push

# 另一台跑之前先 pull
git pull
python3 pipeline.py --company 208 --limit 10
```

### 注意事項

- 兩台爬不同公司（206 vs 208），UUID 天然不衝突（UUID 是從 PDF 內容 hash 出來的）
- 同一份 PDF 如果出現在兩家公司（舊有情形），UUID 相同，第二台會自動跳過
- 如果沒有 git，改用 Dropbox / iCloud 同步這個 JSON 檔案也可以

---

## 五、跑流水線

```bash
# 完整跑（爬蟲 → 分析 → 上傳）
python3 pipeline.py --company 208

# 測試一筆
python3 pipeline.py --company 208 --limit 1

# 只分析（不上傳 Drive，省時測試）
python3 pipeline.py --company 208 --analyze-only

# 已有 analyzed/ 結果，跳過 Claude API 直接上傳
python3 pipeline.py --company 208 --upload-only
```

---

## 六、設定 skills（Claude Code 使用）

Skills 存在 `000_Agent/skills/`，需要 symlink 到 `~/.claude/skills/`：

```bash
mkdir -p ~/.claude/skills
ln -sf ~/Downloads/woody-agent/000_Agent/skills/db-scrape  ~/.claude/skills/db-scrape
ln -sf ~/Downloads/woody-agent/000_Agent/skills/db-extract ~/.claude/skills/db-extract
ln -sf ~/Downloads/woody-agent/000_Agent/skills/db-review  ~/.claude/skills/db-review
ln -sf ~/Downloads/woody-agent/000_Agent/skills/db-archive ~/.claude/skills/db-archive
ln -sf ~/Downloads/woody-agent/000_Agent/skills/db-agent   ~/.claude/skills/db-agent
```

---

## 七、Google Drive 資料夾共用

兩台機器寫入**同一個** Google Drive：

- 上傳的 Sheet 和 PDF 都進「保單審核資料庫/待審核/」
- 審核 UI（Review）部署在同一個 Next.js 實例
- 不需要特別設定，只要兩台都用同一個 Google 帳號授權即可
