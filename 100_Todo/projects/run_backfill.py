#!/usr/bin/env python3
"""
補抓自動重啟包裝器：
  依序對每間公司重跑 scraper_drive.py，崩潰自動重啟，
  直到該公司印出「🎉 完成」或達重試上限，再換下一間。

scraper 本身有：registry 逐筆寫入、子類別完成標記、單筆失敗記錄，
所以每次重啟都能安全續跑、只補缺口。
"""
import os
import re
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).parent
SCRAPER = BASE / "scraper_drive.py"
COMPANIES = ["206", "204"]   # 南山、國泰
MAX_RETRIES = 40             # 每間公司最多重啟次數
ROOT = BASE.parent.parent


def get_api_key() -> str:
    for fn in [".env", ".env.local", ".env.production"]:
        p = ROOT / "policy-analyzer" / fn
        if not p.exists():
            continue
        for line in p.read_text("utf-8", errors="replace").splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                if val:
                    return val
    return ""


def run_once(company: str, env: dict) -> bool:
    """跑一次 scraper，回傳是否「6 個子類別全部完成」（而非只看 🎉）"""
    proc = subprocess.run(
        [sys.executable, str(SCRAPER), "--company", company],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    out = proc.stdout or ""
    print(out[-2000:], flush=True)
    # 以子類別進度檔判斷是否真的全完成（6 個子類別齊全）
    import json as _json
    pf = BASE / f"scraper_subcat_{company}.json"
    done = set(_json.loads(pf.read_text("utf-8"))) if pf.exists() else set()
    return len(done) >= 6


def main():
    key = get_api_key()
    if not key:
        print("❌ 找不到 ANTHROPIC_API_KEY，無法解 CAPTCHA")
        sys.exit(1)
    env = dict(os.environ)
    env["ANTHROPIC_API_KEY"] = key

    for company in COMPANIES:
        print(f"\n{'#'*60}")
        print(f"# 開始補抓公司 {company}")
        print(f"{'#'*60}", flush=True)

        done = False
        for attempt in range(1, MAX_RETRIES + 1):
            print(f"\n>>> 公司 {company} 第 {attempt} 次嘗試 <<<", flush=True)
            try:
                done = run_once(company, env)
            except Exception as e:
                print(f"⚠️  執行例外：{e}", flush=True)
                done = False

            if done:
                print(f"\n✅ 公司 {company} 已完整跑完（第 {attempt} 次）", flush=True)
                break
            else:
                print(f"\n🔁 公司 {company} 未完成，10 秒後重啟...", flush=True)
                time.sleep(10)

        if not done:
            print(f"\n⚠️  公司 {company} 達重試上限 {MAX_RETRIES} 次仍未完成，先跳到下一間", flush=True)

    print(f"\n{'='*60}")
    print("🏁 全部公司補抓流程結束")
    print(f"{'='*60}", flush=True)


if __name__ == "__main__":
    main()
