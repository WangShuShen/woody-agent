#!/usr/bin/env python3
"""
TII 保險商品原始文件 → Google Drive 整理爬蟲

Drive 結構：
  保單原始條款 / {公司} / {主約|附約} / {商品類型} /
    {商品名稱}_{planCode}_{上市日期}_{現售|停售} /
      條款.pdf  費率.pdf  說明.pdf  (有幾種就放幾種)

用法：
  python3 scraper_drive.py --company 206           # 南山人壽，全部
  python3 scraper_drive.py --company 206 --limit 5 # 測試 5 筆
"""

import json
import re
import sys
import argparse
import time
import base64
import ssl
import urllib.request
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import anthropic

try:
    from dotenv import load_dotenv
    _root = Path(__file__).parent.parent.parent
    _loaded = False
    for p in [Path(__file__).parent, _root / "100_Todo/projects", _root, _root / "policy-analyzer"]:
        if (p / ".env.local").exists():
            load_dotenv(p / ".env.local")
            _loaded = True
            break
        if (p / ".env").exists():
            load_dotenv(p / ".env")
            _loaded = True
            break
except ImportError:
    pass

from playwright.sync_api import sync_playwright
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ── 路徑設定 ──────────────────────────────────────
BASE_DIR = Path(__file__).parent
TMP_DIR  = BASE_DIR / "tmp" / "pdfs"
TMP_DIR.mkdir(parents=True, exist_ok=True)

BASE_URL          = "https://insprod.tii.org.tw"
DRIVE_ROOT_FOLDER = "保單原始條款"

CREDENTIALS_FILE = Path(__file__).parent.parent.parent / "000_Agent" / "google_credentials.json"
AUTHORIZED_FILE  = Path(__file__).parent.parent.parent / "000_Agent" / "authorized_user.json"

DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

_SSL_CTX = ssl._create_unverified_context()

# ── 文件後綴 → 中文名稱（None = 跳過）────────────────
_SKIP = object()   # sentinel：有匹配但不要的文件
DOC_SUFFIX_MAP = {
    "-A": "條款",
    "-B": _SKIP,   # 要保書 - 不需要
    "-C": "費率",
    "-D": _SKIP,   # 健康聲明 - 不需要
    "-E": _SKIP,   # 附件 - 不需要
    "-F": "說明",  # 商品說明書
}

# ── 排除（只排產險、延續條款、團險）─────────────────
EXCLUDE_KEYWORDS = [
    "延續條款", "停售後續", "附約延續",
    "產險", "財產保險", "火災保險", "汽車險", "車險",
    "團體", "團險", "集體", "團保", "Group", "group",
]

# ── 主約 / 附約 ───────────────────────────────────
RIDER_KEYWORDS = ["附約", "附加條款", "特約條款"]

# ── 商品類型（細分，順序重要）──────────────────────
PRODUCT_TYPE_DETECT = [
    ("重大疾(傷)病險", ["重大疾病", "特定傷病", "重大傷病", "重大疾(傷)", "七大疾病", "22項"]),
    ("防癌險",         ["癌症", "防癌", "惡性腫瘤"]),
    ("失能險",         ["失能扶助", "失能保險", "長期失能", "失能"]),
    ("長照險",         ["長期照顧", "長照", "巴氏量表", "ADL"]),
    ("傷害險",         ["意外傷害", "傷害保險", "交通意外", "旅行平安", "傷害"]),
    ("醫療實支",       ["實支實付", "實支醫療", "醫療費用補償"]),
    ("醫療定額",       ["醫療保險", "住院醫療", "日額醫療", "醫療給付",
                        "健康保險", "健康", "住院"]),
    ("利率變動型壽險", ["利率變動", "萬能壽險"]),
    ("投資型壽險",     ["投資型", "變額壽"]),
    ("投資型年金",     ["投資型年金", "變額年金"]),
    ("年金險",         ["年金"]),
    ("儲蓄壽險",       ["還本", "保本", "養老"]),
    ("定期壽險",       ["定期壽", "定期保險"]),
    ("終身壽險",       ["終身保險", "終身壽", "終身"]),
]

# ── 幣別 ──────────────────────────────────────────
FOREIGN_KEYWORDS = ["美元", "USD", "外幣", "港幣", "澳幣", "人民幣", "歐元", "英鎊"]


# ── 分類函式 ──────────────────────────────────────

def should_exclude(name: str) -> bool:
    return any(kw in name for kw in EXCLUDE_KEYWORDS)


def detect_contract_type(name: str) -> str:
    return "附約" if any(kw in name for kw in RIDER_KEYWORDS) else "主約"


def detect_product_type(name: str) -> str:
    for ptype, keywords in PRODUCT_TYPE_DETECT:
        if any(kw in name for kw in keywords):
            return ptype
    return "其他"


