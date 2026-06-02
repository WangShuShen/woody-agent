#!/usr/bin/env python3
"""
保單資料庫 → Google Sheets 自動推送腳本
使用方式：
  python3 push_to_sheets.py <policy_json_file>
  python3 push_to_sheets.py --stdin   (從 stdin 讀 JSON)

JSON 格式參考 policy-db-extract 輸出的資料庫 JSON 區塊。
"""

import sys
import json
import argparse
from datetime import datetime
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

# ── 設定 ──────────────────────────────────────────
SERVICE_ACCOUNT_FILE = Path(__file__).parent.parent.parent / "000_Agent" / "service_account.json"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
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


def connect():
    """建立 Google Sheets 連線"""
    if not SERVICE_ACCOUNT_FILE.exists():
        print(f"❌ 找不到 Service Account 金鑰：{SERVICE_ACCOUNT_FILE}")
        print("請確認已將 service_account.json 放到 000_Agent/ 資料夾")
        sys.exit(1)
    creds = Credentials.from_service_account_file(str(SERVICE_ACCOUNT_FILE), scopes=SCOPES)
    return gspread.authorize(creds), creds.service_account_email


def get_or_create_folder(drive_service, folder_name):
    """在 Google Drive 找到或建立資料夾，回傳資料夾 ID"""
    # gspread 不直接操作 Drive，用 requests 搭配 token
    import requests
    token = drive_service.auth.token
    headers = {"Authorization": f"Bearer {token}"}

    # 搜尋資料夾
    q = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    r = requests.get(
        "https://www.googleapis.com/drive/v3/files",
        params={"q": q, "fields": "files(id,name)"},
        headers=headers,
    )
    files = r.json().get("files", [])
    if files:
        return files[0]["id"]

    # 建立資料夾
    r = requests.post(
        "https://www.googleapis.com/drive/v3/files",
        headers={**headers, "Content-Type": "application/json"},
        json={"name": folder_name, "mimeType": "application/vnd.google-apps.folder"},
    )
    return r.json()["id"]


def create_spreadsheet(gc, title):
    """建立新試算表"""
    sh = gc.create(title)
    return sh


def share_with_anyone(sh):
    """設定任何人都可以查看（方便顧問開啟連結）"""
    sh.share(None, perm_type="anyone", role="writer")


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


# ── 分頁 1：給付項目審核 ──────────────────────────

