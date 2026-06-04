#!/usr/bin/env python3
"""
TII 保險商品資料庫爬蟲
用法：
  python3 scraper.py --company 206          # 南山人壽
  python3 scraper.py --company 206 --limit 1  # 只取前1筆（測試用）
"""

import json
import re
import argparse
import base64
import ssl
import urllib.request
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import anthropic

# 自動載入 .env（本目錄 → 上層 → policy-analyzer 同層）
try:
    from dotenv import load_dotenv
    _root = Path(__file__).parent.parent.parent  # woody-agent/
    for p in [Path(__file__).parent, _root / "100_Todo/projects",
              _root, _root / "policy-analyzer"]:
        if (p / ".env").exists():
            load_dotenv(p / ".env"); break
except ImportError:
    pass
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# macOS 上 Python 預設不信任系統憑證，這個 context 跳過 SSL 驗證
_SSL_CTX = ssl._create_unverified_context()

# ── 路徑設定 ──────────────────────────────────────
BASE_DIR = Path(__file__).parent
TMP_DIR  = BASE_DIR / "tmp" / "pdfs"
TMP_DIR.mkdir(parents=True, exist_ok=True)
BASE_URL = "https://insprod.tii.org.tw"

# TII 大類代碼 → 前綴標籤
CATEGORY_LABEL = {
    "健康保險":   "[健]",
    "傳統型壽險": "[壽]",
    "投資型壽險": "[壽]",
    "傷害保險":   "[傷]",
    "傳統型年金": "[年]",
    "投資型年金": "[年]",
}

# 商品名稱關鍵字 → 自動偵測類別（順序重要，先比對的優先）
CATEGORY_DETECT = [
    ("健康保險",   ["醫療", "癌症", "健康", "住院", "防癌", "長期看護", "重大疾病"]),
    ("傷害保險",   ["意外傷害", "傷害保險", "交通意外", "旅行平安"]),
    ("投資型壽險", ["投資型壽", "變額壽"]),
    ("投資型年金", ["投資型年金", "變額年金"]),
    ("傳統型年金", ["年金"]),
    ("傳統型壽險", ["壽險", "終身保險", "定期壽", "還本"]),
]

# 團體保險排除關鍵字（商品名稱）
GROUP_NAME_KEYWORDS = ["團體", "團險", "集體", "團保", "Group", "group"]
# 延續條款排除關鍵字（需精確匹配，避免誤殺正常商品）
CONTINUATION_KEYWORDS = ["延續條款", "停售後續", "附約延續"]


def detect_category(name: str) -> tuple[str, str]:
    """從商品名稱猜測保險類別，回傳 (類別, 標籤)"""
    for cat, keywords in CATEGORY_DETECT:
        if any(kw in name for kw in keywords):
            return cat, CATEGORY_LABEL.get(cat, "[其]")
    return "其他", "[其]"


def should_exclude(name: str) -> bool:
    """排除團體保險與延續條款"""
    if any(kw in name for kw in GROUP_NAME_KEYWORDS):
        return True
    if any(kw in name for kw in CONTINUATION_KEYWORDS):
        return True
    return False


def extract_pdf_uuid(pdf_url: str) -> str:
    """從 TII PDF URL 抽取 UUID，例：Open2.ashx?id=08143f9d-... → 08143f9d-..."""
    if not pdf_url:
        return ""
    parsed = urlparse(pdf_url)
    return parse_qs(parsed.query).get("id", [""])[0]


def fmt_date(roc_date: str) -> str:
    """090/09/01 → 090-09-01（民國年，斜線改短橫線）"""
    return roc_date.strip().replace("/", "-") if roc_date else ""


def make_filename(label: str, name: str, sale: str, stop: str) -> str:
    """[健] 南山人壽終身醫療保險_090-09-01~094-05-31"""
    date_part = fmt_date(sale)
    stop_part = fmt_date(stop)
    suffix = f"{date_part}~{stop_part}" if stop_part else f"{date_part}~"
    safe_name = re.sub(r'[\\/*?:"<>|]', "", name)
    return f"{label} {safe_name}_{suffix}"


def solve_captcha(page) -> str:
    """用 Claude Vision 自動辨識 CAPTCHA 數字"""
    captcha_bytes = page.locator("img[src*='bmp.ashx']").screenshot()
    b64 = base64.b64encode(captcha_bytes).decode()
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=10,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                {"type": "text", "text": "這是圖形驗證碼，只含數字。請只回覆這些數字，不要其他文字。"}
            ]
        }]
    )
    return re.sub(r"[^0-9]", "", resp.content[0].text.strip())