def detect_currency(name: str) -> str:
    for kw in FOREIGN_KEYWORDS:
        if kw in name:
            if "美元" in name or "USD" in name:
                return "美元"
            if "港幣" in name:
                return "港幣"
            if "澳幣" in name:
                return "澳幣"
            return "外幣"
    return "新台幣"


def detect_status(stop_date: str) -> str:
    return "停售" if stop_date else "現售"


def roc_to_western(roc_date: str) -> str:
    """民國 111/06/30 → 2022-06-30"""
    m = re.match(r"(\d+)/(\d+)/(\d+)", roc_date.strip())
    if m:
        year = int(m.group(1)) + 1911
        return f"{year}-{m.group(2)}-{m.group(3)}"
    return roc_date


def get_version_number(plan_code: str) -> int:
    """planCode 末 3 碼推版次：100→0, 200→1, 300→2"""
    m = re.search(r"(\d{3})$", plan_code)
    if m:
        return max(0, int(m.group(1)) // 100 - 1)
    return 0


def make_version_folder_name(plan_code: str, status: str, currency: str,
                              sale_date: str, stop_date: str) -> str:
    v = get_version_number(plan_code)
    label = "原始版" if v == 0 else f"第{v}次部分變更"
    sale  = roc_to_western(sale_date) if sale_date else "未知"
    name  = f"v{v:02d} {label}（{status}·{currency}）{sale}~"
    if stop_date:
        name += roc_to_western(stop_date)
    return name


def safe_folder_name(name: str, max_len: int = 50) -> str:
    s = re.sub(r'[\\/*?:"<>|]', "", name)
    return s[:max_len] if len(s) > max_len else s


# ── Google Drive ──────────────────────────────────

def connect_drive():
    if not AUTHORIZED_FILE.exists():
        print("❌ 找不到 authorized_user.json，請先執行 push_to_sheets.py 完成 OAuth 授權")
        sys.exit(1)
    creds = Credentials.from_authorized_user_file(str(AUTHORIZED_FILE), DRIVE_SCOPES)
    return build("drive", "v3", credentials=creds)


def get_or_create_folder(drive, name: str, parent_id: str = None) -> str:
    safe = name.replace("'", "\\'")
    q = f"name='{safe}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        q += f" and '{parent_id}' in parents"
    res = drive.files().list(q=q, fields="files(id)").execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]
    meta = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        meta["parents"] = [parent_id]
    return drive.files().create(body=meta, fields="id").execute()["id"]


def file_exists(drive, filename: str, folder_id: str) -> bool:
    safe = filename.replace("'", "\\'")
    q = f"name='{safe}' and '{folder_id}' in parents and trashed=false"
    res = drive.files().list(q=q, fields="files(id)").execute()
    return len(res.get("files", [])) > 0


def upload_pdf(drive, local_path: Path, filename: str, folder_id: str) -> str:
    if file_exists(drive, filename, folder_id):
        print(f"         ⏭️  已存在，略過：{filename}")
        return ""
    media = MediaFileUpload(str(local_path), mimetype="application/pdf", resumable=True)
    f = drive.files().create(
        body={"name": filename, "parents": [folder_id]},
        media_body=media,
        fields="id",
    ).execute()
    print(f"         ✅ 上傳完成：{filename}")
    return f["id"]


def get_version_folder(drive, root_id, company, product_type, contract_type,
                        product_name, version_folder) -> str:
    """建立並回傳版次資料夾：根/公司/險種/主附約/商品名稱/版次"""
    cid  = get_or_create_folder(drive, company,        root_id)
    ptid = get_or_create_folder(drive, product_type,   cid)
    ctid = get_or_create_folder(drive, contract_type,  ptid)
    pnid = get_or_create_folder(drive, product_name,   ctid)
    vid  = get_or_create_folder(drive, version_folder, pnid)
    return vid


# ── TII CAPTCHA ────────────────────────────────────

def solve_captcha(page) -> str:
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
                {"type": "text", "text": "這是圖形驗證碼，只含數字。請只回覆這些數字，不要其他文字。"},
            ],
        }],
    )
    return re.sub(r"[^0-9]", "", resp.content[0].text.strip())


# ── 取 DetailList 所有 PDF ─────────────────────────

