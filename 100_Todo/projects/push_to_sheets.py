#!/usr/bin/env python3
"""
保單資料庫 → Google Sheets 自動推送腳本
使用方式：
  python3 push_to_sheets.py <policy_json_file>
  python3 push_to_sheets.py --stdin   (從 stdin 讀 JSON)

JSON 格式參考 policy-db-extract 輸出的資料庫 JSON 區塊。
"""

import re
import sys
import json
import argparse
from datetime import datetime
from pathlib import Path

import gspread
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# ── 設定 ──────────────────────────────────────────
CREDENTIALS_FILE  = Path(__file__).parent.parent.parent / "000_Agent" / "google_credentials.json"
AUTHORIZED_FILE   = Path(__file__).parent.parent.parent / "000_Agent" / "authorized_user.json"
SHEET_FOLDER_NAME = "保單審核資料庫"   # Google Drive 資料夾名稱（會自動建立）

# ── 顏色常數 ──────────────────────────────────────
COLOR = {
    "dark_navy":   {"red": 0.10, "green": 0.10, "blue": 0.18},
    "navy":        {"red": 0.06, "green": 0.19, "blue": 0.38},
    "gold":        {"red": 0.91, "green": 0.77, "blue": 0.28},
    "red":         {"red": 0.75, "green": 0.22, "blue": 0.17},
    "orange":      {"red": 0.90, "green": 0.50, "blue": 0.13},
    "green_dark":  {"red": 0.08, "green": 0.38, "blue": 0.27},
    "white":       {"red": 1.00, "green": 1.00, "blue": 1.00},
    "gray_light":  {"red": 0.97, "green": 0.97, "blue": 0.98},
    "yellow_pale": {"red": 1.00, "green": 0.95, "blue": 0.80},
    "green_pale":  {"red": 0.83, "green": 0.93, "blue": 0.85},
    "red_pale":    {"red": 0.97, "green": 0.84, "blue": 0.84},
}


DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def connect():
    """建立 Google Sheets + Drive 連線（OAuth2，第一次跑會開瀏覽器授權）"""
    if not CREDENTIALS_FILE.exists():
        print(f"❌ 找不到 OAuth 憑證：{CREDENTIALS_FILE}")
        print("請確認已將 google_credentials.json 放到 000_Agent/ 資料夾")
        sys.exit(1)
    gc = gspread.oauth(
        credentials_filename=str(CREDENTIALS_FILE),
        authorized_user_filename=str(AUTHORIZED_FILE),
    )
    # 用 gspread 授權後存下來的 token 建立 Drive 服務
    creds = Credentials.from_authorized_user_file(str(AUTHORIZED_FILE), DRIVE_SCOPES)
    drive = build("drive", "v3", credentials=creds)
    return gc, drive


def get_or_create_folder(drive, name, parent_id=None):
    """在 Google Drive 找到或建立資料夾，回傳資料夾 ID"""
    q = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
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


def move_file(drive, file_id, new_parent_id):
    """把 Google Drive 檔案移到指定資料夾"""
    f = drive.files().get(fileId=file_id, fields="parents").execute()
    old_parents = ",".join(f.get("parents", []))
    drive.files().update(
        fileId=file_id,
        addParents=new_parent_id,
        removeParents=old_parents,
        fields="id, parents",
    ).execute()


