#!/usr/bin/env python3
"""
保單條款自動化流水線
用法：
  python3 pipeline.py --company 206 --limit 1   # 測試單筆
  python3 pipeline.py --company 206              # 跑全部
  python3 pipeline.py --company 206 --skip-scrape  # 跳過爬蟲，用現有 JSON 重跑分析
"""

import json
import sys
import argparse
from datetime import datetime, date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from scraper        import scrape, should_exclude
from analyzer       import load_skill_prompt, analyze_product, load_state, save_state, OUTPUT_DIR, STATE_FILE
from push_to_sheets import (
    connect, get_or_create_folder, move_file,
    build_coverage_sheet, build_restrictions_sheet, build_claim_docs_sheet,
    SHEET_FOLDER_NAME,
)
from sync_registry  import download_registry, upload_registry

BASE_DIR = Path(__file__).parent


# ── Drive PDF 上傳 ───────────────────────────────────────
def upload_pdf(drive, pdf_path: Path, filename: str, folder_id: str) -> str:
    """上傳 PDF 到指定 Drive 資料夾，回傳 file_id"""
    from googleapiclient.http import MediaFileUpload
    meta  = {"name": filename + ".pdf", "parents": [folder_id]}
    media = MediaFileUpload(str(pdf_path), mimetype="application/pdf", resumable=False)
    f = drive.files().create(body=meta, media_body=media, fields="id").execute()
    return f["id"]


# ── Google Sheet 建立 ────────────────────────────────────
def create_sheet(gc, drive, data: dict, folder_id: str) -> str:
    """建立格式化 Google Sheet，放入指定資料夾，回傳 URL"""
    company  = data.get("company", "")
    product  = data.get("productName", "")
    date_str = datetime.today().strftime("%Y%m%d")
    title    = f"【待審核】{company} {product} {date_str}"

    sh = gc.create(title)
    sh.sheet1.update_title("給付項目審核")
    sh.add_worksheet(title="除外責任與限制", rows=100, cols=10)
    sh.add_worksheet(title="理賠必要文件",   rows=100, cols=10)

    # 先移到資料夾，再填內容（避免失敗時留空白孤兒在根目錄）
    move_file(drive, sh.id, folder_id)

    build_coverage_sheet(sh, data)
    build_restrictions_sheet(sh, data)
    build_claim_docs_sheet(sh, data)

    return f"https://docs.google.com/spreadsheets/d/{sh.id}"


