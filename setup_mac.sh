#!/bin/bash
# 在新 Mac 上設定保單爬蟲環境（一次執行即可）
# 用法：bash setup_mac.sh
set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
echo "📁 專案目錄：$REPO_DIR"
echo ""

# ── 1. Python 套件 ─────────────────────────────────────────
echo "📦 安裝 Python 套件..."
pip3 install -r "$REPO_DIR/100_Todo/projects/requirements.txt"
echo ""

# ── 2. Playwright 瀏覽器 ───────────────────────────────────
echo "🌐 安裝 Playwright Chromium（用於爬蟲 + CAPTCHA）..."
python3 -m playwright install chromium
echo ""

# ── 3. Claude Code skills symlink ─────────────────────────
echo "🔗 設定 Claude Code skills 連結..."
mkdir -p ~/.claude
SKILLS_TARGET="$REPO_DIR/000_Agent/skills"

if [ -L ~/.claude/skills ]; then
    echo "   ✅ ~/.claude/skills 已是 symlink（$(readlink ~/.claude/skills)）"
elif [ -d ~/.claude/skills ]; then
    echo "   ⚠️  ~/.claude/skills 是實體資料夾，先備份為 ~/.claude/skills.bak"
    mv ~/.claude/skills ~/.claude/skills.bak
    ln -s "$SKILLS_TARGET" ~/.claude/skills
    echo "   ✅ 已建立 symlink → $SKILLS_TARGET"
else
    ln -s "$SKILLS_TARGET" ~/.claude/skills
    echo "   ✅ ~/.claude/skills → $SKILLS_TARGET"
fi
echo ""

# ── 4. CLAUDE.md 設定 ─────────────────────────────────────
echo "📝 設定 Claude Code 記憶目錄..."
MEMORY_DIR="$HOME/.claude/projects"
mkdir -p "$MEMORY_DIR"
echo "   ✅ $MEMORY_DIR 已建立"
echo ""

# ── 5. 確認憑證 ────────────────────────────────────────────
echo "🔐 確認憑證..."

if [ ! -f "$REPO_DIR/000_Agent/google_credentials.json" ]; then
    echo "   ❌ 缺少 000_Agent/google_credentials.json"
    echo "      請從 Google Cloud Console → API 和服務 → 憑證"
    echo "      下載 OAuth 2.0 用戶端 ID（桌面應用程式類型）放到 000_Agent/"
else
    echo "   ✅ Google OAuth 憑證存在"
fi

if [ ! -f "$REPO_DIR/policy-analyzer/.env" ]; then
    echo "   ⚠️  缺少 policy-analyzer/.env，建立模板..."
    cat > "$REPO_DIR/policy-analyzer/.env" << 'ENVEOF'
ANTHROPIC_API_KEY=sk-ant-請填入你的金鑰
ENVEOF
    echo "   📝 已建立 policy-analyzer/.env 模板，請填入 ANTHROPIC_API_KEY"
else
    echo "   ✅ .env 已存在"
fi
echo ""

# ── 完成 ───────────────────────────────────────────────────
echo "════════════════════════════════════════════"
echo "  ✅ 設定完成！"
echo ""
echo "  下一步："
echo "  1. 填入 policy-analyzer/.env 的 ANTHROPIC_API_KEY"
echo "  2. 確認 000_Agent/google_credentials.json 已放好"
echo "  3. 執行爬蟲："
echo "     cd $REPO_DIR/100_Todo/projects"
echo "     python3 pipeline.py --company 211 --limit 1  # 測試1筆"
echo "     python3 pipeline.py --company 211             # 跑全部"
echo "════════════════════════════════════════════"
