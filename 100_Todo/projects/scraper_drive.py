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
import math
import base64
import ssl
import unicodedata
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

AUTHORIZED_FILE      = Path(__file__).parent.parent.parent / "000_Agent" / "authorized_user.json"
CREDENTIALS_FILE     = Path(__file__).parent.parent.parent / "000_Agent" / "google_credentials.json"

DRIVE_SCOPES = [
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


_CH_NUMS = {
    '一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10,
    '十一':11,'十二':12,'十三':13,'十四':14,'十五':15,'十六':16,'十七':17,
    '十八':18,'十九':19,'二十':20,
}
# 匹配各種版次變體：部分/部份、全形/半形括號、中文/阿拉伯數字、有無「次」
_VER_RE = re.compile(r'[\(（]第([一二三四五六七八九十\d]+)次?部[分份]變更[\)）]')


def get_version_number(product_name: str) -> int:
    """從商品名稱取版次，支援阿拉伯/中文數字、全形括號、缺「次」"""
    m = _VER_RE.search(product_name)
    if not m:
        return 0
    token = m.group(1)
    return int(token) if token.isdigit() else _CH_NUMS.get(token, 0)


def base_product_name(product_name: str) -> str:
    """去除版次後綴，支援全形括號、中文數字、缺「次」"""
    return _VER_RE.sub('', product_name).strip()


def safe_folder_name(name: str, max_len: int = 50) -> str:
    s = unicodedata.normalize("NFC", name)
    s = re.sub(r'[\\/*?:"<>|]', "", s)
    return s[:max_len] if len(s) > max_len else s


# ── Google Drive ──────────────────────────────────

def connect_drive():
    if not AUTHORIZED_FILE.exists():
        print("❌ 找不到 authorized_user.json，請先執行 OAuth 授權")
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
    for attempt in range(1, 4):
        try:
            media = MediaFileUpload(str(local_path), mimetype="application/pdf", resumable=True)
            f = drive.files().create(
                body={"name": filename, "parents": [folder_id]},
                media_body=media,
                fields="id",
            ).execute()
            print(f"         ✅ 上傳完成：{filename}")
            return f["id"]
        except Exception as e:
            if attempt < 3:
                print(f"         ⚠️  上傳失敗（第{attempt}次），5秒後重試：{e}")
                time.sleep(5)
            else:
                print(f"         ❌ 上傳失敗（放棄）：{e}")
    return ""


def get_version_folder(drive, root_id, company, product_type, contract_type,
                        product_name, version_folder) -> str:
    """建立並回傳版次資料夾：根/公司/險種/主附約/基本商品名稱/vN"""
    cid  = get_or_create_folder(drive, company,        root_id)
    ptid = get_or_create_folder(drive, product_type,   cid)
    ctid = get_or_create_folder(drive, contract_type,  ptid)
    pnid = get_or_create_folder(drive, product_name,   ctid)  # 已是基本名稱
    vid  = get_or_create_folder(drive, version_folder, pnid)  # v0 / v1 / v2
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

class DetailListError(Exception):
    """DetailList 網路抓取失敗（需重試，不可當成『無文件』）"""


def get_all_pdfs(product_id: str, cookies: dict, retries: int = 3) -> dict:
    """回傳 {doc_type: url}，用 HTTP 抓 DetailList（不動 Playwright browser）。

    成功載入但無符合文件 → 回傳 {}（合理，該筆真的沒 PDF）。
    網路/逾時失敗 → 重試 retries 次後仍失敗則 raise DetailListError，
    讓呼叫端記錄並改日重試，避免靜默丟筆。
    """
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            url = f"{BASE_URL}/DetailList.aspx?productId={product_id}"
            req = urllib.request.Request(url)
            req.add_header("Cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()))
            req.add_header("Referer", BASE_URL)
            req.add_header("User-Agent", "Mozilla/5.0")
            with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
                html = resp.read().decode("utf-8", errors="replace")

            # 找所有 <a href="Open2.ashx?id=...">filename.pdf</a>
            pattern = re.compile(
                r'href=["\']?(Open2\.ashx\?[^"\'>\s]+)["\']?[^>]*>\s*([^<]+\.pdf)\s*</a>',
                re.IGNORECASE,
            )
            pdfs = {}
            for href, text in pattern.findall(html):
                text = text.strip()
                full_url = f"{BASE_URL}/{href}"
                doc_label = None
                for suffix, label in DOC_SUFFIX_MAP.items():
                    if re.search(rf"{re.escape(suffix)}\.pdf", text, re.IGNORECASE):
                        doc_label = label
                        break
                if doc_label is _SKIP or doc_label is None:
                    continue
                pdfs[doc_label] = full_url
            return pdfs
        except Exception as e:
            last_err = e
            if attempt < retries:
                print(f"         ⚠️  DetailList 失敗（第{attempt}次）{product_id}，5秒後重試：{e}")
                time.sleep(5)
    raise DetailListError(f"{product_id}: {last_err}")


def download_pdf(url: str, dest: Path, cookies: dict) -> bool:
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(url)
            req.add_header("Cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()))
            req.add_header("Referer", BASE_URL)
            req.add_header("User-Agent", "Mozilla/5.0")
            with urllib.request.urlopen(req, timeout=120, context=_SSL_CTX) as resp:
                dest.write_bytes(resp.read())
            return True
        except Exception as e:
            if attempt < 3:
                print(f"         ⚠️  下載失敗（第{attempt}次），5秒後重試：{e}")
                time.sleep(5)
            else:
                print(f"         ❌ 下載失敗（放棄）：{e}")
    return False


# ── 主流程 ─────────────────────────────────────────

# 人身保險子類別（空選會漏掉大量資料，需逐一查詢）
LIFE_SUB_CATEGORIES = [
    ("2_1", "傷害保險"),
    ("2_2", "健康保險"),
    ("2_3", "傳統型壽險"),
    ("2_4", "傳統型年金"),
    ("2_5", "投資型壽險"),
    ("2_6", "投資型年金"),
]


def _subcat_progress_path(company_code: str) -> Path:
    return BASE_DIR / f"scraper_subcat_{company_code}.json"


def _load_done_subcats(company_code: str) -> set:
    p = _subcat_progress_path(company_code)
    if p.exists():
        return set(json.loads(p.read_text("utf-8")))
    return set()


def _mark_subcat_done(company_code: str, sub_val: str):
    p = _subcat_progress_path(company_code)
    done = _load_done_subcats(company_code)
    done.add(sub_val)
    p.write_text(json.dumps(sorted(done), ensure_ascii=False))


def _failures_path(company_code: str) -> Path:
    return BASE_DIR / f"scraper_failures_{company_code}.json"


def _log_failure(company_code: str, pid: str, name: str, reason: str):
    """記錄抓取失敗的單筆，供日後重試（不靜默丟掉）"""
    p = _failures_path(company_code)
    data = {}
    if p.exists():
        try:
            data = json.loads(p.read_text("utf-8"))
        except Exception:
            data = {}
    data[pid] = {"productName": name, "reason": reason}
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")


def _navigate_and_captcha(page, target_value: str, sub_cat: str) -> bool:
    """導航到 TII 查詢頁、選公司+子類別、解 CAPTCHA，回傳是否成功"""
    page.goto(f"{BASE_URL}/Query.aspx", wait_until="networkidle", timeout=60000)
    page.select_option("select[name='categoryId']", "人身保險")
    page.wait_for_timeout(500)
    page.select_option("select[name='CompanyID']", target_value)
    page.wait_for_timeout(300)
    if sub_cat:
        page.select_option("select[name='f_CategoryId1']", sub_cat)

    for attempt in range(3):
        try:
            captcha_val = solve_captcha(page)
            print(f"   🤖 CAPTCHA：{captcha_val}（第 {attempt+1} 次）")
            if not captcha_val:
                raise ValueError("空")
            page.fill("input[name='bmpC']", captcha_val)
            page.click("input[name='Go222']")
            page.wait_for_load_state("networkidle", timeout=15000)
            if page.query_selector("a[href*='DetailList.aspx']") or "查無" in page.content():
                print(f"   ✅ CAPTCHA 正確")
                return True
        except Exception as e:
            print(f"   ⚠️  CAPTCHA 失敗：{e}")
        # 重試：重新導航
        page.goto(f"{BASE_URL}/Query.aspx", wait_until="networkidle")
        page.select_option("select[name='categoryId']", "人身保險")
        page.wait_for_timeout(500)
        page.select_option("select[name='CompanyID']", target_value)
        page.wait_for_timeout(300)
        if sub_cat:
            page.select_option("select[name='f_CategoryId1']", sub_cat)

    # 自動解析失敗，等人工輸入
    captcha_img    = BASE_DIR / "captcha_live.png"
    captcha_answer = BASE_DIR / "captcha_answer.txt"
    if captcha_answer.exists():
        captcha_answer.unlink()
    page.locator("img[src*='bmp.ashx']").screenshot(path=str(captcha_img))
    print(f"\n   ⚠️  CAPTCHA 自動解析失敗，截圖：{captcha_img}")
    print(f"   請將答案寫入 {captcha_answer}（最多等 120 秒）")
    for _ in range(240):
        time.sleep(0.5)
        if captcha_answer.exists():
            captcha_val = captcha_answer.read_text("utf-8").strip()
            captcha_answer.unlink()
            if captcha_val:
                page.fill("input[name='bmpC']", captcha_val)
                page.click("input[name='Go222']")
                page.wait_for_load_state("networkidle", timeout=15000)
                if page.query_selector("a[href*='DetailList.aspx']"):
                    print(f"   ✅ CAPTCHA 正確（{captcha_val}）")
                    return True
                print("   ❌ CAPTCHA 錯誤")
                return False
            break
    print("   ❌ 等待 CAPTCHA 超時")
    return False


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

        # ── 查公司名稱 ────────────────────────────
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

        print(f"✅ 公司：{company_name}")

        # ── 逐一子類別爬取 ────────────────────────
        processed = 0
        done_subcats = _load_done_subcats(company_code)
        if done_subcats:
            print(f"   已完成子類別：{', '.join(sorted(done_subcats))}，直接跳過")

        for sub_val, sub_label in LIFE_SUB_CATEGORIES:
            print(f"\n{'='*50}")
            print(f"📂 子類別：{sub_label}（{sub_val}）")

            if sub_val in done_subcats:
                print(f"   ⏭️  已完成，跳過")
                continue

            if manual_captcha:
                page.goto(f"{BASE_URL}/Query.aspx", wait_until="networkidle")
                page.select_option("select[name='categoryId']", "人身保險")
                page.wait_for_timeout(500)
                page.select_option("select[name='CompanyID']", target_value)
                page.wait_for_timeout(300)
                page.select_option("select[name='f_CategoryId1']", sub_val)
                page.fill("input[name='bmpC']", manual_captcha)
                page.click("input[name='Go222']")
                page.wait_for_load_state("networkidle", timeout=15000)
                if not page.query_selector("a[href*='DetailList.aspx']"):
                    print(f"   ❌ 手動 CAPTCHA 在 {sub_label} 失敗，跳過")
                    continue
            else:
                if not _navigate_and_captcha(page, target_value, sub_val):
                    print(f"   ⚠️  {sub_label} CAPTCHA 失敗，跳過此子類別")
                    continue

            # ── 每頁顯示改 50 筆（減少頁數、降低翻頁風險）─────
            page_size = 50
            try:
                page.select_option("select[name='PageCrt']", "50")
                page.wait_for_load_state("networkidle", timeout=15000)
                page.wait_for_timeout(500)
            except Exception as _e:
                page_size = 10
                print(f"   ⚠️  設定每頁 50 筆失敗，沿用預設：{_e}")

            # ── 翻頁爬取（同一子類別內）──────────────
            page_num = 1
            sub_done = False
            subcat_had_failure = False   # 有任何單筆網路失敗就不標記子類別完成
            rows_seen = 0                 # 已看過的列數（用以對照 TII 宣告總數）

            # 取得 TII 宣告的總筆數（「找到 N 筆」），作為分頁完整性依據
            try:
                _txt = page.inner_text("body")
                _m = re.search(r"找到\s*(\d+)\s*筆", _txt)
                total_count = int(_m.group(1)) if _m else -1
            except Exception:
                total_count = -1
            last_page = max(1, math.ceil(total_count / page_size)) if total_count > 0 else None
            print(f"   TII 宣告總筆數：{total_count if total_count >= 0 else '未知'}"
                  f"（每頁 {page_size}，共 {last_page if last_page else '?'} 頁）")

            while True:
                print(f"\n📄 [{sub_label}] 第 {page_num} 頁...")
                page.wait_for_timeout(600)

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
                        sub_done = True
                        break

                    name = unicodedata.normalize("NFC", item["productName"])
                    pid  = item["productId"]

                    if should_exclude(name):
                        print(f"   ⏭️  排除：{name}")
                        continue

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
                    ver            = get_version_number(name)
                    version_folder = f"v{ver}"
                    product_folder = safe_folder_name(base_product_name(name))

                    print(f"\n  [{processed+1}] {name}")
                    print(f"       {contract_type} ｜ {product_type} ｜ {currency} ｜ {status}")
                    print(f"       版次：{version_folder}")

                    cookies = {c["name"]: c["value"] for c in context.cookies()}
                    try:
                        pdfs = get_all_pdfs(pid, cookies)
                    except DetailListError as e:
                        # 網路失敗：記錄下來改日重試，不要靜默丟掉，也標記此子類別未完成
                        print(f"       ❌ DetailList 連續失敗，記錄待重試：{e}")
                        _log_failure(company_code, pid, name, f"DetailList: {e}")
                        subcat_had_failure = True
                        continue
                    if pdfs:
                        print(f"       文件類型：{', '.join(pdfs.keys())}")
                    else:
                        print(f"       ⚠️  此筆確實無符合文件（DetailList 已載入），略過")
                        processed += 1
                        continue

                    version_id = get_version_folder(
                        drive, root_id, company_name,
                        product_type, contract_type, product_folder, version_folder,
                    )

                    uploaded_docs = []
                    for doc_type, pdf_url in pdfs.items():
                        local_path = TMP_DIR / f"{pid}_{doc_type}.pdf"
                        print(f"       ⬇️  下載 {doc_type}...")
                        if download_pdf(pdf_url, local_path, cookies):
                            drive_id = upload_pdf(drive, local_path, f"{doc_type}.pdf", version_id)
                            if drive_id or True:
                                uploaded_docs.append(doc_type)

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
                    processed += 1

                rows_seen += len(page_items)
                print(f"   累計 {processed} 筆（已看 {rows_seen}/{total_count if total_count>=0 else '?'} 列）")

                if sub_done or (limit > 0 and processed >= limit):
                    print(f"\n✅ 達到 limit={limit}，停止")
                    break

                # 已到最後一頁？
                if last_page and page_num >= last_page:
                    if total_count >= 0 and rows_seen < total_count:
                        print(f"\n❌ [{sub_label}] 列數不足！{rows_seen}/{total_count}，標記未完成下次重掃")
                        subcat_had_failure = True
                    else:
                        print(f"\n✅ [{sub_label}] 全部爬完（{rows_seen}/{total_count} 列）")
                    break
                if not last_page:
                    # 不知總頁數（無宣告總數）：退回偵測下一頁連結
                    nl = page.query_selector(f"a[href*='page={page_num+1}']")
                    if not nl:
                        print(f"\n✅ [{sub_label}] 全部爬完（{rows_seen} 列）")
                        break

                # 直接以網址跳下一頁（TII 分頁為 GET：ResultQueryAll.aspx?page=N）
                page_num += 1
                _ok = False
                for _attempt in range(3):
                    try:
                        page.goto(f"{BASE_URL}/ResultQueryAll.aspx?page={page_num}",
                                  wait_until="networkidle", timeout=30000)
                        _ok = True
                        break
                    except Exception as _e:
                        print(f"   ⚠️  跳頁 {page_num} 失敗，重試：{_e}")
                        time.sleep(5)
                if not _ok:
                    print(f"\n❌ [{sub_label}] 跳頁 {page_num} 連續失敗，標記未完成下次重掃")
                    subcat_had_failure = True
                    break

            # 子類別全部完成（非 limit 中斷、且無任何單筆失敗）才記錄，避免重啟重複掃
            if not sub_done and not (limit > 0 and processed >= limit):
                if subcat_had_failure:
                    print(f"   ⚠️  {sub_label} 有單筆失敗，不標記完成（下次會重掃補抓）")
                else:
                    _mark_subcat_done(company_code, sub_val)
                    print(f"   💾 {sub_label} 完成，已記錄")

            if sub_done or (limit > 0 and processed >= limit):
                break

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