def get_terms_pdf_url(page, product_id: str) -> str:
    """用 Playwright page 進入 DetailList.aspx，取出「保單條款」(-A.pdf) 的 URL"""
    detail_url = f"{BASE_URL}/DetailList.aspx?productId={product_id}"
    try:
        page.goto(detail_url, wait_until="networkidle", timeout=15000)
        # 優先找 -A.pdf（保單條款），備援找任一 Open2.ashx
        links = page.query_selector_all("a[href*='Open2.ashx']")
        terms_url = ""
        fallback_url = ""
        for link in links:
            text = link.inner_text().strip()
            href = link.get_attribute("href")
            if not href:
                continue
            full_url = f"{BASE_URL}/{href}" if href.startswith("Open2") else href
            if text.endswith("-A.pdf"):
                terms_url = full_url
                break
            if not fallback_url:
                fallback_url = full_url
        return terms_url or fallback_url
    except Exception as e:
        print(f"      ⚠️  取得 PDF URL 失敗 ({product_id})：{e}")
    return ""


def download_pdf(url: str, dest: Path, cookies: dict) -> bool:
    """下載 PDF，回傳是否成功"""
    try:
        req = urllib.request.Request(url)
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
        req.add_header("Cookie", cookie_str)
        req.add_header("Referer", BASE_URL)
        req.add_header("User-Agent", "Mozilla/5.0")
        with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
            dest.write_bytes(resp.read())
        return True
    except Exception as e:
        print(f"   ⚠️  PDF 下載失敗：{e}")
        return False