def build_coverage_sheet(sh, data):
    ws = sh.worksheet("給付項目審核")
    sheet_id = ws.id

    company = data.get("company", "—")
    product = data.get("productName", "—")
    plan    = data.get("planCode", "—") or "—"
    base    = data.get("baseType", "—")
    date    = data.get("extractedAt", datetime.today().strftime("%Y-%m-%d"))
    items   = data.get("items", [])
    limit   = data.get("annualLimit", {})

    # ── 寫入文字資料 ──
    header1 = f"【{data.get('insuranceType', ['—'])[0]}】{company}　｜　{product}　｜　{plan}"
    header2 = f"保額基礎：{base}　｜　狀態：待審核　｜　提取日期：{date}"
    col_headers = ["給付項目", "公式", "限制條件", "注意事項", "✅ 狀態", "📝 審核備注"]

    rows = [[header1], [header2], [], col_headers]
    for item in items:
        rows.append([
            item.get("name", ""),
            item.get("formula", "") + (item.get("unit", "") or ""),
            item.get("restriction", "") or "—",
            item.get("notes", "") or "—",
            "待審核",
            "",
        ])
    rows.append([])  # 空行
    rows.append([
        "⚠️ 累積給付上限",
        limit.get("formula", ""),
        "所有給付項目合計",
        limit.get("notes", ""),
        "待審核",
        "",
    ])

    ws.update(rows, "A1", value_input_option="USER_ENTERED")

    # ── 合併標題欄 ──
    requests = []
    for r in [0, 1, 2]:
        requests.append({"mergeCells": {
            "range": block_range(sheet_id, r, r + 1, 0, 6),
            "mergeType": "MERGE_ALL",
        }})

    # ── 格式：標題行 ──
    def fmt_row(row, bg, fg, size, bold):
        return {"repeatCell": {
            "range": row_range(sheet_id, row),
            "cell": {"userEnteredFormat": cell_format(bg, fg, size, bold)},
            "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment,wrapStrategy)",
        }}

    requests += [
        fmt_row(0, COLOR["dark_navy"], COLOR["white"], 13, True),
        fmt_row(1, COLOR["navy"],      COLOR["gray_light"], 10, False),
        fmt_row(3, COLOR["navy"],      COLOR["white"], 11, True),
    ]

    # ── 格式：資料行斑馬紋 ──
    for i, _ in enumerate(items):
        bg = COLOR["gray_light"] if i % 2 == 0 else COLOR["white"]
        requests.append({"repeatCell": {
            "range": row_range(sheet_id, 4 + i),
            "cell": {"userEnteredFormat": {"backgroundColor": bg, "textFormat": {"fontSize": 10},
                                            "verticalAlignment": "MIDDLE", "wrapStrategy": "WRAP"}},
            "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment,wrapStrategy)",
        }})

    # ── 格式：累積上限行 ──
    limit_row = 4 + len(items) + 1
    requests.append(fmt_row(limit_row, COLOR["gold"], COLOR["dark_navy"], 10, True))

    # ── 欄寬 ──
    col_widths = [220, 120, 130, 210, 90, 200]
    for i, w in enumerate(col_widths):
        requests.append({"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                      "startIndex": i, "endIndex": i + 1},
            "properties": {"pixelSize": w},
            "fields": "pixelSize",
        }})

    # ── 凍結前4列 ──
    requests.append({"updateSheetProperties": {
        "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 4}},
        "fields": "gridProperties.frozenRowCount",
    }})

    # ── 框線 ──
    requests.append({"updateBorders": {
        "range": block_range(sheet_id, 3, limit_row + 1),
        **border_style(),
    }})

    # ── 下拉選單：狀態欄 ──
    status_rows = len(items) + 1  # 資料行 + 累積上限行
    requests.append({"setDataValidation": {
        "range": block_range(sheet_id, 4, 4 + status_rows, 4, 5),
        "rule": {
            "condition": {"type": "ONE_OF_LIST",
                          "values": [{"userEnteredValue": v} for v in ["待審核", "已審核", "有疑問"]]},
            "showCustomUi": True,
            "strict": True,
        },
    }})

    # ── 條件格式：狀態顏色 ──
    status_range = block_range(sheet_id, 4, 4 + status_rows, 4, 5)
    for text, bg, fg in [
        ("已審核", COLOR["green_pale"], {"red": 0.08, "green": 0.34, "blue": 0.14}),
        ("有疑問", COLOR["red_pale"],   {"red": 0.44, "green": 0.11, "blue": 0.11}),
        ("待審核", COLOR["yellow_pale"],{"red": 0.52, "green": 0.39, "blue": 0.02}),
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

def build_restrictions_sheet(sh, data):
    ws = sh.worksheet("除外責任與限制")
    sheet_id = ws.id

    wp   = data.get("waitingPeriod", {})
    excl = data.get("exclusions", [])
    spec = data.get("specialRestrictions", [])

    rows = [
        ["理賠條件與限制　｜　" + data.get("productName", "")],
        [],
        ["⏳ 等待期", "類型", "天數"],
        ["", "疾病", f"{wp.get('disease', 30)}天"],
        ["", "傷害意外", "無" if wp.get("injury", 0) == 0 else f"{wp.get('injury')}天"],
        ["", "增加保額部分", wp.get("note", "另計30天")],
        [],
        [],
        ["🚫 除外責任"],
    ]
    for e in excl:
        rows.append(["✗", e])
    rows.append([])
    rows.append(["⚠️ 特別注意"])
    for s in spec:
        rows.append(["✗", s])

    ws.update(rows, "A1", value_input_option="USER_ENTERED")

    requests = []

    # 合併標題
    requests.append({"mergeCells": {
        "range": block_range(sheet_id, 0, 1, 0, 3),
        "mergeType": "MERGE_ALL",
    }})

    def fmt_row(row, bg, fg, size=10, bold=True):
        return {"repeatCell": {
            "range": block_range(sheet_id, row, row + 1, 0, 3),
            "cell": {"userEnteredFormat": cell_format(bg, fg, size, bold)},
            "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment,wrapStrategy)",
        }}

    excl_start = 8
    note_start = excl_start + len(excl) + 2

    requests += [
        fmt_row(0, COLOR["dark_navy"], COLOR["white"], 13),
        fmt_row(2, COLOR["navy"],      COLOR["white"]),
        fmt_row(excl_start, COLOR["red"], COLOR["white"]),
        fmt_row(note_start, COLOR["orange"], COLOR["white"]),
    ]

    # 等待期淡藍背景
    requests.append({"repeatCell": {
        "range": block_range(sheet_id, 3, 6, 0, 3),
        "cell": {"userEnteredFormat": {"backgroundColor": {"red": 0.94, "green": 0.96, "blue": 1.0},
                                        "textFormat": {"fontSize": 10}, "verticalAlignment": "MIDDLE"}},
        "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment)",
    }})

    # 除外責任字色
    requests.append({"repeatCell": {
        "range": block_range(sheet_id, excl_start + 1, excl_start + 1 + len(excl), 0, 3),
        "cell": {"userEnteredFormat": {"textFormat": {"foregroundColor": COLOR["red"], "fontSize": 10},
                                        "verticalAlignment": "MIDDLE"}},
        "fields": "userEnteredFormat(textFormat,verticalAlignment)",
    }})

    # 特別注意字色
    requests.append({"repeatCell": {
        "range": block_range(sheet_id, note_start + 1, note_start + 1 + len(spec), 0, 3),
        "cell": {"userEnteredFormat": {"textFormat": {"foregroundColor": COLOR["orange"], "fontSize": 10},
                                        "verticalAlignment": "MIDDLE"}},
        "fields": "userEnteredFormat(textFormat,verticalAlignment)",
    }})

    col_widths = [40, 430, 110]
    for i, w in enumerate(col_widths):
        requests.append({"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                      "startIndex": i, "endIndex": i + 1},
            "properties": {"pixelSize": w}, "fields": "pixelSize",
        }})

    requests.append({"updateSheetProperties": {
        "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
        "fields": "gridProperties.frozenRowCount",
    }})

    sh.batch_update({"requests": requests})