def archive_sheet(gc, drive, sheet_ref, force=False, pdf_drive_id=None):
    """把審核完成的試算表歸檔到正確的險種/公司資料夾"""
    # 支援完整 URL 或純 ID
    if "docs.google.com" in sheet_ref:
        sheet_id = sheet_ref.split("/d/")[1].split("/")[0]
    else:
        sheet_id = sheet_ref

    sh = gc.open_by_key(sheet_id)
    ws = sh.worksheet("給付項目審核")

    # 從 A1 解析險種與公司（格式：【險種】公司　｜　商品　｜　planCode）
    header = ws.cell(1, 1).value or ""
    type_match    = re.search(r"【(.+?)】", header)
    company_match = re.search(r"】(.+?)　｜", header)
    insurance_type = type_match.group(1).strip()    if type_match    else "未分類"
    company        = company_match.group(1).strip() if company_match else "未知公司"

    # 檢查是否還有「待審核」或「需修改」項目（A 欄 = 審核狀態）
    all_values = ws.get_all_values()
    incomplete_statuses = {"待審核", "需修改", "進一步核實"}
    pending = [r[0] for r in all_values[4:] if r and r[0] in incomplete_statuses]
    if pending and not force:
        print(f"⚠️  還有 {len(pending)} 個項目尚未審核完成。確定要歸檔嗎？(y/N) ", end="", flush=True)
        if input().strip().lower() != "y":
            print("取消歸檔。")
            return
    elif pending and force:
        print(f"⚠️  跳過確認，強制歸檔（{len(pending)} 個項目尚未審核）")

    # 建立目標資料夾層級：保單審核資料庫 / 公司 / 險種
    root_id    = get_or_create_folder(drive, SHEET_FOLDER_NAME)
    company_id = get_or_create_folder(drive, company, root_id)
    type_id    = get_or_create_folder(drive, insurance_type, company_id)

    # 移動 Sheet
    move_file(drive, sh.id, type_id)

    # 重新命名：移除「待審核」前綴和公司名，日期改用底線
    new_title = re.sub(r"^【待審核】\s*", "", sh.title).strip()
    new_title = re.sub(rf"^{re.escape(company)}\s+", "", new_title).strip()
    new_title = re.sub(r"\s+(\d{8})$", r"_\1", new_title)
    drive.files().update(fileId=sh.id, body={"name": new_title}).execute()

    # 移動 PDF（如果有提供）
    if pdf_drive_id:
        try:
            move_file(drive, pdf_drive_id, type_id)
            print(f"   📄 PDF 已移至同一資料夾")
        except Exception as e:
            print(f"   ⚠️  PDF 移動失敗（不影響歸檔）：{e}")

    url = f"https://docs.google.com/spreadsheets/d/{sh.id}"
    print(f"\n✅ 歸檔完成！")
    print(f"   路徑：保單審核資料庫 / {company} / {insurance_type}")
    print(f"   連結：{url}\n")


# ── 格式化工具 ────────────────────────────────────

def cell_format(bg_color, font_color, font_size=10, bold=False):
    return {
        "backgroundColor": bg_color,
        "textFormat": {
            "foregroundColor": font_color,
            "fontSize": font_size,
            "bold": bold,
        },
        "verticalAlignment": "MIDDLE",
        "wrapStrategy": "WRAP",
    }


def border_style():
    solid = {"style": "SOLID", "color": {"red": 0.8, "green": 0.8, "blue": 0.8}}
    return {"top": solid, "bottom": solid, "left": solid, "right": solid,
            "innerHorizontal": solid, "innerVertical": solid}


def row_range(sheet_id, row, col_start=0, col_end=6):
    return {
        "sheetId": sheet_id,
        "startRowIndex": row, "endRowIndex": row + 1,
        "startColumnIndex": col_start, "endColumnIndex": col_end,
    }


def block_range(sheet_id, r1, r2, c1=0, c2=6):
    return {
        "sheetId": sheet_id,
        "startRowIndex": r1, "endRowIndex": r2,
        "startColumnIndex": c1, "endColumnIndex": c2,
    }


# ── 下拉選單常數 ────────────────────────────────────

REVIEW_STATUS_OPTIONS   = ["待審核", "確認正確", "需修改", "忽略此項", "進一步核實"]
OVERALL_STATUS_OPTIONS  = ["待審核", "確認正確", "有疑問需修改"]
RESTRICTION_CATEGORIES  = ["等待期", "除外責任", "特別限制", "給付觸發條件"]
CLAIM_SITUATIONS        = ["每次申請必備", "住院申請", "手術申請", "出院後申請",
                            "重大疾病確診", "身故申請", "其他"]


def dropdown_rule(options: list[str]) -> dict:
    return {
        "condition": {"type": "ONE_OF_LIST",
                      "values": [{"userEnteredValue": v} for v in options]},
        "showCustomUi": True,
        "strict": True,
    }


def format_amount(formula: str, unit: str) -> str:
    """合併公式與單位為單一顯示字串"""
    formula = (formula or "").strip()
    unit    = (unit    or "").strip()
    if not formula:
        return "—"
    return f"{formula}{unit}" if unit else formula


