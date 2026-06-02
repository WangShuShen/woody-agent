#!/usr/bin/env python3
"""
保單條款 AI 分析器
用法：
  python3 analyzer.py --input 206_products.json           # 分析所有有 PDF 的商品
  python3 analyzer.py --input 206_products.json --limit 1 # 測試單筆
  python3 analyzer.py --input 206_products.json --force   # 強制重新分析
"""

import json
import re
import argparse
import base64
from datetime import date
from pathlib import Path
import anthropic

BASE_DIR   = Path(__file__).parent
STATE_FILE = BASE_DIR / "analyzed_state.json"
OUTPUT_DIR = BASE_DIR / "analyzed"
OUTPUT_DIR.mkdir(exist_ok=True)

SKILL_PATH = Path.home() / ".claude" / "skills" / "policy-db-extract" / "SKILL.md"


def load_skill_prompt() -> str:
    """讀取 policy-db-extract skill，去掉 frontmatter 作為 system prompt"""
    text = SKILL_PATH.read_text(encoding="utf-8")
    text = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.DOTALL)
    return text.strip()


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict):
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def extract_json(response_text: str) -> dict | None:
    """從 Claude 回應中提取 ```json ... ``` 區塊"""
    # 找 ```json 區塊（允許前後有空白）
    match = re.search(r"```json\s*(\{.*?\})\s*```", response_text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError as e:
            print(f"      ⚠️  JSON 解析失敗：{e}")

    # 備援：找最後一個完整 { ... } 區塊
    candidates = re.findall(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", response_text, re.DOTALL)
    for c in reversed(candidates):
        if '"company"' in c or '"items"' in c:
            try:
                return json.loads(c)
            except json.JSONDecodeError:
                continue
    return None


def analyze_product(product: dict, skill_prompt: str) -> tuple[dict | None, str]:
    """
    用 Claude API 分析單份保單 PDF。
    回傳 (parsed_json, full_response_text)
    """
    pdf_path = product.get("pdfPath", "")
    if not pdf_path or not Path(pdf_path).exists():
        raise FileNotFoundError(f"找不到 PDF：{pdf_path}")

    pdf_b64 = base64.b64encode(Path(pdf_path).read_bytes()).decode()

    user_msg = (
        f"請分析這份保單條款。\n"
        f"公司：{product['company']}\n"
        f"商品名稱：{product['productName']}\n"
        f"商品編號：{product['planCode']}\n\n"
        f"請依照指示輸出「人工審核表」和「資料庫 JSON」。"
    )

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8192,
        system=skill_prompt,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": pdf_b64,
                    },
                },
                {"type": "text", "text": user_msg},
            ],
        }],
    )

    full_text = response.content[0].text
    data = extract_json(full_text)
    return data, full_text


def main():
    parser = argparse.ArgumentParser(description="保單條款 AI 分析器")
    parser.add_argument("--input",  required=True, help="scraper 輸出的 JSON 路徑")
    parser.add_argument("--limit",  type=int, default=0, help="最多分析幾筆（0=全部）")
    parser.add_argument("--force",  action="store_true", help="強制重新分析（忽略已完成狀態）")
    args = parser.parse_args()

    products     = json.loads(Path(args.input).read_text(encoding="utf-8"))
    state        = load_state()
    skill_prompt = load_skill_prompt()

    print(f"📋 共 {len(products)} 筆商品")
    if args.limit:
        print(f"   本次最多分析 {args.limit} 筆")

    analyzed = skipped = failed = 0

    for product in products:
        plan_code    = product.get("planCode", "")
        product_name = product.get("productName", plan_code)
        prev_status  = state.get(plan_code, {}).get("status", "")

        # 跳過已完成（除非 --force）
        if not args.force and prev_status == "analyzed":
            print(f"   ⏭️  {product_name} 已分析，跳過")
            skipped += 1
            continue

        # 無 PDF 跳過
        if not product.get("pdfPath"):
            print(f"   ⏭️  {product_name} 無 PDF，跳過")
            state[plan_code] = {"status": "no_pdf"}
            save_state(state)
            continue

        if args.limit > 0 and analyzed >= args.limit:
            break

        print(f"\n🔍 分析：{product_name} ({plan_code})")

        try:
            data, full_text = analyze_product(product, skill_prompt)

            if not data:
                print("   ❌ 無法從回應中提取 JSON")
                # 儲存完整回應供偵錯
                debug_path = OUTPUT_DIR / f"{plan_code}_debug.txt"
                debug_path.write_text(full_text, encoding="utf-8")
                print(f"   完整回應已存：{debug_path.name}")
                print(f"   回應結尾200字：...{full_text[-200:]}")
                state[plan_code] = {"status": "failed", "error": "json_parse_error"}
                failed += 1
            else:
                # 補充欄位
                data.setdefault("planCode", plan_code)
                data["status"]      = "待審核"
                data["extractedAt"] = date.today().isoformat()

                # 儲存分析結果（含完整原始回應）
                out_data = {**data, "_rawResponse": full_text}
                out_path = OUTPUT_DIR / f"{plan_code}.json"
                out_path.write_text(
                    json.dumps(out_data, ensure_ascii=False, indent=2),
                    encoding="utf-8"
                )

                state[plan_code] = {
                    "status":      "analyzed",
                    "analyzedAt":  date.today().isoformat(),
                    "outputPath":  str(out_path),
                    "productName": product_name,
                }
                print(f"   ✅ 已儲存：{out_path.name}")
                print(f"   险種：{data.get('insuranceType')}  基礎類型：{data.get('baseType')}")
                analyzed += 1

        except FileNotFoundError as e:
            print(f"   ❌ {e}")
            state[plan_code] = {"status": "failed", "error": str(e)}
            failed += 1
        except Exception as e:
            print(f"   ❌ API 錯誤：{e}")
            state[plan_code] = {"status": "failed", "error": str(e)}
            failed += 1

        save_state(state)

    print(f"\n✅ 完成！分析 {analyzed} 筆 ｜ 跳過 {skipped} 筆 ｜ 失敗 {failed} 筆")


if __name__ == "__main__":
    main()