# ── 分頁 3：理賠必要文件 ──────────────────────────

def build_claim_docs_sheet(sh, data):
    ws = sh.worksheet("理賠必要文件")
    sheet_id = ws.id

    # 固定文件結構（依保單類型可擴充）
    sections = [
        ("🏥 一般住院／加護病房", [
            "保險金申請書",
            "被保險人身分證影本",
            "醫療診斷書（含住院原因/入出院日期）",
            "加護病房住院證明（載明起訖日期）",
            "受益人身分證影本",
            "受益人存摺封面影本",
        ]),
        ("🔪 重大手術", [
            "保險金申請書",
            "被保險人身分證影本",
            "醫療診斷書（載明手術名稱及日期）",
            "手術紀錄書",
            "開刀房紀錄（含麻醉方式）",
            "受益人身分證影本",
            "受益人存摺封面影本",
        ]),
        ("🚪 住院前後門診", [
            "保險金申請書",
            "門診收據（含就診日期/診斷）",
            "住院診斷書（證明同一疾病/傷害）",
            "受益人身分證影本",
            "受益人存摺封面影本",
        ]),
        ("💊 出院療養", [
            "保險金申請書",
            "出院診斷書（含出院日期及住院日數）",
            "受益人身分證影本",
            "受益人存摺封面影本",
        ]),
        ("🕊️ 身故／喪葬費用", [
            "保險金申請書",
            "保險單或其謄本",
            "死亡診斷書（或相驗屍體證明書）",
            "除戶戶籍謄本",
            "受益人戶籍謄本",
            "受益人身分證影本",
            "受益人存摺封面影本",
        ]),
        ("🚑 緊急醫療運送", [
            "保險金申請書",
            "救護車費用收據",
            "急診或住院證明",
            "受益人身分證影本",
            "受益人存摺封面影本",
        ]),
        ("💔 重大疾病確診（特別看護觸發）", [
            "診斷書（載明重大疾病種類）",
            "心肌梗塞：心電圖＋心肌酶報告",
            "冠狀動脈繞道：心導管報告＋手術紀錄",
            "腦中風：腦部影像（CT/MRI）",
            "慢性腎衰竭：腎功能檢查＋洗腎紀錄",
            "癌症：病理組織切片報告",
            "癱瘓：神經科專科醫師診斷書",
            "器官移植：移植手術紀錄",
        ]),
    ]

    header = ["理賠必要文件　｜　" + data.get("productName", "")]
    all_rows = [header, []]

    # 每4個 section 換行
    section_cols = 4
    for batch_start in range(0, len(sections), section_cols):
        batch = sections[batch_start: batch_start + section_cols]
        max_docs = max(len(s[1]) for s in batch)
        title_row = [s[0] for s in batch] + [""] * (section_cols - len(batch))
        all_rows.append(title_row)
        for i in range(max_docs):
            row = []
            for sec in batch:
                row.append("· " + sec[1][i] if i < len(sec[1]) else "")
            row += [""] * (section_cols - len(batch))
            all_rows.append(row)
        all_rows.append([])

    ws.update(all_rows, "A1", value_input_option="USER_ENTERED")

    requests = []
    # 標題合併
    requests.append({"mergeCells": {
        "range": block_range(sheet_id, 0, 1, 0, section_cols),
        "mergeType": "MERGE_ALL",
    }})
    requests.append({"repeatCell": {
        "range": block_range(sheet_id, 0, 1, 0, section_cols),
        "cell": {"userEnteredFormat": cell_format(COLOR["dark_navy"], COLOR["white"], 13, True)},
        "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment,wrapStrategy)",
    }})

    sec_colors = [
        COLOR["green_dark"], COLOR["navy"], {"red": 0.42, "green": 0.11, "blue": 0.11},
        {"red": 0.29, "green": 0.11, "blue": 0.42},
        {"red": 0.49, "green": 0.11, "blue": 0.11}, COLOR["navy"],
        {"red": 0.35, "green": 0.11, "blue": 0.49},
    ]

    row_cursor = 2
    for batch_start in range(0, len(sections), section_cols):
        batch = sections[batch_start: batch_start + section_cols]
        max_docs = max(len(s[1]) for s in batch)
        # 分類標題行
        for col_i, (sec, _) in enumerate(batch):
            color = sec_colors[(batch_start + col_i) % len(sec_colors)]
            requests.append({"repeatCell": {
                "range": block_range(sheet_id, row_cursor, row_cursor + 1, col_i, col_i + 1),
                "cell": {"userEnteredFormat": cell_format(color, COLOR["white"], 10, True)},
                "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment,wrapStrategy)",
            }})
        # 資料行斑馬紋
        for i in range(max_docs):
            bg = COLOR["gray_light"] if i % 2 == 0 else COLOR["white"]
            requests.append({"repeatCell": {
                "range": block_range(sheet_id, row_cursor + 1 + i, row_cursor + 2 + i, 0, section_cols),
                "cell": {"userEnteredFormat": {"backgroundColor": bg, "textFormat": {"fontSize": 10},
                                               "verticalAlignment": "MIDDLE", "wrapStrategy": "WRAP"}},
                "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment,wrapStrategy)",
            }})
        row_cursor += max_docs + 2

    # 欄寬
    for i in range(section_cols):
        requests.append({"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                      "startIndex": i, "endIndex": i + 1},
            "properties": {"pixelSize": 240}, "fields": "pixelSize",
        }})

    requests.append({"updateSheetProperties": {
        "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
        "fields": "gridProperties.frozenRowCount",
    }})

    sh.batch_update({"requests": requests})