# ── 分頁 1：給付項目審核 ──────────────────────────
# 欄位（5 欄）：A=審核狀態  B=給付項目  C=給付金額  D=限制條件  E=注意事項

def build_coverage_sheet(sh, data):
    ws = sh.worksheet("給付項目審核")
    sheet_id = ws.id
    NUM_COLS = 5

    company      = data.get("company", "—")
    product      = data.get("productName", "—")
    plan         = data.get("planCode", "—") or "—"
    base         = data.get("baseType", "—")
    ins_type_raw = data.get("insuranceType", ["—"])
    ins_type     = ins_type_raw[0] if isinstance(ins_type_raw, list) else ins_type_raw
    ins_type_str = "、".join(ins_type_raw) if isinstance(ins_type_raw, list) else ins_type_raw
    date_str     = data.get("extractedAt", datetime.today().strftime("%Y-%m-%d"))
    items        = data.get("items", [])
    limit        = data.get("annualLimit") or {}

    header1  = f"【{ins_type}】{company}　｜　{product}　｜　{plan}"
    header2  = f"保額基礎：{base}　｜　險種：{ins_type_str}　｜　提取日期：{date_str}"
    col_hdrs = ["審核狀態", "給付項目", "給付金額", "限制條件", "注意事項"]

    rows = [[header1], [header2], [], col_hdrs]
    for item in items:
        rows.append([
            "待審核",
            item.get("name", ""),
            format_amount(item.get("formula", ""), item.get("unit", "")),
            item.get("restriction", "") or "—",
            item.get("notes", "") or "—",
        ])
    rows.append([])
    rows.append([
        "待審核",
        "⚠️ 累積給付上限",
        format_amount(limit.get("formula", ""), ""),
        "所有給付項目合計",
        limit.get("notes", ""),
    ])

    ws.update(rows, "A1", value_input_option="USER_ENTERED")

    requests = []

    # 合併標題行
    for r in [0, 1, 2]:
        requests.append({"mergeCells": {
            "range": block_range(sheet_id, r, r + 1, 0, NUM_COLS),
            "mergeType": "MERGE_ALL",
        }})

    def fmt_row(row, bg, fg, size, bold):
        return {"repeatCell": {
            "range": row_range(sheet_id, row, col_end=NUM_COLS),
            "cell": {"userEnteredFormat": cell_format(bg, fg, size, bold)},
            "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment,wrapStrategy)",
        }}

    requests += [
        fmt_row(0, COLOR["dark_navy"], COLOR["white"], 13, True),
        fmt_row(1, COLOR["navy"],      COLOR["gray_light"], 10, False),
        fmt_row(3, COLOR["navy"],      COLOR["white"], 11, True),
    ]

    # 資料行斑馬紋
    for i in range(len(items)):
        bg = COLOR["gray_light"] if i % 2 == 0 else COLOR["white"]
        requests.append({"repeatCell": {
            "range": row_range(sheet_id, 4 + i, col_end=NUM_COLS),
            "cell": {"userEnteredFormat": {"backgroundColor": bg, "textFormat": {"fontSize": 10},
                                            "verticalAlignment": "MIDDLE", "wrapStrategy": "WRAP"}},
            "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment,wrapStrategy)",
        }})

    limit_row = 4 + len(items) + 1
    requests.append(fmt_row(limit_row, COLOR["gold"], COLOR["dark_navy"], 10, True))

    # 欄寬：A(90) B(220) C(200) D(160) E(210)
    for i, w in enumerate([90, 220, 200, 160, 210]):
        requests.append({"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                      "startIndex": i, "endIndex": i + 1},
            "properties": {"pixelSize": w}, "fields": "pixelSize",
        }})

    # 凍結前 4 列
    requests.append({"updateSheetProperties": {
        "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 4}},
        "fields": "gridProperties.frozenRowCount",
    }})

    # 框線
    requests.append({"updateBorders": {
        "range": block_range(sheet_id, 3, limit_row + 1, 0, NUM_COLS),
        **border_style(),
    }})

    # 下拉：A 欄審核狀態
    data_rows = len(items) + 1
    requests.append({"setDataValidation": {
        "range": block_range(sheet_id, 4, 4 + data_rows, 0, 1),
        "rule": dropdown_rule(REVIEW_STATUS_OPTIONS),
    }})

    # 條件格式：A 欄審核狀態顏色
    status_range = block_range(sheet_id, 4, 4 + data_rows, 0, 1)
    for text, bg, fg in [
        ("確認正確",   COLOR["green_pale"],  {"red": 0.08, "green": 0.34, "blue": 0.14}),
        ("需修改",     COLOR["red_pale"],    {"red": 0.44, "green": 0.11, "blue": 0.11}),
        ("待審核",     COLOR["yellow_pale"], {"red": 0.52, "green": 0.39, "blue": 0.02}),
        ("忽略此項",   COLOR["gray_light"],  {"red": 0.50, "green": 0.50, "blue": 0.50}),
        ("進一步核實", {"red": 0.90, "green": 0.85, "blue": 1.0},
                       {"red": 0.28, "green": 0.09, "blue": 0.50}),
    ]:
        requests.append({"addConditionalFormatRule": {
            "rule": {
                "ranges": [status_range],
                "booleanRule": {
                    "condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": text}]},
                    "format": {"backgroundColor": bg, "textFormat": {"foregroundColor": fg, "bold": True}},
                },
            },
            "index": 0,
        }})

    sh.batch_update({"requests": requests})


