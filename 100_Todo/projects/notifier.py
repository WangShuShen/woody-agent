#!/usr/bin/env python3
"""
傳家知保 · 保單審核提醒系統

功能：
  - 將待審核保單平均分配給三位顧問（每人每天 20 份）
  - 截止前 2 小時發送個人化 HTML Email
  - Email 風格與傳家知保網站一致（暖琥珀色系）

用法：
  python3 notifier.py                    # 發送所有待通知顧問
  python3 notifier.py --dry-run          # 預覽，不實際發送
  python3 notifier.py --force            # 強制重送（忽略已通知記錄）
  python3 notifier.py --consultant c1    # 只發給指定顧問
"""

import json
import math
import smtplib
import argparse
from datetime import datetime, date
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

BASE_DIR    = Path(__file__).parent
STATE_PATH  = BASE_DIR / "analyzed_state.json"
CONFIG_PATH = BASE_DIR / "notifier_config.json"


# ── 設定 ──────────────────────────────────────────────────

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise SystemExit(f"❌ 找不到 {CONFIG_PATH.name}")
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


# ── 狀態 ──────────────────────────────────────────────────

def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state: dict):
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def get_uploaded_policies(state: dict) -> list[dict]:
    return [
        {
            "planCode":    k,
            "productName": v.get("productName", k),
            "filename":    v.get("filename", ""),
            "sheetUrl":    v.get("sheetUrl", ""),
            "uploadedAt":  v.get("uploadedAt", ""),
            "notifiedAt":  v.get("notifiedAt"),
        }
        for k, v in state.items()
        if v.get("status") == "uploaded"
    ]


# ── 分配邏輯 ──────────────────────────────────────────────

def assign_policies(policies: list[dict], consultants: list[dict]) -> dict[str, list[dict]]:
    """將保單平均分配給顧問，優先分配尚未通知的"""
    assignment: dict[str, list[dict]] = {c["id"]: [] for c in consultants}

    unnotified = [p for p in policies if not p["notifiedAt"]]
    notified   = [p for p in policies if p["notifiedAt"]]
    all_sorted = unnotified + notified

    quota = consultants[0]["daily_quota"] if consultants else 20

    for i, policy in enumerate(all_sorted):
        idx = i % len(consultants)
        cid = consultants[idx]["id"]
        if len(assignment[cid]) < quota:
            assignment[cid].append(policy)

    return assignment


# ── HTML Email 模板 ───────────────────────────────────────