# ── 主流程 ────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="推送保單 JSON 到 Google Sheets")
    parser.add_argument("json_file", nargs="?", help="policy-db-extract 輸出的 JSON 檔案路徑")
    parser.add_argument("--sheet-id", help="已建立的 Google Sheet ID（從網址複製）")
    parser.add_argument("--stdin", action="store_true", help="從 stdin 讀取 JSON")
    args = parser.parse_args()

    # 讀取 JSON
    if args.stdin:
        data = json.load(sys.stdin)
    elif args.json_file:
        with open(args.json_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        parser.print_help()
        sys.exit(1)

    print(f"📋 保單：{data.get('company')} / {data.get('productName')}")
    print("🔌 連線 Google Sheets...")
    gc, sa_email = connect()

    if args.sheet_id:
        # 寫入現有試算表
        print(f"📂 開啟現有試算表：{args.sheet_id}")
        sh = gc.open_by_key(args.sheet_id)
        # 清除並重建分頁
        existing = [ws.title for ws in sh.worksheets()]
        for title in ["給付項目審核", "除外責任與限制", "理賠必要文件"]:
            if title not in existing:
                sh.add_worksheet(title=title, rows=100, cols=10)
        # 把預設的 Sheet1 重命名（如果還在）
        try:
            sh.sheet1.update_title("給付項目審核")
        except Exception:
            pass
    else:
        # 建立新試算表
        title = f"【審核】{data.get('company', '')} {data.get('productName', '')} {datetime.today().strftime('%Y%m%d')}"
        print(f"📊 建立試算表：{title}")
        sh = gc.create(title)
        sh.sheet1.update_title("給付項目審核")
        sh.add_worksheet(title="除外責任與限制", rows=100, cols=10)
        sh.add_worksheet(title="理賠必要文件",   rows=100, cols=10)

    print("✍️  填入給付項目...")
    build_coverage_sheet(sh, data)

    print("✍️  填入除外責任...")
    build_restrictions_sheet(sh, data)

    print("✍️  填入理賠文件...")
    build_claim_docs_sheet(sh, data)

    url = f"https://docs.google.com/spreadsheets/d/{sh.id}"
    print(f"\n✅ 完成！試算表連結：")
    print(f"   {url}\n")

    return url


if __name__ == "__main__":
    main()