# ── 分頁 2：除外責任與限制 ────────────────────────
# 欄位（2 欄）：A=類別  B=內容
# 頂部 row2 有整體審核狀態（單一 dropdown）

def build_restrictions_sheet(sh, data):
    ws = sh.worksheet("除外責任與限制")
    sheet_id = ws.id
    NUM_COLS = 2

    wp   = data.get("waitingPeriod") or {}
    excl = data.get("exclusions") or []
    spec = data.get("specialRestrictions") or []

    header   = ["理賠條件與限制　｜　" + data.get("productName", ""), ""]
    status   = ["整體審核狀態", "待審核"]   # B2 放 dropdown
    col_hdrs = ["類別", "內容"]

    rows = [header, status, [], col_hdrs]

    disease_days = wp.get("disease", 30)
    injury_days  = wp.get("injury", 0)
    wp_note      = wp.get("note", "")
    rows.append(["等待期", f"疾病：{disease_days}天"])
    rows.append(["等待期", "傷害意外：無" if injury_days == 0 else f"傷害意外：{injury_days}天"])
    if wp_note:
        rows.append(["等待期", wp_note])
    for e in excl:
        rows.append(["除外責任", e])
    for s in spec:
        rows.append(["特別限制", s])

    ws.update(rows, "A1", value_input_option="USER_ENTERED")

    data_start = 4   # row index（0-based）資料從第 5 列開始
    data_end   = len(rows)
    requests   = []

    def fmt_block(r1, r2, bg, fg, size=10, bold=True):
        return {"repeatCell": {
            "range": block_range(sheet_id, r1, r2, 0, NUM_COLS),
            "cell": {"userEnteredFormat": cell_format(bg, fg, size, bold)},
            "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment,wrapStrategy)",
        }}

    # 合併標題行
    requests.append({"mergeCells": {
        "range": block_range(sheet_id, 0, 1, 0, NUM_COLS), "mergeType": "MERGE_ALL",
    }})
    requests += [
        fmt_block(0, 1, COLOR["dark_navy"], COLOR["white"], 13),
        fmt_block(3, 4, COLOR["navy"],      COLOR["white"], 11),
    ]

    # row 2（index 1）整體審核狀態列
    requests.append({"repeatCell": {
        "range": block_range(sheet_id, 1, 2, 0, 1),
        "cell": {"userEnteredFormat": cell_format(COLOR["navy"], COLOR["white"], 10, True)},
        "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment,wrapStrategy)",
    }})
    requests.append({"repeatCell": {
        "range": block_range(sheet_id, 1, 2, 1, 2),
        "cell": {"userEnteredFormat": {
            "backgroundColor": COLOR["yellow_pale"],
            "textFormat": {"fontSize": 10, "bold": True, "foregroundColor": {"red": 0.52, "green": 0.39, "blue": 0.02}},
            "verticalAlignment": "MIDDLE",
        }},
        "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment)",
    }})
    # dropdown on B2
    requests.append({"setDataValidation": {
        "range": block_range(sheet_id, 1, 2, 1, 2),
        "rule": dropdown_rule(OVERALL_STATUS_OPTIONS),
    }})
    # 條件格式：B2 整體狀態顏色
    overall_range = block_range(sheet_id, 1, 2, 1, 2)
    for text, bg, fg in [
        ("確認正確",     COLOR["green_pale"], {"red": 0.08, "green": 0.34, "blue": 0.14}),
        ("有疑問需修改", COLOR["red_pale"],   {"red": 0.44, "green": 0.11, "blue": 0.11}),
        ("待審核",       COLOR["yellow_pale"],{"red": 0.52, "green": 0.39, "blue": 0.02}),
    ]:
        requests.append({"addConditionalFormatRule": {
            "rule": {
                "ranges": [overall_range],
                "booleanRule": {
                    "condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": text}]},
                    "format": {"backgroundColor": bg, "textFormat": {"foregroundColor": fg, "bold": True}},
                },
            }, "index": 0,
        }})

    # 資料行斑馬紋
    for i in range(data_start, data_end):
        bg = COLOR["gray_light"] if (i - data_start) % 2 == 0 else COLOR["white"]
        requests.append({"repeatCell": {
            "range": block_range(sheet_id, i, i + 1, 0, NUM_COLS),
            "cell": {"userEnteredFormat": {"backgroundColor": bg, "textFormat": {"fontSize": 10},
                                            "verticalAlignment": "MIDDLE", "wrapStrategy": "WRAP"}},
            "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment,wrapStrategy)",
        }})

    # 下拉：A 欄類別
    requests.append({"setDataValidation": {
        "range": block_range(sheet_id, data_start, data_end, 0, 1),
        "rule": dropdown_rule(RESTRICTION_CATEGORIES),
    }})

    # 欄寬：A(110) B(540)
    for i, w in enumerate([110, 540]):
        requests.append({"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                      "startIndex": i, "endIndex": i + 1},
            "properties": {"pixelSize": w}, "fields": "pixelSize",
        }})

    requests.append({"updateSheetProperties": {
        "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 4}},
        "fields": "gridProperties.frozenRowCount",
    }})
    requests.append({"updateBorders": {
        "range": block_range(sheet_id, 3, data_end, 0, NUM_COLS),
        **border_style(),
    }})

    sh.batch_update({"requests": requests})


