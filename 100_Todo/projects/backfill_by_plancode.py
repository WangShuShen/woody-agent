#!/usr/bin/env python3
"""
直接以 planCode 補抓（不靠列表分頁，避開分頁提早中止的漏抓）：
  1. 對每個版次缺口，從同組既有版次構造候選 planCode
  2. 打 TII DetailList 探測，有 PDF → 真漏，直接下載上傳並補進 registry
  3. 空 → TII 本身無此版，略過

涵蓋構造得出的所有缺口（單一鄰居即可推，多寬度嘗試）。
"""
import json
import os
import re
import sys
import time
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))
import scraper_drive as S

BASE = Path(__file__).parent
REGISTRY = BASE / "drive_registry.json"

COMPANY_CODE = {
    "南山人壽保險股份有限公司": "206",
    "國泰人壽保險股份有限公司": "204",
}


def candidates_for(present: dict, miss: int) -> list:
    """從同組既有版次 {ver:code} 構造缺漏版次 miss 的候選 planCode（多個、去重）"""
    out = []
    seen = set()
    for ver, code in present.items():
        for W in range(1, 8):
            if len(code) < W:
                continue
            tail = code[-W:]
            if not tail.isdigit():
                continue
            if int(tail) != ver:
                continue
            if miss >= 10 ** W:
                continue
            cand = code[:-W] + str(miss).zfill(W)
            if cand not in seen and cand != code:
                seen.add(cand)
                out.append(cand)
    return out


def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        envp = BASE.parent.parent / "policy-analyzer" / ".env"
        if envp.exists():
            for line in envp.read_text("utf-8", errors="replace").splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    v = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if v:
                        os.environ["ANTHROPIC_API_KEY"] = v

    registry = json.loads(REGISTRY.read_text("utf-8"))

    # 分組（含每組的中繼資料樣本）
    groups = defaultdict(dict)
    meta = {}
    for k, v in registry.items():
        m = re.match(r"v(\d+)$", v["versionFolder"])
        if m:
            groups[(v["company"], v["productFolder"])][int(m.group(1))] = k
            meta[(v["company"], v["productFolder"])] = v

    # 收集每間公司的缺口候選
    by_company = defaultdict(list)
    for (company, pf), vmap in groups.items():
        mx = max(vmap)
        for miss in range(mx + 1):
            if miss in vmap:
                continue
            cands = candidates_for(vmap, miss)
            cands = [c for c in cands if c not in registry]
            if cands:
                by_company[company].append({
                    "pf": pf, "version": miss, "cands": cands,
                    "meta": meta[(company, pf)],
                })

    for co, items in by_company.items():
        print(f"{co}: {len(items)} 個可探測缺口", flush=True)

    drive = S.connect_drive()
    root_id = S.get_or_create_folder(drive, S.DRIVE_ROOT_FOLDER)

    from playwright.sync_api import sync_playwright
    added = 0
    real_miss = []
    genuine = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(f"{S.BASE_URL}/Query.aspx", wait_until="networkidle", timeout=60000)

        for company, items in by_company.items():
            code = COMPANY_CODE.get(company)
            if not code:
                continue
            # 取得 session
            opts = page.query_selector_all("select[name='CompanyID'] option")
            target = next((o.get_attribute("value") for o in opts
                           if (o.get_attribute("value") or "").startswith(code)), None)
            if not S._navigate_and_captcha(page, target, S.LIFE_SUB_CATEGORIES[0][0]):
                print(f"   ❌ {company} 無法取得 session", flush=True)
                continue
            cookies = {c["name"]: c["value"] for c in context.cookies()}

            print(f"\n{'='*55}\n🔍 {company}：探測 {len(items)} 個缺口\n{'='*55}", flush=True)

            for it in items:
                hit = None
                hit_pdfs = None
                for cand in it["cands"]:
                    try:
                        pdfs = S.get_all_pdfs(cand, cookies, retries=2)
                    except S.DetailListError:
                        continue
                    if pdfs:
                        hit, hit_pdfs = cand, pdfs
                        break
                    time.sleep(0.3)

                if not hit:
                    genuine += 1
                    continue

                # 真漏 → 補抓
                base = it["pf"]
                ver = it["version"]
                mv = it["meta"]
                vfolder = f"v{ver}"
                pname = base if ver == 0 else f"{base}(第{ver}次部分變更)"
                print(f"   ⚠️  補抓 {base[:28]} v{ver} ({hit}) → {list(hit_pdfs.keys())}", flush=True)

                version_id = S.get_version_folder(
                    drive, root_id, company,
                    mv["productType"], mv["contractType"], base, vfolder,
                )
                uploaded = []
                for doc_type, url in hit_pdfs.items():
                    lp = S.TMP_DIR / f"{hit}_{doc_type}.pdf"
                    if S.download_pdf(url, lp, cookies):
                        S.upload_pdf(drive, lp, f"{doc_type}.pdf", version_id)
                        uploaded.append(doc_type)

                registry[hit] = {
                    "company": company, "productName": pname, "planCode": hit,
                    "contractType": mv["contractType"], "productType": mv["productType"],
                    "currency": mv["currency"], "status": mv.get("status", ""),
                    "saleDate": "", "stopDate": "",
                    "versionFolder": vfolder, "productFolder": base,
                    "docTypes": uploaded,
                }
                REGISTRY.write_text(json.dumps(registry, ensure_ascii=False, indent=2), "utf-8")
                added += 1
                real_miss.append({"planCode": hit, "productFolder": base, "version": ver})

        browser.close()

    print(f"\n{'='*55}")
    print(f"🏁 直接補抓完成")
    print(f"   ⚠️  真漏並已補抓：{added} 個")
    print(f"   ✅ 確認 TII 無此版：{genuine} 個")
    print(f"   registry 現有：{len(registry)} 筆")
    if real_miss:
        (BASE / "backfill_plancode_added.json").write_text(
            json.dumps(real_miss, ensure_ascii=False, indent=2), "utf-8")


if __name__ == "__main__":
    main()
