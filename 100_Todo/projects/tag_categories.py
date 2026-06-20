#!/usr/bin/env python3
"""
回補既有記錄的 TII 險種代碼（categoryCode）：
  重走各公司 6 個子類別的『列表頁』（只讀名單、不下載 PDF），
  依每個 productId 出現在哪個子類別，更新 registry 的 categoryCode/category。

用法： python3 tag_categories.py 206,204,209,216
"""
import json, os, sys, math, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import scraper_drive as S

BASE = Path(__file__).parent
MAIN = BASE / "drive_registry.json"


def get_key():
    if os.environ.get("ANTHROPIC_API_KEY"): return
    for l in (BASE.parent.parent/"policy-analyzer"/".env").read_text("utf-8","replace").splitlines():
        if l.startswith("ANTHROPIC_API_KEY="):
            v=l.split("=",1)[1].strip().strip('"').strip("'")
            if v: os.environ["ANTHROPIC_API_KEY"]=v


def list_page_ids(page):
    return page.evaluate("""() => {
        const ids=[];
        document.querySelectorAll("a[href*='DetailList.aspx']").forEach(a=>{
            const m=(a.getAttribute('href')||'').match(/productId=([^&]+)/);
            if(m) ids.push(m[1]);
        });
        return ids;
    }""")


def main(codes):
    get_key()
    registry = json.loads(MAIN.read_text("utf-8"))
    from playwright.sync_api import sync_playwright

    updated = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(); page = ctx.new_page()
        page.goto(f"{S.BASE_URL}/Query.aspx", wait_until="networkidle", timeout=60000)
        opts = page.query_selector_all("select[name='CompanyID'] option")
        code2val = {}
        for o in opts:
            val=o.get_attribute("value") or ""
            for c in codes:
                if val.startswith(c): code2val[c]=val

        for code in codes:
            target = code2val.get(code)
            if not target:
                print(f"❌ 找不到公司 {code}"); continue
            for sub_val, sub_label in S.LIFE_SUB_CATEGORIES:
                print(f"\n=== 公司 {code} / {sub_label}({sub_val}) ===", flush=True)
                if not S._navigate_and_captcha(page, target, sub_val):
                    print("  ❌ CAPTCHA 失敗，跳過"); continue
                page_size=50
                try:
                    page.select_option("select[name='PageCrt']","50")
                    page.wait_for_load_state("networkidle",timeout=15000); page.wait_for_timeout(400)
                except Exception: page_size=10
                txt=page.inner_text("body"); import re
                m=re.search(r"找到\s*(\d+)\s*筆",txt)
                total=int(m.group(1)) if m else -1
                last=max(1,math.ceil(total/page_size)) if total>0 else None
                pnum=1; seen=0; subupd=0
                while True:
                    ids=list_page_ids(page); seen+=len(ids)
                    for pid in ids:
                        if pid in registry and registry[pid].get("categoryCode")!=sub_val:
                            registry[pid]["categoryCode"]=sub_val
                            registry[pid]["category"]=sub_label
                            subupd+=1; updated+=1
                    if last and pnum>=last: break
                    if not last and not page.query_selector(f"a[href*='page={pnum+1}']"): break
                    pnum+=1; ok=False
                    for _ in range(4):
                        try:
                            page.goto(f"{S.BASE_URL}/ResultQueryAll.aspx?page={pnum}",wait_until="networkidle",timeout=30000)
                            if page.query_selector("a[href*='DetailList.aspx']"): ok=True; break
                            raise RuntimeError("空頁")
                        except Exception:
                            if S._navigate_and_captcha(page,target,sub_val):
                                try:
                                    page.select_option("select[name='PageCrt']","50")
                                    page.wait_for_load_state("networkidle",timeout=15000); page.wait_for_timeout(400)
                                except Exception: pass
                            time.sleep(2)
                    if not ok: print(f"  ⚠️ 第{pnum}頁失敗，停此子類別"); break
                MAIN.write_text(json.dumps(registry,ensure_ascii=False,indent=2),"utf-8")
                print(f"  標記 {subupd} 筆（看過 {seen}/{total} 列）", flush=True)
        browser.close()
    print(f"\n🏁 共標記/更新 {updated} 筆 categoryCode")
    # 統計
    nocat=sum(1 for v in registry.values() if not v.get("categoryCode"))
    print(f"   仍無 categoryCode：{nocat} 筆")


if __name__=="__main__":
    codes=(sys.argv[1] if len(sys.argv)>1 else "206,204,209,216").split(",")
    main([c.strip() for c in codes if c.strip()])
