#!/usr/bin/env python3
"""
缺口抽樣驗證：
  對「能從相鄰版次推算 planCode」的缺口，構造缺漏版次的 planCode，
  直接打 TII DetailList 確認該版次到底存不存在。

  有 PDF  → 真的漏抓（TII 有、我們沒有）
  空/404 → TII 本身就沒登記這版（合理跳號）
"""
import json
import os
import re
import sys
import time
from pathlib import Path
from collections import defaultdict

# 重用 scraper 既有功能
sys.path.insert(0, str(Path(__file__).parent))
import scraper_drive as S

BASE = Path(__file__).parent
REGISTRY = BASE / "drive_registry.json"
SAMPLE_PER_COMPANY = 40   # 每間公司抽幾個缺口驗證

COMPANY_CODE = {
    "南山人壽保險股份有限公司": "206",
    "國泰人壽保險股份有限公司": "204",
}


def construct_plancode(present: dict, miss: int):
    """present: {ver:planCode}。回傳缺漏版次 miss 的推算 planCode 或 None。"""
    codes = list(present.items())
    # 找兩個同長度、能對應出版次編碼位置的鄰居
    for i in range(len(codes)):
        for j in range(len(codes)):
            if i == j:
                continue
            a, ca = codes[i]
            b, cb = codes[j]
            if len(ca) != len(cb):
                continue
            # 共同前綴長度
            cp = 0
            while cp < len(ca) and ca[cp] == cb[cp]:
                cp += 1
            sa, sb = ca[cp:], cb[cp:]
            if not sa or not sb or len(sa) != len(sb):
                continue
            if not (sa.isdigit() and sb.isdigit()):
                continue
            if int(sa) == a and int(sb) == b:
                W = len(sa)
                if miss < 10 ** W:
                    return ca[:cp] + str(miss).zfill(W)
    return None


def collect_candidates():
    r = json.loads(REGISTRY.read_text("utf-8"))
    groups = defaultdict(dict)   # (company, productFolder) -> {ver: planCode}
    for k, v in r.items():
        m = re.match(r"v(\d+)$", v["versionFolder"])
        if m:
            groups[(v["company"], v["productFolder"])][int(m.group(1))] = k

    by_company = defaultdict(list)
    for (company, pf), vmap in groups.items():
        mx = max(vmap)
        for miss in range(mx + 1):
            if miss in vmap:
                continue
            cand = construct_plancode(vmap, miss)
            if cand and cand not in r:   # 推算出且尚未在 registry
                by_company[company].append({
                    "productFolder": pf, "version": miss, "candidate": cand,
                })
    return by_company


def get_session_cookies(page, context, company_code):
    """導航 + 解 CAPTCHA，取得有效 session cookies"""
    company_options = page.query_selector_all("select[name='CompanyID'] option")
    target = None
    for opt in company_options:
        val = opt.get_attribute("value") or ""
        if val.startswith(company_code):
            target = val
            break
    if not target:
        # 需先 goto
        pass
    ok = S._navigate_and_captcha(page, target, S.LIFE_SUB_CATEGORIES[0][0])
    if not ok:
        return None
    return {c["name"]: c["value"] for c in context.cookies()}


def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        # 從 policy-analyzer/.env 補上
        envp = BASE.parent.parent / "policy-analyzer" / ".env"
        if envp.exists():
            for line in envp.read_text("utf-8", errors="replace").splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        os.environ["ANTHROPIC_API_KEY"] = val

    print("📊 收集可推算的缺口候選...")
    by_company = collect_candidates()
    for co, items in by_company.items():
        print(f"   {co}: {len(items)} 個可推算缺口")

    from playwright.sync_api import sync_playwright

    results = {"real_miss": [], "genuine_gap": [], "uncertain": []}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(f"{S.BASE_URL}/Query.aspx", wait_until="networkidle", timeout=60000)

        for company, items in by_company.items():
            code = COMPANY_CODE.get(company)
            if not code:
                continue
            sample = items[:SAMPLE_PER_COMPANY]
            print(f"\n{'='*55}")
            print(f"🔍 驗證 {company}（抽 {len(sample)} / {len(items)} 個）")
            cookies = get_session_cookies(page, context, code)
            if not cookies:
                print("   ❌ 無法取得 session，跳過此公司")
                continue

            for it in sample:
                cand = it["candidate"]
                try:
                    pdfs = S.get_all_pdfs(cand, cookies, retries=2)
                    if pdfs:
                        results["real_miss"].append((company, it, list(pdfs.keys())))
                        print(f"   ⚠️  真漏！{it['productFolder'][:28]} v{it['version']} "
                              f"({cand}) → {list(pdfs.keys())}")
                    else:
                        results["genuine_gap"].append((company, it))
                        print(f"   ✅ TII 無此版：{it['productFolder'][:28]} v{it['version']}")
                except S.DetailListError as e:
                    results["uncertain"].append((company, it, str(e)))
                    print(f"   ❓ 網路失敗無法判定：{it['productFolder'][:28]} v{it['version']}")
                time.sleep(0.5)

        browser.close()

    print(f"\n{'='*55}")
    print("🏁 抽樣驗證結果")
    print(f"   ⚠️  真漏抓（TII 有、我們沒有）：{len(results['real_miss'])} 個")
    print(f"   ✅ TII 本身就沒這版（合理跳號）：{len(results['genuine_gap'])} 個")
    print(f"   ❓ 網路失敗無法判定：{len(results['uncertain'])} 個")

    if results["real_miss"]:
        print("\n真漏清單（需再補抓）：")
        for co, it, docs in results["real_miss"]:
            print(f"   {it['candidate']}  {it['productFolder']} v{it['version']}  {docs}")
        # 存檔供補抓
        (BASE / "verify_real_miss.json").write_text(
            json.dumps([{"company": co, **it, "docs": docs}
                        for co, it, docs in results["real_miss"]],
                       ensure_ascii=False, indent=2), "utf-8")
        print(f"\n   已存到 verify_real_miss.json")


if __name__ == "__main__":
    main()
