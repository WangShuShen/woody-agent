#!/usr/bin/env python3
"""
南山人壽 5 筆爬蟲（不需要 API key，CAPTCHA 由外部傳入）
用法：
  python3 scrape_nanshan.py <captcha_value>
  # 或讓腳本自己截圖後暫停等你輸入
"""
import json, re, sys, time, base64, ssl, urllib.request
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).parent
TMP_DIR  = BASE_DIR / "tmp" / "pdfs"
TMP_DIR.mkdir(parents=True, exist_ok=True)
BASE_URL = "https://insprod.tii.org.tw"
LIMIT    = 5
COMPANY  = "206"

CATEGORY_LABEL = {
    "健康保險": "[健]", "傳統型壽險": "[壽]", "投資型壽險": "[壽]",
    "傷害保險": "[傷]", "傳統型年金": "[年]", "投資型年金": "[年]",
}
CATEGORY_DETECT = [
    ("健康保險",   ["醫療", "癌症", "健康", "住院", "防癌", "長期看護", "重大疾病"]),
    ("傷害保險",   ["意外傷害", "傷害保險", "交通意外", "旅行平安"]),
    ("投資型壽險", ["投資型壽", "變額壽"]),
    ("投資型年金", ["投資型年金", "變額年金"]),
    ("傳統型年金", ["年金"]),
    ("傳統型壽險", ["壽險", "終身保險", "定期壽", "還本"]),
]
EXCLUDE_KEYWORDS = ["團體", "團險", "集體", "團保", "Group", "group",
                    "延續條款", "停售後續", "附約延續"]

_SSL_CTX = ssl._create_unverified_context()

def detect_category(name):
    for cat, kws in CATEGORY_DETECT:
        if any(k in name for k in kws):
            return cat, CATEGORY_LABEL.get(cat, "[其]")
    return "其他", "[其]"

def should_exclude(name):
    return any(k in name for k in EXCLUDE_KEYWORDS)

def extract_uuid(url):
    if not url: return ""
    return parse_qs(urlparse(url).query).get("id", [""])[0]

def fmt_date(d):
    return d.strip().replace("/", "-") if d else ""

def make_filename(label, name, sale, stop):
    safe = re.sub(r'[\\/*?:"<>|]', "", name)
    suffix = f"{fmt_date(sale)}~{fmt_date(stop)}" if stop else f"{fmt_date(sale)}~"
    return f"{label} {safe}_{suffix}"

def get_pdf_url(page, product_id):
    try:
        page.goto(f"{BASE_URL}/DetailList.aspx?productId={product_id}",
                  wait_until="networkidle", timeout=15000)
        links = page.query_selector_all("a[href*='Open2.ashx']")
        best, fallback = "", ""
        for lnk in links:
            text = lnk.inner_text().strip()
            href = lnk.get_attribute("href") or ""
            full = f"{BASE_URL}/{href}" if href.startswith("Open2") else href
            if text.endswith("-A.pdf"):
                best = full; break
            if not fallback: fallback = full
        return best or fallback
    except Exception as e:
        print(f"      ⚠️  PDF URL 失敗 ({product_id}): {e}")
    return ""

def download_pdf(url, dest, cookies):
    try:
        req = urllib.request.Request(url)
        req.add_header("Cookie", "; ".join(f"{k}={v}" for k,v in cookies.items()))
        req.add_header("Referer", BASE_URL)
        req.add_header("User-Agent", "Mozilla/5.0")
        with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as r:
            dest.write_bytes(r.read())
        return True
    except Exception as e:
        print(f"   ⚠️  PDF 下載失敗: {e}")
    return False

def main(captcha_answer=None):
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx  = browser.new_context()
        page = ctx.new_page()

        print("🌐 開啟 TII...")
        page.goto(f"{BASE_URL}/Query.aspx", wait_until="networkidle")
        page.select_option("select[name='categoryId']", "人身保險")
        page.wait_for_timeout(500)

        # 找南山
        opts = page.query_selector_all("select[name='CompanyID'] option")
        target, company_name = None, ""
        for o in opts:
            val = o.get_attribute("value") or ""
            if val.startswith(COMPANY):
                target = val
                company_name = o.inner_text().split("-",1)[-1].strip()
                break

        page.select_option("select[name='CompanyID']", target)
        print(f"✅ 已選：{company_name}")

        # CAPTCHA
        if not captcha_answer:
            img_bytes = page.locator("img[src*='bmp.ashx']").screenshot()
            cap_path  = Path("/tmp/captcha_current.png")
            cap_path.write_bytes(img_bytes)
            print(f"\n📸 CAPTCHA 已存至：{cap_path}")
            print("   請看圖後輸入數字：", end="", flush=True)
            captcha_answer = input().strip()

        print(f"🤖 輸入 CAPTCHA：{captcha_answer}")
        page.fill("input[name='bmpC']", captcha_answer)
        page.click("input[name='Go222']")
        page.wait_for_load_state("networkidle", timeout=15000)

        if not page.query_selector("a[href*='DetailList.aspx']"):
            print("❌ CAPTCHA 錯誤，沒有結果")
            browser.close()
            return []

        print("✅ CAPTCHA 正確，開始爬取...")
        results_url = page.url

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
                items.push({ productName: link.innerText.trim(), productId: m[1],
                             saleDate: dates[0]||"", stopDate: dates[1]||"" });
            });
            return items;
        }""")

        for item in page_items:
            if len(results) >= LIMIT: break
            if should_exclude(item["productName"]): continue

            cat, label = detect_category(item["productName"])
            filename   = make_filename(label, item["productName"], item["saleDate"], item["stopDate"])

            pdf_url  = get_pdf_url(page, item["productId"])
            pdf_path = ""
            if pdf_url:
                dest = TMP_DIR / f"{item['productId']}.pdf"
                cookies = {c["name"]: c["value"] for c in ctx.cookies()}
                print(f"   📥 {filename[:50]}...")
                if download_pdf(pdf_url, dest, cookies):
                    pdf_path = str(dest)
            page.goto(results_url, wait_until="networkidle", timeout=15000)

            results.append({
                "company":     company_name,
                "productName": item["productName"],
                "planCode":    item["productId"],
                "pdfUUID":     extract_uuid(pdf_url),
                "category":    cat,
                "label":       label,
                "filename":    filename,
                "saleDate":    item["saleDate"],
                "stopDate":    item["stopDate"],
                "active":      not bool(item["stopDate"]),
                "pdfUrl":      pdf_url,
                "pdfPath":     pdf_path,
            })
            print(f"   ✅ [{len(results)}/{LIMIT}] {item['productName']}")

        browser.close()

    out = BASE_DIR / "206_products.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ 完成！{len(results)} 筆 → {out}")
    pdf_ok = sum(1 for r in results if r.get("pdfPath"))
    print(f"   PDF 下載：{pdf_ok}/{len(results)}")
    return results

if __name__ == "__main__":
    cap = sys.argv[1] if len(sys.argv) > 1 else None
    main(cap)
