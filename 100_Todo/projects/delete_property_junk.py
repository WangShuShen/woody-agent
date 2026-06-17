#!/usr/bin/env python3
"""刪除誤抓的產險商品 Drive 資料夾（依 junk_property_records.json）"""
import json, time
from pathlib import Path
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

ROOT = Path(__file__).parent.parent.parent
creds = Credentials.from_authorized_user_file(
    str(ROOT/"000_Agent"/"authorized_user.json"), ["https://www.googleapis.com/auth/drive"])
drive = build("drive","v3",credentials=creds)
DRIVE_ROOT="保單原始條款"

_cache={}
def find(name,parent):
    key=f"{parent}/{name}"
    if key in _cache: return _cache[key]
    safe=name.replace("'","\\'")
    r=drive.files().list(q=f"name='{safe}' and '{parent}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
                         fields="files(id)").execute().get("files",[])
    fid=r[0]["id"] if r else None
    _cache[key]=fid
    return fid

def delete(fid):
    for _ in range(3):
        try: drive.files().delete(fileId=fid).execute(); return True
        except Exception: time.sleep(3)
    return False

def main():
    junk=json.loads((Path(__file__).parent/"junk_property_records.json").read_text("utf-8"))
    # 找根
    r=drive.files().list(q=f"name='{DRIVE_ROOT}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
                         fields="files(id)").execute().get("files",[])
    root=r[0]["id"]
    # 去重到 (company,productType,contractType,productFolder)
    folders=set()
    for v in junk.values():
        folders.add((v["company"],v["productType"],v["contractType"],v["productFolder"]))
    print(f"待刪 {len(junk)} 筆 → {len(folders)} 個唯一商品資料夾")
    deleted=0; notfound=0
    for co,pt,ct,pf in sorted(folders):
        cid=find(co,root)
        if not cid: notfound+=1; continue
        ptid=find(pt,cid)
        if not ptid: notfound+=1; continue
        ctid=find(ct,ptid)
        if not ctid: notfound+=1; continue
        pfid=find(pf,ctid)
        if not pfid: notfound+=1; continue
        if delete(pfid):
            deleted+=1
            print(f"  🗑️  {co[:6]}/{pt}/{ct}/{pf[:30]}")
        time.sleep(0.2)
    print(f"\n✅ 刪除 {deleted} 個產險資料夾，找不到 {notfound} 個")

if __name__=="__main__":
    main()