def build_html_email(consultant: dict, policies: list[dict], deadline_str: str) -> str:
    total     = len(policies)
    notified  = sum(1 for p in policies if p["notifiedAt"])
    pending   = total - notified
    progress  = int((notified / total) * 100) if total else 0

    # 狀態顏色
    if pending == 0:
        status_color = "#16a34a"
        status_bg    = "#f0fdf4"
        status_text  = "✅ 今日任務已完成"
    elif pending <= 5:
        status_color = "#d97706"
        status_bg    = "#fffbeb"
        status_text  = f"⚠️ 剩餘 {pending} 份，請抓緊完成"
    else:
        status_color = "#dc2626"
        status_bg    = "#fef2f2"
        status_text  = f"🔔 今日尚有 {pending} 份待審核"

    # 保單列表 rows
    rows_html = ""
    for i, p in enumerate(policies):
        is_done   = bool(p["notifiedAt"])
        row_bg    = "#f9f6f0" if i % 2 == 0 else "#ffffff"
        badge_bg  = "#dcfce7" if is_done else "#fef3c7"
        badge_color = "#15803d" if is_done else "#92400e"
        badge_text  = "已審核" if is_done else "待審核"
        name      = p["productName"]
        filename  = p["filename"] or "—"
        url       = p["sheetUrl"]
        link      = f'<a href="{url}" style="color:#7B5E2A;font-weight:600;text-decoration:none;">開啟 →</a>' if url else "—"

        rows_html += f"""
        <tr style="background:{row_bg};">
          <td style="padding:12px 16px;font-size:13px;color:#4a3728;border-bottom:1px solid #ede8df;">{i+1}</td>
          <td style="padding:12px 16px;font-size:13px;color:#3c2e1a;font-weight:500;border-bottom:1px solid #ede8df;">{name}</td>
          <td style="padding:12px 16px;font-size:12px;color:#7a6a50;border-bottom:1px solid #ede8df;">{filename}</td>
          <td style="padding:12px 16px;border-bottom:1px solid #ede8df;">
            <span style="background:{badge_bg};color:{badge_color};font-size:11px;font-weight:600;
                         padding:3px 10px;border-radius:20px;">{badge_text}</span>
          </td>
          <td style="padding:12px 16px;font-size:13px;border-bottom:1px solid #ede8df;">{link}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>保單審核提醒</title>
</head>
<body style="margin:0;padding:0;background:#f5f0e8;font-family:system-ui,-apple-system,'Segoe UI',sans-serif;">

<!-- Wrapper -->
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f5f0e8;padding:40px 0;">
<tr><td align="center">
<table width="620" cellpadding="0" cellspacing="0" style="max-width:620px;width:100%;">

  <!-- Header -->
  <tr>
    <td style="background:linear-gradient(135deg,#7B5E2A 0%,#9B7A3A 100%);
               border-radius:16px 16px 0 0;padding:32px 36px 28px;">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td>
            <div style="font-size:11px;letter-spacing:3px;color:rgba(255,255,255,0.7);
                        text-transform:uppercase;margin-bottom:8px;">傳家知保</div>
            <div style="font-size:22px;font-weight:700;color:#ffffff;line-height:1.3;">
              保單審核提醒
            </div>
            <div style="font-size:13px;color:rgba(255,255,255,0.75);margin-top:6px;">
              截止時間：<strong style="color:#fde68a;">{deadline_str}</strong>
            </div>
          </td>
          <td align="right" style="vertical-align:top;">
            <div style="background:rgba(255,255,255,0.15);border-radius:12px;
                        padding:14px 18px;text-align:center;min-width:80px;">
              <div style="font-size:28px;font-weight:800;color:#ffffff;">{pending}</div>
              <div style="font-size:11px;color:rgba(255,255,255,0.75);margin-top:2px;">份待審核</div>
            </div>
          </td>
        </tr>
      </table>
    </td>
  </tr>

  <!-- Body -->
  <tr>
    <td style="background:#ffffff;padding:28px 36px;">

      <!-- 問候 -->
      <p style="margin:0 0 20px;font-size:15px;color:#3c2e1a;">
        Hi，<strong>{consultant['name']}</strong>，
      </p>
      <p style="margin:0 0 24px;font-size:14px;color:#6b5a42;line-height:1.7;">
        今天的保單審核任務共 <strong style="color:#7B5E2A;">{total} 份</strong>，
        距離截止時間還有不到 2 小時，請確認以下保單已完成審核。
      </p>

      <!-- 狀態卡 -->
      <div style="background:{status_bg};border:1px solid;
                  border-color:{status_color}33;border-radius:12px;
                  padding:14px 18px;margin-bottom:24px;">
        <div style="font-size:13px;font-weight:600;color:{status_color};">{status_text}</div>
      </div>

      <!-- 進度條 -->
      <div style="margin-bottom:28px;">
        <div style="display:flex;justify-content:space-between;margin-bottom:8px;">
          <span style="font-size:12px;color:#7a6a50;">審核進度</span>
          <span style="font-size:12px;font-weight:600;color:#7B5E2A;">{notified} / {total}</span>
        </div>
        <div style="background:#ede8df;border-radius:999px;height:8px;overflow:hidden;">
          <div style="background:linear-gradient(90deg,#7B5E2A,#9B7A3A);
                      width:{progress}%;height:100%;border-radius:999px;
                      transition:width 0.3s;"></div>
        </div>
      </div>

      <!-- 保單列表 -->
      <div style="border:1px solid #ede8df;border-radius:12px;overflow:hidden;margin-bottom:28px;">
        <table width="100%" cellpadding="0" cellspacing="0">
          <thead>
            <tr style="background:#faf6f0;">
              <th style="padding:11px 16px;font-size:11px;color:#9a8a70;font-weight:600;
                         text-align:left;border-bottom:1px solid #ede8df;width:36px;">#</th>
              <th style="padding:11px 16px;font-size:11px;color:#9a8a70;font-weight:600;
                         text-align:left;border-bottom:1px solid #ede8df;">商品名稱</th>
              <th style="padding:11px 16px;font-size:11px;color:#9a8a70;font-weight:600;
                         text-align:left;border-bottom:1px solid #ede8df;">保單代碼</th>
              <th style="padding:11px 16px;font-size:11px;color:#9a8a70;font-weight:600;
                         text-align:left;border-bottom:1px solid #ede8df;">狀態</th>
              <th style="padding:11px 16px;font-size:11px;color:#9a8a70;font-weight:600;
                         text-align:left;border-bottom:1px solid #ede8df;">審核</th>
            </tr>
          </thead>
          <tbody>
            {rows_html}
          </tbody>
        </table>
      </div>

      <!-- CTA -->
      <div style="text-align:center;margin-bottom:8px;">
        <a href="https://drive.google.com" target="_blank"
           style="display:inline-block;background:linear-gradient(135deg,#7B5E2A,#9B7A3A);
                  color:#ffffff;font-size:14px;font-weight:600;text-decoration:none;
                  padding:13px 32px;border-radius:10px;letter-spacing:0.5px;">
          前往 Google Drive 審核資料夾 →
        </a>
      </div>

    </td>
  </tr>

  <!-- Footer -->
  <tr>
    <td style="background:#faf6f0;border-radius:0 0 16px 16px;
               padding:20px 36px;border-top:1px solid #ede8df;">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td>
            <div style="font-size:11px;color:#9a8a70;line-height:1.6;">
              此郵件由 <strong style="color:#7B5E2A;">傳家知保</strong> 自動發送
              &nbsp;·&nbsp; {date.today().strftime('%Y/%m/%d')}
            </div>
          </td>
          <td align="right">
            <div style="font-size:11px;color:#c4b49a;">傳家知保 保單審核系統</div>
          </td>
        </tr>
      </table>
    </td>
  </tr>

</table>
</td></tr>
</table>

</body>
</html>"""
    return html


