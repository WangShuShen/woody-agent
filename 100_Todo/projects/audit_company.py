#!/usr/bin/env python3
"""稽核某公司:重走 TII 列表,找出「TII 現在有、registry 沒有」的漏抓(排除團體/產險)。"""
import json, os, sys, math, time, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import scraper_drive as S

BASE=Path(__file__).parent
MAIN=BASE/"drive_registry.json"

def get_key():
    if os.environ.get("ANTHROPIC_API_KEY"): return
    for l in (BASE.parent.parent/"policy-analyzer"/".env").read_text("utf-8","replace").splitlines():
        if l.startswith("ANTHROPIC_API_KEY="):
            v=l.split("=",1)[1].strip().strip('"').strip("'")
            if v: os.environ["ANTHROPIC_API_KEY"]=v

def page_items(page):
    return page.evaluate("""() => {
        const out=[];
        document.querySelectorAll("a[href*='DetailList.aspx']").forEach(a=>{
            const m=(a.getAttribute('href')||'').match(/productId=([^&]+)/);
            if(m) out.push({id:m[1], name:a.innerText.trim()});
        });
        return out;
    }""")

def main(code):
    get_key()
    registry=json.loads(MAIN.read_text("utf-8"))
    from playwright.sync_api import sync_playwright
    tii_all={}; missing={}
    with sync_playwright() as p:
        b=p.chromium.launch(headless=False); ctx=b.new_context(); pg=ctx.new_page()
        pg.goto(f"{S.BASE_URL}/Query.aspx",wait_until="networkidle",timeout=60000)
        opts=pg.query_selector_all("select[name='CompanyID'] option")
        target=next((o.get_attribute("value") for o in opts if (o.get_attribute("value") or "").startswith(code)),None)
        for sub_val,sub_label in S.LIFE_SUB_CATEGORIES:
            if not S._navigate_and_captcha(pg,target,sub_val):
                print(f"❌ {sub_label} CAPTCHA 失敗"); continue
            ps=50
            try:
                pg.select_option("select[name='PageCrt']","50"); pg.wait_for_load_state("networkidle",timeout=15000); pg.wait_for_timeout(400)
            except: ps=10
            m=re.search(r"找到\s*(\d+)\s*筆",pg.inner_text("body")); total=int(m.group(1)) if m else -1
            last=max(1,math.ceil(total/ps)) if total>0 else None
            pn=1; seen=0
            while True:
                for it in page_items(pg):
                    seen+=1; tii_all[it['id']]=it['name']
                    if not S.should_exclude(it['name']) and it['id'] not in registry:
                        missing[it['id']]=(sub_label,it['name'])
                if last and pn>=last: break
                if not last and not pg.query_selector(f"a[href*='page={pn+1}']"): break
                pn+=1; ok=False
                for _ in range(4):
                    try:
                        pg.goto(f"{S.BASE_URL}/ResultQueryAll.aspx?page={pn}",wait_until="networkidle",timeout=30000)
                        if pg.query_selector("a[href*='DetailList.aspx']"): ok=True;break
                        raise RuntimeError("空")
                    except:
                        if S._navigate_and_captcha(pg,target,sub_val):
                            try: pg.select_option("select[name='PageCrt']","50"); pg.wait_for_load_state("networkidle",timeout=15000); pg.wait_for_timeout(400)
                            except: pass
                        time.sleep(2)
                if not ok: print(f"  ⚠️ {sub_label} 第{pn}頁失敗,停"); break
            print(f"  {sub_label}: TII列出 {seen} 列",flush=True)
        b.close()
    print(f"\n=== 稽核結果 (公司 {code}) ===")
    print(f"TII 現有(去重)productId: {len(tii_all)}")
    print(f"⚠️ TII 有、registry 沒有(排除團體/產險後): {len(missing)} 筆")
    for pid,(cat,name) in list(missing.items())[:40]:
        print(f"   [{cat}] {name[:42]} ({pid})")
    Path(BASE/f"audit_missing_{code}.json").write_text(
        json.dumps({k:{"category":v[0],"productName":v[1]} for k,v in missing.items()},ensure_ascii=False,indent=2),"utf-8")
    print(f"\n清單存 audit_missing_{code}.json")

if __name__=="__main__":
    main(sys.argv[1] if len(sys.argv)>1 else "216")