# ── 主流程 ───────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="保單條款自動化流水線")
    parser.add_argument("--company",      required=True, help="公司代碼，例如 206（南山人壽）")
    parser.add_argument("--limit",        type=int, default=0, help="最多處理幾筆（0=全部）")
    parser.add_argument("--skip-scrape",  action="store_true", help="跳過爬蟲，用現有 products JSON")
    parser.add_argument("--analyze-only", action="store_true", help="只跑分析，不上傳 Drive / Sheets")
    parser.add_argument("--upload-only",  action="store_true", help="只跑上傳（假設 analyzed/ 已存在）")
    parser.add_argument("--force",        action="store_true", help="強制重新處理（忽略已上傳狀態）")
    args = parser.parse_args()

    products_path = BASE_DIR / f"{args.company}_products.json"
    state         = load_state()

    # ── Phase 1：爬蟲 ────────────────────────────────────
    if args.skip_scrape or args.upload_only:
        if not products_path.exists():
            print(f"❌ 找不到 {products_path}，請先不帶 --skip-scrape 執行一次")
            sys.exit(1)
        products = json.loads(products_path.read_text(encoding="utf-8"))
        print(f"📂 載入現有資料：{len(products)} 筆（已跳過爬蟲）")
    else:
        print(f"🕷️  Phase 1：爬取 TII 公司代碼 {args.company}...")
        products = scrape(args.company, limit=args.limit)
        if not products:
            print("⚠️  爬蟲回傳 0 筆，流程中止（CAPTCHA 可能失敗，請手動確認後再重跑）")
            sys.exit(1)
        products_path.write_text(
            json.dumps(products, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"   已存：{products_path.name}，共 {len(products)} 筆")

    # ── Phase 2：連線 Google Drive（analyze-only 模式跳過）──
    gc = drive = pending_id = root_id = None
    if not args.analyze_only:
        print("\n🔌 Phase 2：連線 Google Sheets / Drive...")
        gc, drive  = connect()
        root_id    = get_or_create_folder(drive, SHEET_FOLDER_NAME)
        pending_id = get_or_create_folder(drive, "待審核", root_id)
        print("   連線成功")

        print("   🔄 同步 Drive registry（雙機共用狀態）...")
        state = download_registry(drive, root_id, STATE_FILE)

    # ── Phase 3：分析 + 上傳 ─────────────────────────────
    print(f"\n🔍 Phase 3：{'只分析（不上傳）' if args.analyze_only else '分析保單條款並上傳'}...")
    skill_prompt = load_skill_prompt() if not args.upload_only else None

    done = failed = skipped = 0

    for product in products:
        plan_code    = product.get("planCode", "")
        pdf_uuid     = product.get("pdfUUID", "") or plan_code  # 舊資料相容
        product_name = product.get("productName", plan_code)
        prev_status  = state.get(pdf_uuid, {}).get("status", "")

        # 排除團險、產險
        if should_exclude(product_name):
            print(f"   ⏭️  {product_name}  排除（團險/產險）")
            skipped += 1
            continue

        # 跳過已上傳（除非 --force）；UUID 去重，相同 PDF 不重複上傳
        if not args.force and prev_status in ("uploaded", "archived"):
            print(f"   ⏭️  {product_name}  [{pdf_uuid[:8]}...] 已上傳，跳過")
            skipped += 1
            continue

        # 無 PDF 跳過
        pdf_path = Path(product.get("pdfPath") or "")
        if not pdf_path.exists():
            print(f"   ⏭️  {product_name}  無 PDF，跳過")
            continue

        # 達到 limit 停止
        if args.limit > 0 and done >= args.limit:
            break

        filename = product.get("filename", plan_code)
        print(f"\n📄 [{done + 1}] {product_name}")
        print(f"   {filename}")

        try:
            # ── 分析（如果尚未分析過）──────────────────────
            out_path = OUTPUT_DIR / f"{pdf_uuid}.json"
            if args.upload_only and out_path.exists():
                # upload-only 模式：直接讀已有分析結果
                data = json.loads(out_path.read_text(encoding="utf-8"))
                data.pop("_rawResponse", None)
                print("   📂 載入已有分析結果（跳過 Claude API）")
            elif not args.upload_only and out_path.exists() and not args.force:
                # 一般模式：分析 JSON 已存在，跳過重複分析
                data = json.loads(out_path.read_text(encoding="utf-8"))
                data.pop("_rawResponse", None)
                print("   📂 已有分析結果，跳過 Claude API")
            else:
                print("   🔍 Claude 分析中...")
                data, full_text = analyze_product(product, skill_prompt)
                if not data:
                    raise ValueError("回應中找不到 JSON")
                data.setdefault("planCode", plan_code)
                data["status"]      = "待審核"
                data["extractedAt"] = date.today().isoformat()
                out_path.write_text(
                    json.dumps({**data, "_rawResponse": full_text}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                # 分析成功後先更新狀態，避免後續 Drive 失敗導致整筆重新分析
                if pdf_uuid not in state or state[pdf_uuid].get("status") == "failed":
                    state[pdf_uuid] = {
                        "status": "analyzed", "analyzedAt": date.today().isoformat(),
                        "planCode": plan_code, "company": product.get("company", ""),
                        "productName": product_name, "filename": filename,
                    }
                    save_state(state)
                    if drive and root_id:
                        upload_registry(drive, root_id, state, STATE_FILE)

            # ── 上傳（analyze-only 模式跳過）──────────────
            if args.analyze_only:
                done += 1
                continue

            print(f"   📎 上傳 PDF → Drive 待審核/")
            pdf_drive_id = upload_pdf(drive, pdf_path, filename, pending_id)

            print("   📊 建立 Google Sheet...")
            sheet_url = create_sheet(gc, drive, data, pending_id)

            state[pdf_uuid] = {
                "status":      "uploaded",
                "uploadedAt":  date.today().isoformat(),
                "sheetUrl":    sheet_url,
                "pdfDriveId":  pdf_drive_id,
                "planCode":    plan_code,
                "company":     product.get("company", ""),
                "productName": product_name,
                "filename":    filename,
            }
            save_state(state)
            upload_registry(drive, root_id, state, STATE_FILE)

            print(f"   ✅  Sheet：{sheet_url}")
            done += 1

        except Exception as e:
            print(f"   ❌  失敗：{e}")
            # 保留已有分析狀態（不退步到 failed），避免重複呼叫 Claude
            if state.get(pdf_uuid, {}).get("status") not in ("analyzed", "uploaded", "archived"):
                state[pdf_uuid] = {"status": "failed", "error": str(e), "planCode": plan_code}
                save_state(state)
            failed += 1

    # ── 摘要 ─────────────────────────────────────────────
    mode_label = "分析" if args.analyze_only else "上傳"
    print(f"\n{'═' * 52}")
    print(f"完成！{mode_label} {done} 筆 ｜ 跳過 {skipped} 筆 ｜ 失敗 {failed} 筆")
    if done > 0 and not args.analyze_only:
        print(f"📂 位置：Google Drive → 保單審核資料庫 / 待審核/")
        print(f"   審核後執行歸檔：")
        print(f"   python3 push_to_sheets.py --archive <sheet_id>")


if __name__ == "__main__":
    main()