# ── 分頁 3：理賠必要文件 ──────────────────────────
# 欄位（2 欄）：A=申請情境  B=必要文件
# 頂部 row2 有整體審核狀態（單一 dropdown）

def build_claim_docs_sheet(sh, data):
    ws = sh.worksheet("理賠必要文件")
    sheet_id = ws.id
    NUM_COLS = 2

    sections = [
        ("住院申請", [
            "保險金申請書",
            "被保險人身分證影本",
            "醫療診斷書（含住院原因/入出院日期）",
            "加護病房住院證明（載明起訖日期）",
            "受益人身分證影本",
            "受益人存摺封面影本",
        ]),
        ("手術申請", [
            "保險金申請書",
            "被保險人身分證影本",
            "醫療診斷書（載明手術名稱及日期）",
            "手術紀錄書",
            "開刀房紀錄（含麻醉方式）",
            "受益人身分證影本",
            "受益人存摺封面影本",
        ]),
        ("出院後申請", [
            "保險金申請書",
            "門診收據（含就診日期/診斷）",
            "住院診斷書（證明同一疾病/傷害）",
            "出院診斷書（含出院日期及住院日數）",
            "受益人身分證影本",
            "受益人存摺封面影本",
        ]),
        ("身故申請", [
            "保險金申請書",
            "保險單或其謄本",
            "死亡診斷書（或相驗屍體證明書）",
            "除戶戶籍謄本",
            "受益人戶籍謄本",
            "受益人身分證影本",
            "受益人存摺封面影本",
        ]),
        ("重大疾病確診", [
            "診斷書（載明重大疾病種類）",
            "心肌梗塞：心電圖＋心肌酶報告",
            "腦中風：腦部影像（CT/MRI）",
            "癌症：病理組織切片報告",
            "慢性腎衰竭：腎功能檢查＋洗腎紀錄",
            "癱瘓：神經科專科醫師診斷書",
            "器官移植：移植手術紀錄",
        ]),
        ("其他", [
            "救護車費用收據（緊急醫療運送）",
            "急診或住院證明（緊急醫療運送）",
        ]),
    ]

    header   = ["理賠必要文件　｜　" + data.get("productName", ""), ""]
    status   = ["整體審核狀態", "待審核"]   # B2 放 dropdown
    col_hdrs = ["申請情境", "必要文件"]
    rows     = [header, status, [], col_hdrs]

    for situation, docs in sections:
        for doc in docs:
            rows.append([situation, doc])

    ws.update(rows, "A1", value_input_option="USER_ENTERED")

    data_start = 4
    data_end   = len(rows)
    requests   = []

    def fmt_block(r1, r2, bg, fg, size=10, bold=True):
        return {"repeatCell": {
            "range": block_range(sheet_id, r1, r2, 0, NUM_COLS),
            "cell": {"userEnteredFormat": cell_format(bg, fg, size, bold)},
            "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment,wrapStrategy)",
        }}

    # 合併標題行
    requests.append({"mergeCells": {
        "range": block_range(sheet_id, 0, 1, 0, NUM_COLS), "mergeType": "MERGE_ALL",
    }})
    requests += [
        fmt_block(0, 1, COLOR["dark_navy"], COLOR["white"], 13),
        fmt_block(3, 4, COLOR["navy"],      COLOR["white"], 11),
    ]

    # row 2（index 1）整體審核狀態列
    requests.append({"repeatCell": {
        "range": block_range(sheet_id, 1, 2, 0, 1),
        "cell": {"userEnteredFormat": cell_format(COLOR["navy"], COLOR["white"], 10, True)},
        "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment,wrapStrategy)",
    }})
    requests.append({"repeatCell": {
        "range": block_range(sheet_id, 1, 2, 1, 2),
        "cell": {"userEnteredFormat": {
            "backgroundColor": COLOR["yellow_pale"],
            "textFormat": {"fontSize": 10, "bold": True, "foregroundColor": {"red": 0.52, "green": 0.39, "blue": 0.02}},
            "verticalAlignment": "MIDDLE",
        }},
        "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment)",
    }})
    requests.append({"setDataValidation": {
        "range": block_range(sheet_id, 1, 2, 1, 2),
        "rule": dropdown_rule(OVERALL_STATUS_OPTIONS),
    }})
    overall_range = block_range(sheet_id, 1, 2, 1, 2)
    for text, bg, fg in [
        ("確認正確",     COLOR["green_pale"], {"red": 0.08, "green": 0.34, "blue": 0.14}),
        ("有疑問需修改", COLOR["red_pale"],   {"red": 0.44, "green": 0.11, "blue": 0.11}),
        ("待審核",       COLOR["yellow_pale"],{"red": 0.52, "green": 0.39, "blue": 0.02}),
    ]:
        requests.append({"addConditionalFormatRule": {
            "rule": {
                "ranges": [overall_range],
                "booleanRule": {
                    "condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": text}]},
                    "format": {"backgroundColor": bg, "textFormat": {"foregroundColor": fg, "bold": True}},
                },
            }, "index": 0,
        }})

    # 資料行斑馬紋
    for i in range(data_start, data_end):
        bg = COLOR["gray_light"] if (i - data_start) % 2 == 0 else COLOR["white"]
        requests.append({"repeatCell": {
            "range": block_range(sheet_id, i, i + 1, 0, NUM_COLS),
            "cell": {"userEnteredFormat": {"backgroundColor": bg, "textFormat": {"fontSize": 10},
                                            "verticalAlignment": "MIDDLE", "wrapStrategy": "WRAP"}},
            "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment,wrapStrategy)",
        }})

    # 下拉：A 欄申請情境
    requests.append({"setDataValidation": {
        "range": block_range(sheet_id, data_start, data_end, 0, 1),
        "rule": dropdown_rule(CLAIM_SITUATIONS),
    }})

    # 欄寬：A(130) B(510)
    for i, w in enumerate([130, 510]):
        requests.append({"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                      "startIndex": i, "endIndex": i + 1},
            "properties": {"pixelSize": w}, "fields": "pixelSize",
        }})

    requests.append({"updateSheetProperties": {
        "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 4}},
        "fields": "gridProperties.frozenRowCount",
    }})
    requests.append({"updateBorders": {
        "range": block_range(sheet_id, 3, data_end, 0, NUM_COLS),
        **border_style(),
    }})

    sh.batch_update({"requests": requests})