# ── 發送 ──────────────────────────────────────────────────

def send_email(consultant: dict, policies: list[dict], config: dict,
               deadline_str: str, dry_run: bool = False) -> bool:
    html    = build_html_email(consultant, policies, deadline_str)
    pending = sum(1 for p in policies if not p["notifiedAt"])
    subject = f"【傳家知保】{consultant['name']} 今日保單審核提醒｜剩餘 {pending} 份待完成"

    if dry_run:
        preview_path = BASE_DIR / f"preview_{consultant['id']}.html"
        preview_path.write_text(html, encoding="utf-8")
        print(f"   📄 預覽已存：{preview_path.name}")
        print(f"   主旨：{subject}")
        return True

    cfg = config["email"]
    msg = MIMEMultipart("alternative")
    msg["From"]    = f"{cfg['sender_name']} <{cfg['sender']}>"
    msg["To"]      = consultant["email"]
    msg["Subject"] = subject
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"]) as server:
            server.starttls()
            server.login(cfg["sender"], cfg["password"])
            server.sendmail(cfg["sender"], [consultant["email"]], msg.as_string())
        print(f"   ✅ 已發送 → {consultant['name']} ({consultant['email']})")
        return True
    except Exception as e:
        print(f"   ❌ 發送失敗：{e}")
        return False


# ── 主流程 ────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="傳家知保 · 保單審核提醒系統")
    parser.add_argument("--dry-run",     action="store_true", help="輸出 HTML 預覽，不實際發送")
    parser.add_argument("--force",       action="store_true", help="強制重送已通知的顧問")
    parser.add_argument("--consultant",  help="只發給指定顧問 ID（例如 c1）")
    args = parser.parse_args()

    config      = load_config()
    state       = load_state()
    consultants = config["consultants"]
    policies    = get_uploaded_policies(state)

    if not policies:
        print("✅ 目前 analyzed_state.json 中沒有 uploaded 保單，無需發送。")
        return

    # 計算截止時間
    deadline_hour = config.get("deadline_hour", 0)
    now           = datetime.now()
    if deadline_hour == 0:
        deadline_str = "今日 24:00（明日零時）"
    else:
        deadline_str = now.replace(hour=deadline_hour, minute=0, second=0).strftime("今日 %H:%M")

    # 分配保單
    assignment = assign_policies(policies, consultants)

    # 過濾顧問
    targets = [c for c in consultants if not args.consultant or c["id"] == args.consultant]

    print(f"\n📋 保單總數：{len(policies)} 份  ·  顧問：{len(targets)} 位  ·  截止：{deadline_str}")
    print("─" * 56)

    now_str  = datetime.now().isoformat(timespec="seconds")
    any_sent = False

    for consultant in targets:
        assigned = assignment[consultant["id"]]
        pending  = sum(1 for p in assigned if not p["notifiedAt"])

        print(f"\n👤 {consultant['name']}  分配 {len(assigned)} 份  |  待審核 {pending} 份")

        if not args.force and pending == 0:
            print("   ⏭️  已全部審核完畢，跳過")
            continue

        success = send_email(consultant, assigned, config, deadline_str, dry_run=args.dry_run)

        if success and not args.dry_run:
            for p in assigned:
                if not p["notifiedAt"]:
                    state[p["planCode"]]["notifiedAt"]    = now_str
                    state[p["planCode"]]["notifyChannel"] = "email"
                    state[p["planCode"]]["notifiedTo"]    = consultant["id"]
            save_state(state)
            any_sent = True

    print(f"\n{'═' * 56}")
    if args.dry_run:
        print("Dry Run 完成！HTML 預覽已存在同一資料夾，用瀏覽器開啟確認效果。")
    elif any_sent:
        print("所有提醒發送完畢，analyzed_state.json 已更新。")
    else:
        print("無需發送（所有顧問的保單均已審核完畢）。")


if __name__ == "__main__":
    main()