def scrape(company_code: str, limit: int = 0) -> list[dict]:
    """主爬蟲：開瀏覽器 → 自動解 CAPTCHA → 爬結果"""
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=200)
        context = browser.new_context()
        page    = context.new_page()

        print("🌐 開啟 TII 查詢頁面...")
        page.goto(f"{BASE_URL}/Query.aspx", wait_until="networkidle")

        # ── 選公司 ────────────────────────────────────
        page.select_option("select[name='categoryId']", "人身保險")
        page.wait_for_timeout(500)

        company_options = page.query_selector_all("select[name='CompanyID'] option")
        target_value = None
        company_name = ""
        for opt in company_options:
            val = opt.get_attribute("value") or ""
            if val.startswith(company_code):
                target_value = val
                company_name = opt.inner_text().split("-", 1)[-1].strip()
                break

        if not target_value:
            print(f"❌ 找不到公司代碼 {company_code}")
            browser.close()
            return []

        page.select_option("select[name='CompanyID']", target_value)
        print(f"✅ 已選擇公司：{company_name}")

        # ── 自動解 CAPTCHA（最多重試 3 次）──────────────
        MAX_RETRY = 3
        solved = False
        for attempt in range(MAX_RETRY):
            try:
                captcha_val = solve_captcha(page)
                print(f"🤖 CAPTCHA 辨識：{captcha_val}（第 {attempt + 1} 次）")
                if not captcha_val:
                    raise ValueError("辨識結果為空")

                page.fill("input[name='bmpC']", captcha_val)
                page.click("input[name='Go222']")
                page.wait_for_load_state("networkidle", timeout=15000)

                if page.query_selector("a[href*='DetailList.aspx']"):
                    print("✅ CAPTCHA 正確，結果已載入")
                    solved = True
                    break

                print(f"⚠️  未看到結果（{attempt + 1}/{MAX_RETRY}）")
            except Exception as e:
                print(f"⚠️  CAPTCHA 失敗：{e}（{attempt + 1}/{MAX_RETRY}）")

            if attempt < MAX_RETRY - 1:
                page.goto(f"{BASE_URL}/Query.aspx", wait_until="networkidle")
                page.select_option("select[name='categoryId']", "人身保險")
                page.wait_for_timeout(500)
                page.select_option("select[name='CompanyID']", target_value)
                page.wait_for_timeout(500)

        if not solved:
            print()
            print("=" * 55)
            print("  自動辨識失敗，請手動解驗證碼：")
            print("  1. 在瀏覽器輸入驗證數字並按查詢")
            print("  2. 看到結果後，回到這裡按 Enter")
            print("=" * 55)
            try:
                input("  按 Enter 繼續...")
            except EOFError:
                browser.close()
                return []

        # 取得 cookies 供 PDF 下載使用
        cookies_list = context.cookies()
        cookies = {c["name"]: c["value"] for c in cookies_list}

        # ── 爬所有分頁 ────────────────────────────────
        page_num = 1
        while True:
            print(f"\n📄 爬取第 {page_num} 頁...")
            page.wait_for_timeout(800)

            # 用 JS 一次把本頁所有商品資料抓成純資料（避免導航後 element handle 失效）
            page_items = page.evaluate("""() => {
                const items = [];
                document.querySelectorAll("tr").forEach(row => {
                    const link = row.querySelector("a[href*='DetailList.aspx']");
                    if (!link) return;
                    const href = link.getAttribute("href") || "";
                    const m = href.match(/productId=([^&]+)/);
                    if (!m) return;
                    const dates = [];
                    row.querySelectorAll("td").forEach(td => {
                        if (/^\\d{3}\\/\\d{2}\\/\\d{2}$/.test(td.innerText.trim()))
                            dates.push(td.innerText.trim());
                    });
                    items.push({
                        productName: link.innerText.trim(),
                        productId:   m[1],
                        saleDate:    dates[0] || "",
                        stopDate:    dates[1] || "",
                    });
                });
                return items;
            }""")

            results_url = page.url
            page_found  = 0

            for item in page_items:
                if should_exclude(item["productName"]):
                    continue
                if limit > 0 and len(results) >= limit:
                    break

                category, label = detect_category(item["productName"])
                filename = make_filename(label, item["productName"], item["saleDate"], item["stopDate"])

                # 用 Playwright 進入詳細頁取 PDF URL（保持 session）
                pdf_url  = get_terms_pdf_url(page, item["productId"])
                pdf_path = ""

                if pdf_url:
                    dest = TMP_DIR / f"{item['productId']}.pdf"
                    print(f"   📥 {filename[:45]}...")
                    cookies_list = context.cookies()
                    cookies = {c["name"]: c["value"] for c in cookies_list}
                    if download_pdf(pdf_url, dest, cookies):
                        pdf_path = str(dest)

                # 回到結果頁
                page.goto(results_url, wait_until="networkidle", timeout=15000)

                results.append({
                    "company":     company_name,
                    "productName": item["productName"],
                    "planCode":    item["productId"],
                    "pdfUUID":     extract_pdf_uuid(pdf_url),
                    "category":    category,
                    "label":       label,
                    "filename":    filename,
                    "saleDate":    item["saleDate"],
                    "stopDate":    item["stopDate"],
                    "active":      not bool(item["stopDate"]),
                    "pdfUrl":      pdf_url,
                    "pdfPath":     pdf_path,
                })
                page_found += 1

            print(f"   本頁找到 {page_found} 筆，累計 {len(results)} 筆")

            if limit > 0 and len(results) >= limit:
                print(f"   達到 limit={limit}，停止")
                break

            # 找下一頁連結（重新查詢，因為剛回到結果頁）
            next_link = page.query_selector(f"a:text('{page_num + 1}')")
            if not next_link:
                next_link = page.query_selector("a:text('[下十頁]')")
            if not next_link:
                print("   沒有下一頁，爬取完成")
                break

            next_link.click()
            page_num += 1

        browser.close()

    return results


def main():
    parser = argparse.ArgumentParser(description="TII 保險商品爬蟲")
    parser.add_argument("--company", required=True, help="公司代碼，例如 206（南山人壽）")
    parser.add_argument("--limit",   type=int, default=0, help="最多抓幾筆（0=全部）")
    parser.add_argument("--output",  default="", help="輸出 JSON 路徑")
    args = parser.parse_args()

    output_path = args.output or str(BASE_DIR / f"{args.company}_products.json")

    print(f"🚀 開始爬取公司代碼 {args.company}...")
    if args.limit:
        print(f"   測試模式：最多 {args.limit} 筆")

    products = scrape(args.company, args.limit)

    Path(output_path).write_text(
        json.dumps(products, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(f"\n✅ 完成！共 {len(products)} 筆")
    print(f"   已儲存：{output_path}")
    pdf_ok = sum(1 for p in products if p.get("pdfPath"))
    print(f"   PDF 下載成功：{pdf_ok} 筆")

    return products


if __name__ == "__main__":
    main()
