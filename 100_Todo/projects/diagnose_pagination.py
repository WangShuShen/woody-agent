#!/usr/bin/env python3
"""診斷 TII 投資型壽險的分頁機制與每頁筆數選項"""
import os, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import scraper_drive as S

BASE = Path(__file__).parent
if not os.environ.get("ANTHROPIC_API_KEY"):
    envp = BASE.parent.parent / "policy-analyzer" / ".env"
    for line in envp.read_text("utf-8", errors="replace").splitlines():
        if line.startswith("ANTHROPIC_API_KEY="):
            v = line.split("=", 1)[1].strip().strip('"').strip("'")
            if v: os.environ["ANTHROPIC_API_KEY"] = v

from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto(f"{S.BASE_URL}/Query.aspx", wait_until="networkidle", timeout=60000)
    opts = page.query_selector_all("select[name='CompanyID'] option")
    target = next((o.get_attribute("value") for o in opts
                   if (o.get_attribute("value") or "").startswith("204")), None)
    # 國泰 投資型壽險 = 2_5
    S._navigate_and_captcha(page, target, "2_5")
    page.wait_for_timeout(1000)

    txt = page.inner_text("body")
    m = re.search(r"找到\s*(\d+)\s*筆", txt)
    print("總筆數:", m.group(1) if m else "?")

    # 1) 每頁筆數下拉
    print("\n=== 每頁筆數下拉 (select) ===")
    selects = page.evaluate("""() => {
        return [...document.querySelectorAll('select')].map(s => ({
            name: s.name, id: s.id,
            options: [...s.options].map(o => o.value + ':' + o.text)
        }));
    }""")
    for s in selects:
        print(s)

    # 2) 分頁連結
    print("\n=== 分頁連結 (含 Page/doPostBack 的 a) ===")
    links = page.evaluate("""() => {
        return [...document.querySelectorAll('a')].filter(a => {
            const h = a.getAttribute('href')||'';
            return h.includes('Page') || h.includes('doPostBack') || /^\\d+$/.test(a.innerText.trim()) || a.innerText.includes('頁');
        }).slice(0,25).map(a => ({text: a.innerText.trim(), href: a.getAttribute('href')}));
    }""")
    for l in links:
        print(l)

    # 3) 試著點第 2 頁，看 URL/內容是否變
    print("\n=== 嘗試點「2」前後比較 ===")
    first_ids = page.evaluate("""() => [...document.querySelectorAll("a[href*='DetailList.aspx']")].slice(0,3).map(a=>a.href)""")
    print("點前前3筆:", first_ids)
    try:
        page.click("a:text('2')", timeout=5000)
        page.wait_for_load_state("networkidle", timeout=20000)
        page.wait_for_timeout(1500)
        after_ids = page.evaluate("""() => [...document.querySelectorAll("a[href*='DetailList.aspx']")].slice(0,3).map(a=>a.href)""")
        print("點後前3筆:", after_ids)
        print("有變化?", first_ids != after_ids)
    except Exception as e:
        print("點擊失敗:", e)

    input_path = BASE / "diag_done.txt"
    print(f"\n診斷完成。瀏覽器保持開啟 20 秒供觀察...")
    page.wait_for_timeout(20000)
    browser.close()