def get_all_pdfs(page, product_id: str) -> dict:
    """回傳 {doc_type: url}，例：{"條款": "https://...", "費率": "https://..."}"""
    try:
        page.goto(
            f"{BASE_URL}/DetailList.aspx?productId={product_id}",
            wait_until="networkidle",
            timeout=15000,
        )
        links = page.query_selector_all("a[href*='Open2.ashx']")
        pdfs = {}
        for link in links:
            text = (link.inner_text() or "").strip()
            href = link.get_attribute("href") or ""
            if not href:
                continue
            full_url = f"{BASE_URL}/{href}" if href.startswith("Open2") else href

            doc_label = None
            for suffix, label in DOC_SUFFIX_MAP.items():
                if re.search(rf"{re.escape(suffix)}\.pdf", text, re.IGNORECASE):
                    doc_label = label
                    break

            if doc_label is _SKIP or doc_label is None:
                continue
            pdfs[doc_label] = full_url
    except Exception as e:
        print(f"         ⚠️  DetailList 失敗 ({product_id})：{e}")
        pdfs = {}
    return pdfs


def download_pdf(url: str, dest: Path, cookies: dict) -> bool:
    try:
        req = urllib.request.Request(url)
        req.add_header("Cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()))
        req.add_header("Referer", BASE_URL)
        req.add_header("User-Agent", "Mozilla/5.0")
        with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
            dest.write_bytes(resp.read())
        return True
    except Exception as e:
        print(f"         ⚠️  下載失敗：{e}")
        return False


# ── 主流程 ─────────────────────────────────────────

def scrape_and_upload(company_code: str, limit: int = 0, manual_captcha: str = ""):
    print("🔌 連線 Google Drive...")
    drive   = connect_drive()
    root_id = get_or_create_folder(drive, DRIVE_ROOT_FOLDER)
    print(f"   ✅ Drive 根資料夾：{DRIVE_ROOT_FOLDER}")

    # 本地 registry 記錄已處理項目（避免重跑時重複上傳）
    registry_path = BASE_DIR / "drive_registry.json"
    registry: dict = {}
    if registry_path.exists():
        try:
            registry = json.loads(registry_path.read_text("utf-8"))
        except Exception:
            pass

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=200)
        context = browser.new_context()
        page    = context.new_page()

        # ── 選公司 ────────────────────────────────
        print("🌐 開啟 TII 查詢頁面...")
        page.goto(f"{BASE_URL}/Query.aspx", wait_until="networkidle")
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
            return

        page.select_option("select[name='CompanyID']", target_value)
        print(f"✅ 公司：{company_name}")

        # ── CAPTCHA ────────────────────────────────
        solved = False
        if manual_captcha:
            print(f"🔑 使用手動傳入的 CAPTCHA：{manual_captcha}")
            page.fill("input[name='bmpC']", manual_captcha)
            page.click("input[name='Go222']")
            page.wait_for_load_state("networkidle", timeout=15000)
            if page.query_selector("a[href*='DetailList.aspx']"):
                print("✅ CAPTCHA 正確")
                solved = True
            else:
                print("❌ 手動 CAPTCHA 錯誤，請重新截圖並確認數值")
                browser.close()
                return
        else:
            for attempt in range(3):
                try:
                    captcha_val = solve_captcha(page)
                    print(f"🤖 CAPTCHA：{captcha_val}（第 {attempt+1} 次）")
                    if not captcha_val:
                        raise ValueError("空")
                    page.fill("input[name='bmpC']", captcha_val)
                    page.click("input[name='Go222']")
                    page.wait_for_load_state("networkidle", timeout=15000)
                    if page.query_selector("a[href*='DetailList.aspx']"):
                        print("✅ CAPTCHA 正確")
                        solved = True
                        break
                except Exception as e:
                    print(f"⚠️  CAPTCHA 失敗：{e}")
                if attempt < 2:
                    page.goto(f"{BASE_URL}/Query.aspx", wait_until="networkidle")
                    page.select_option("select[name='categoryId']", "人身保險")
                    page.wait_for_timeout(500)
                    page.select_option("select[name='CompanyID']", target_value)

            if not solved:
                captcha_img    = BASE_DIR / "captcha_live.png"
                captcha_answer = BASE_DIR / "captcha_answer.txt"
                if captcha_answer.exists():
                    captcha_answer.unlink()
                page.locator("img[src*='bmp.ashx']").screenshot(path=str(captcha_img))
                print(f"\n⚠️  CAPTCHA 自動解析失敗")
                print(f"   截圖已存至：{captcha_img}")
                print(f"   請將答案寫入：{captcha_answer}，腳本將自動繼續（最多等 120 秒）")
                for _ in range(240):   # 每 0.5s 檢查，共 120s
                    time.sleep(0.5)
                    if captcha_answer.exists():
                        captcha_val = captcha_answer.read_text("utf-8").strip()
                        captcha_answer.unlink()
                        if captcha_val:
                            page.fill("input[name='bmpC']", captcha_val)
                            page.click("input[name='Go222']")
                            page.wait_for_load_state("networkidle", timeout=15000)
                            if page.query_selector("a[href*='DetailList.aspx']"):
                                print(f"✅ CAPTCHA 正確（{captcha_val}）")
                                solved = True
                            else:
                                print("❌ CAPTCHA 錯誤，請重試")
                                browser.close()
                                return
                        break
                if not solved:
                    print("❌ 等待 CAPTCHA 答案超時")
                    browser.close()
                    return

        # ── 翻頁爬取 ──────────────────────────────
        processed = 0
        page_num  = 1

        while True:
            print(f"\n📄 第 {page_num} 頁...")
            page.wait_for_timeout(600)
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
                    items.push({
                        productName: link.innerText.trim(),
                        productId:   m[1],
                        saleDate:    dates[0] || "",
                        stopDate:    dates[1] || "",
                    });
                });
                return items;
            }""")

            for item in page_items:
                if limit > 0 and processed >= limit:
                    break

                name = item["productName"]
                pid  = item["productId"]

                if should_exclude(name):
                    print(f"   ⏭️  排除：{name}")
                    continue

                # 已處理過則跳過
                if pid in registry:
                    print(f"   ⏭️  已存在 registry，略過：{name}")
                    processed += 1
                    continue

                sale_date     = item["saleDate"]
                stop_date     = item["stopDate"]
                contract_type = detect_contract_type(name)
                product_type  = detect_product_type(name)
                currency      = detect_currency(name)
                status        = detect_status(stop_date)
                version_folder = make_version_folder_name(pid, status, currency, sale_date, stop_date)
                product_folder = safe_folder_name(name)

                print(f"\n  [{processed+1}] {name}")
                print(f"       {contract_type} ｜ {product_type} ｜ {currency} ｜ {status}")
                print(f"       版次：{version_folder}")

                # 取 DetailList 所有 PDF
                cookies = {c["name"]: c["value"] for c in context.cookies()}
                pdfs = get_all_pdfs(page, pid)
                if pdfs:
                    print(f"       文件類型：{', '.join(pdfs.keys())}")
                else:
                    print(f"       ⚠️  找不到任何 PDF，略過")
                    page.goto(results_url, wait_until="networkidle", timeout=15000)
                    processed += 1
                    continue

                # 建 Drive 資料夾層級：根/公司/險種/主附約/商品名稱/版次
                version_id = get_version_folder(
                    drive, root_id, company_name,
                    product_type, contract_type, product_folder, version_folder,
                )

                # 下載 + 上傳每種文件
                cookies = {c["name"]: c["value"] for c in context.cookies()}
                uploaded_docs = []
                for doc_type, pdf_url in pdfs.items():
                    local_path = TMP_DIR / f"{pid}_{doc_type}.pdf"
                    print(f"       ⬇️  下載 {doc_type}...")
                    if download_pdf(pdf_url, local_path, cookies):
                        drive_id = upload_pdf(drive, local_path, f"{doc_type}.pdf", version_id)
                        if drive_id or True:   # 即使已存在也記錄
                            uploaded_docs.append(doc_type)

                # 寫入 registry
                registry[pid] = {
                    "company":       company_name,
                    "productName":   name,
                    "planCode":      pid,
                    "contractType":  contract_type,
                    "productType":   product_type,
                    "currency":      currency,
                    "status":        status,
                    "saleDate":      roc_to_western(sale_date) if sale_date else "",
                    "stopDate":      roc_to_western(stop_date) if stop_date else "",
                    "versionFolder": version_folder,
                    "productFolder": product_folder,
                    "docTypes":      uploaded_docs,
                }
                registry_path.write_text(
                    json.dumps(registry, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )

                page.goto(results_url, wait_until="networkidle", timeout=15000)
                processed += 1

            print(f"   累計 {processed} 筆")

            if limit > 0 and processed >= limit:
                print(f"\n✅ 達到 limit={limit}，停止")
                break

            next_link = page.query_selector(f"a:text('{page_num + 1}')")
            if not next_link:
                next_link = page.query_selector("a:text('[下十頁]')")
            if not next_link:
                print("\n✅ 沒有下一頁，全部爬完")
                break

            next_link.click()
            page_num += 1

        browser.close()

    print(f"\n🎉 完成！共處理 {processed} 筆")
    print(f"   Drive 結構：{DRIVE_ROOT_FOLDER} / {company_name} / 險種 / 主附約 / 商品名稱 / 版次")
    print(f"   Registry：{registry_path}")


def main():
    parser = argparse.ArgumentParser(description="TII 保單文件 → Google Drive 分類爬蟲")
    parser.add_argument("--company", required=True, help="公司代碼，例如 206（南山人壽）")
    parser.add_argument("--limit",   type=int, default=0, help="最多處理幾筆（0=全部）")
    parser.add_argument("--captcha", default="", help="直接傳入已知的 CAPTCHA 值，跳過自動解析")
    args = parser.parse_args()
    scrape_and_upload(args.company, args.limit, args.captcha)


if __name__ == "__main__":
    main()
