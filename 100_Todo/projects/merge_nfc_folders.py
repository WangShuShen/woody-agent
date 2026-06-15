#!/usr/bin/env python3
"""
合併因 Unicode 正規化(NFC/NFD)差異被拆成兩個的 Drive 資料夾：
  以 NFC 名稱為準，把 NFD 資料夾的 vN 子夾與 PDF 移入 NFC 資料夾，刪除空的 NFD 夾。
"""
import time
import unicodedata
from pathlib import Path
from collections import defaultdict
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

ROOT = Path(__file__).parent.parent.parent
creds = Credentials.from_authorized_user_file(
    str(ROOT / "000_Agent" / "authorized_user.json"),
    ["https://www.googleapis.com/auth/drive"])
drive = build("drive", "v3", credentials=creds)

TARGETS = [
    "南山人壽不分紅康壽終身壽險",
    "南山人壽鑫美多利外幣利率變動型年金保險(甲型)",
    "南山人壽呵護倍至醫療終身保險",
    "南山人壽月月得利利率變動型養老保險",
    "南山人壽安心傷害醫療費用給付附加條款",
    "國泰人壽眷屬身故前未給付醫療保險金受益人指定批註條款",
]


def children(fid, only_folders=False, only_files=False):
    q = f"'{fid}' in parents and trashed=false"
    if only_folders:
        q += " and mimeType='application/vnd.google-apps.folder'"
    if only_files:
        q += " and mimeType!='application/vnd.google-apps.folder'"
    return drive.files().list(q=q, fields="files(id,name,mimeType,parents)", pageSize=500).execute().get("files", [])


def move(fid, frm, to):
    for _ in range(3):
        try:
            drive.files().update(fileId=fid, addParents=to, removeParents=frm, fields="id").execute()
            return True
        except Exception as e:
            time.sleep(3)
    print(f"      ❌ 移動失敗 {fid}")
    return False


def delete(fid):
    for _ in range(3):
        try:
            drive.files().delete(fileId=fid).execute()
            return
        except Exception:
            time.sleep(3)


def main():
    for t in TARGETS:
        res = drive.files().list(
            q=f"name contains '{t[:9]}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
            fields="files(id,name,parents)", pageSize=200).execute()
        matches = [f for f in res.get("files", [])
                   if unicodedata.normalize("NFC", f["name"]) == unicodedata.normalize("NFC", t)]
        if len(matches) < 2:
            print(f"⏭️  {t[:24]}：只有 {len(matches)} 個，無需合併")
            continue

        nfc = next((f for f in matches if f["name"] == unicodedata.normalize("NFC", f["name"])), None)
        nfds = [f for f in matches if f is not nfc]
        if not nfc:
            # 都不是 NFC，挑第一個改名為 NFC
            nfc = matches[0]
            drive.files().update(fileId=nfc["id"],
                                 body={"name": unicodedata.normalize("NFC", nfc["name"])}).execute()
            nfds = matches[1:]

        print(f"\n🔀 合併 {t[:24]}  (保留 {nfc['id'][:10]})")
        nfc_subs = {s["name"]: s["id"] for s in children(nfc["id"], only_folders=True)}

        for nfd in nfds:
            for sub in children(nfd["id"], only_folders=True):
                vname = sub["name"]
                if vname in nfc_subs:
                    # 目標已有同名 vN：移檔案進去
                    dest = nfc_subs[vname]
                    existing = {f["name"] for f in children(dest, only_files=True)}
                    for f in children(sub["id"], only_files=True):
                        if f["name"] not in existing:
                            move(f["id"], sub["id"], dest)
                            print(f"      → {vname}/{f['name']}")
                    # 移完刪空 vN
                    if not children(sub["id"]):
                        delete(sub["id"])
                else:
                    # 整個 vN 子夾搬過去
                    move(sub["id"], nfd["id"], nfc["id"])
                    nfc_subs[vname] = sub["id"]
                    print(f"      → 整夾搬移 {vname}")
            # NFD 夾若已空就刪
            if not children(nfd["id"]):
                delete(nfd["id"])
                print(f"      ✅ 已刪空的 NFD 資料夾 {nfd['id'][:10]}")
            else:
                print(f"      ⚠️  NFD 夾仍有殘留，未刪 {nfd['id'][:10]}")

    print("\n🏁 NFC 資料夾合併完成")


if __name__ == "__main__":
    main()