# ── 主流程 ────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="推送保單 JSON 到 Google Sheets")
    parser.add_argument("json_file", nargs="?", help="policy-db-extract 輸出的 JSON 檔案路徑")
    parser.add_argument("--sheet-id", help="已建立的 Google Sheet ID 或 URL")
    parser.add_argument("--stdin",    action="store_true", help="從 stdin 讀取 JSON")
    parser.add_argument("--archive",  metavar="SHEET_ID_OR_URL",
                        help="歸檔已審核的試算表到正確的險種/公司資料夾")
    parser.add_argument("--force",    action="store_true",
                        help="歸檔時跳過未審核確認")
    parser.add_argument("--pdf-id",   metavar="FILE_ID", dest="pdf_id",
                        help="歸檔時同步將 PDF 移到同一資料夾")
    args = parser.parse_args()

    print("🔌 連線 Google Sheets...")
    gc, drive = connect()

    # ── 歸檔模式 ──────────────────────────────────────
    if args.archive:
        print(f"📦 歸檔模式：{args.archive}")
        archive_sheet(gc, drive, args.archive, force=args.force, pdf_drive_id=getattr(args, "pdf_id", None))
        return

    # ── 建立模式：讀取 JSON ────────────────────────────
    if args.stdin:
        data = json.load(sys.stdin)
    elif args.json_file:
        with open(args.json_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        parser.print_help()
        sys.exit(1)

    company  = data.get("company", "")
    product  = data.get("productName", "")
    date_str = datetime.today().strftime("%Y%m%d")
    print(f"📋 保單：{company} / {product}")

    if args.sheet_id:
        # 寫入現有試算表
        ref = args.sheet_id
        if "docs.google.com" in ref:
            ref = ref.split("/d/")[1].split("/")[0]
        print(f"📂 開啟現有試算表：{ref}")
        sh = gc.open_by_key(ref)
        existing = [ws.title for ws in sh.worksheets()]
        for t in ["給付項目審核", "除外責任與限制", "理賠必要文件"]:
            if t not in existing:
                sh.add_worksheet(title=t, rows=100, cols=10)
        try:
            sh.sheet1.update_title("給付項目審核")
        except Exception:
            pass
    else:
        # 建立新試算表，放進「保單審核資料庫/待審核/」
        title = f"【待審核】{company} {product} {date_str}"
        print(f"📊 建立試算表：{title}")
        sh = gc.create(title)
        sh.sheet1.update_title("給付項目審核")
        sh.add_worksheet(title="除外責任與限制", rows=100, cols=10)
        sh.add_worksheet(title="理賠必要文件",   rows=100, cols=10)

        # 移到「待審核」資料夾
        print("📁 放入「待審核」資料夾...")
        root_id    = get_or_create_folder(drive, SHEET_FOLDER_NAME)
        pending_id = get_or_create_folder(drive, "待審核", root_id)
        move_file(drive, sh.id, pending_id)

    print("✍️  填入給付項目...")
    build_coverage_sheet(sh, data)

    print("✍️  填入除外責任...")
    build_restrictions_sheet(sh, data)

    print("✍️  填入理賠文件...")
    build_claim_docs_sheet(sh, data)

    url = f"https://docs.google.com/spreadsheets/d/{sh.id}"
    print(f"\n✅ 完成！試算表已放入「待審核」資料夾")
    print(f"   連結：{url}")
    print(f"\n審核完成後執行：")
    print(f"   python3 push_to_sheets.py --archive {sh.id}\n")

    return url


if __name__ == "__main__":
    main()
