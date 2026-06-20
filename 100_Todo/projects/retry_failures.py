#!/usr/bin/env python3
"""重試 scraper_failures_{code}.json 記錄的失敗筆，寫入主 registry。"""
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import scraper_drive as S

BASE = Path(__file__).parent
MAIN = BASE / "drive_registry.json"

COMPANY_CODE = {
    "南山人壽保險股份有限公司": "206",
    "國泰人壽保險股份有限公司": "204",
    "富邦人壽保險股份有限公司": "209",
}

def main(code):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        for l in (BASE.parent.parent/"policy-analyzer"/".env").read_text("utf-8","replace").splitlines():
            if l.startswith("ANTHROPIC_API_KEY="):
                v=l.split("=",1)[1].strip().strip('"').strip("'")
                if v: os.environ["ANTHROPIC_API_KEY"]=v

    fpath = BASE / f"scraper_failures_{code}.json"
    failures = json.loads(fpath.read_text("utf-8"))
    registry = json.loads(MAIN.read_text("utf-8"))
    # 公司名：先查字典，沒有就從 registry 中同代碼前綴的既有記錄取得
    company_name = next((n for n, c in COMPANY_CODE.items() if c == code), "")
    if not company_name:
        company_name = next((v["company"] for k, v in registry.items()
                             if k.startswith(code) and v.get("company")), "")
    if not company_name:
        print(f"❌ 無法判定公司名（code={code}），中止"); return
    print(f"待重試 {len(failures)} 筆（公司：{company_name}）")

    drive = S.connect_drive()
    root_id = S.get_or_create_folder(drive, S.DRIVE_ROOT_FOLDER)

    from playwright.sync_api import sync_playwright
    done, still_fail = 0, {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(); page = ctx.new_page()
        page.goto(f"{S.BASE_URL}/Query.aspx", wait_until="networkidle", timeout=60000)
        opts = page.query_selector_all("select[name='CompanyID'] option")
        target = next((o.get_attribute("value") for o in opts if (o.get_attribute("value") or "").startswith(code)), None)
        if not S._navigate_and_captcha(page, target, S.LIFE_SUB_CATEGORIES[0][0]):
            print("❌ 無法取得 session"); browser.close(); return
        cookies = {c["name"]: c["value"] for c in ctx.cookies()}

        for pid, info in failures.items():
            if pid in registry:
                done += 1; continue
            name = S.unicodedata.normalize("NFC", info["productName"])
            try:
                pdfs = S.get_all_pdfs(pid, cookies, retries=3)
            except S.DetailListError as e:
                still_fail[pid] = info; print(f"  ❌ 仍失敗 {name[:30]}"); continue
            if not pdfs:
                print(f"  ⚠️ 無文件 {name[:30]}"); continue
            ct = S.detect_contract_type(name); pt = S.detect_product_type(name)
            ver = S.get_version_number(name); base = S.safe_folder_name(S.base_product_name(name))
            vf = f"v{ver}"
            vid = S.get_version_folder(drive, root_id, company_name, pt, ct, base, vf)
            uploaded=[]
            for dt,url in pdfs.items():
                lp = S.TMP_DIR / f"{pid}_{dt}.pdf"
                if S.download_pdf(url, lp, cookies):
                    S.upload_pdf(drive, lp, f"{dt}.pdf", vid); uploaded.append(dt)
            registry[pid] = {"company":company_name,"productName":name,"planCode":pid,
                "contractType":ct,"productType":pt,"currency":S.detect_currency(name),
                "status":"","saleDate":"","stopDate":"","versionFolder":vf,
                "productFolder":base,"docTypes":uploaded}
            MAIN.write_text(json.dumps(registry, ensure_ascii=False, indent=2), "utf-8")
            done += 1; print(f"  ✅ 補回 {name[:30]} {vf}")
            time.sleep(0.3)
        browser.close()

    fpath.write_text(json.dumps(still_fail, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n✅ 補回 {done} 筆，仍失敗 {len(still_fail)} 筆")

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv)>1 else "209")
