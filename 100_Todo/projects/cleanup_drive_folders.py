#!/usr/bin/env python3
"""
清理 Drive 中錯誤命名的商品資料夾：
  (第N次部分變更) 應在 vN 層，不應出現在商品資料夾名稱裡。

支援：阿拉伯/中文數字版次、全形括號、缺「次」的格式。
"""
import json, re, sys, time
from pathlib import Path
from collections import defaultdict
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

ROOT = Path(__file__).parent.parent.parent
REGISTRY_PATH = Path(__file__).parent / "drive_registry.json"
AUTHORIZED_FILE = ROOT / "000_Agent" / "authorized_user.json"

_CH_NUMS = {
    '一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10,
    '十一':11,'十二':12,'十三':13,'十四':14,'十五':15,'十六':16,'十七':17,
    '十八':18,'十九':19,'二十':20,
}
_VER_RE = re.compile(r'[\(（]第([一二三四五六七八九十\d]+)次?部[分份]變更[\)）]')

def ver_num(name):
    m = _VER_RE.search(name)
    if not m: return None
    t = m.group(1)
    return int(t) if t.isdigit() else _CH_NUMS.get(t)

def base_name(name):
    return _VER_RE.sub('', name).strip()

_cache = {}
def find_folder(drive, name, parent_id):
    key = f"{parent_id}/{name}"
    if key in _cache: return _cache[key]
    safe = name.replace("'", "\\'")
    res = drive.files().list(
        q=f"name='{safe}' and '{parent_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
        fields="files(id)"
    ).execute()
    fid = res['files'][0]['id'] if res['files'] else None
    _cache[key] = fid
    return fid

def get_or_create_folder(drive, name, parent_id):
    fid = find_folder(drive, name, parent_id)
    if fid: return fid
    f = drive.files().create(
        body={"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]},
        fields="id"
    ).execute()
    fid = f["id"]
    _cache[f"{parent_id}/{name}"] = fid
    return fid

def list_children(drive, folder_id):
    res = drive.files().list(
        q=f"'{folder_id}' in parents and trashed=false",
        fields="files(id,name,mimeType)",
        pageSize=500
    ).execute()
    return res.get('files', [])

def list_files(drive, folder_id):
    return [f for f in list_children(drive, folder_id)
            if f['mimeType'] != 'application/vnd.google-apps.folder']

def move_file(drive, file_id, from_id, to_id):
    for attempt in range(3):
        try:
            drive.files().update(
                fileId=file_id, addParents=to_id, removeParents=from_id, fields="id"
            ).execute()
            return
        except Exception as e:
            if attempt < 2: time.sleep(3)
            else: raise

def delete_folder(drive, folder_id):
    for attempt in range(3):
        try:
            drive.files().delete(fileId=folder_id).execute()
            return
        except Exception as e:
            if attempt < 2: time.sleep(3)
            else: print(f"   ⚠️  刪除失敗：{e}")

def get_folder_parent(drive, folder_id):
    res = drive.files().get(fileId=folder_id, fields="parents").execute()
    return res.get('parents', [None])[0]

def run(dry_run=False):
    print("🔌 連線 Google Drive...")
    creds = Credentials.from_authorized_user_file(str(AUTHORIZED_FILE), ["https://www.googleapis.com/auth/drive"])
    drive = build("drive", "v3", credentials=creds)

    # 讀取 registry
    registry = json.loads(REGISTRY_PATH.read_text('utf-8'))

    # 用 API 搜尋所有含「部分變更」或「部份變更」的資料夾
    print("搜尋含版次的錯誤資料夾...")
    wrong_folders_raw = []
    for keyword in ["部分變更", "部份變更"]:
        page_token = None
        while True:
            params = dict(
                q=f"name contains '{keyword}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
                fields="nextPageToken,files(id,name,parents)",
                pageSize=1000,
            )
            if page_token: params['pageToken'] = page_token
            res = drive.files().list(**params).execute()
            wrong_folders_raw.extend(res.get('files', []))
            page_token = res.get('nextPageToken')
            if not page_token: break
    # 去重（同一資料夾可能被兩個 keyword 匹配）
    seen = set()
    wrong_folders = []
    for f in wrong_folders_raw:
        if f['id'] not in seen:
            seen.add(f['id'])
            wrong_folders.append(f)

    print(f"找到 {len(wrong_folders)} 個錯誤資料夾")

    fixed = 0
    errors = 0

    for wf in wrong_folders:
        name    = wf['name']
        wf_id   = wf['id']
        parent_id = wf.get('parents', [None])[0]
        if not parent_id:
            parent_id = get_folder_parent(drive, wf_id)

        correct_base = base_name(name)
        correct_ver  = ver_num(name)
        if correct_ver is None:
            print(f"⚠️  無法解析版次，跳過：{name}")
            continue

        print(f"\n[{'DRY' if dry_run else 'FIX'}] {name}")
        print(f"  → 目標：{correct_base} / v{correct_ver}")

        # 列出錯誤資料夾的子內容（version 層）
        children = list_children(drive, wf_id)
        if not children:
            print(f"  空資料夾，直接刪除")
            if not dry_run:
                delete_folder(drive, wf_id)
            fixed += 1
            continue

        # 找或建正確的 base 資料夾（在同一 parent 下）
        if not dry_run:
            correct_base_id = get_or_create_folder(drive, correct_base, parent_id)
        else:
            correct_base_id = f"[DRY:{correct_base}]"

        for child in children:
            child_name = child['name']
            child_id   = child['id']

            # 判斷這個子資料夾代表哪個版次
            if re.match(r'v\d+$', child_name):
                src_ver = int(child_name[1:])
            elif re.match(r'v\d+', child_name):
                # 舊格式：v00 原始版... → ver 0
                src_ver = 0
            else:
                src_ver = 0

            # 目標版次：使用錯誤資料夾名稱的版次（因為 child 可能是錯誤的 v0）
            dest_ver = correct_ver

            print(f"  子資料夾：{child_name}（src v{src_ver}） → 目標 v{dest_ver}")

            # 找 PDFs in child
            pdfs = list_files(drive, child_id)
            if not pdfs:
                print(f"  無 PDF，刪除空子資料夾")
                if not dry_run:
                    delete_folder(drive, child_id)
                continue

            print(f"  PDFs：{[p['name'] for p in pdfs]}")

            # 建正確的 vN 資料夾
            if not dry_run:
                dest_ver_id = get_or_create_folder(drive, f"v{dest_ver}", correct_base_id)
            else:
                dest_ver_id = f"[DRY:v{dest_ver}]"

            # 檢查目標是否已有同名檔案
            if not dry_run:
                existing = {f['name'] for f in list_files(drive, dest_ver_id)}
            else:
                existing = set()

            for pdf in pdfs:
                if pdf['name'] in existing:
                    print(f"    ⏭  {pdf['name']} 已存在，跳過")
                else:
                    print(f"    → 移動 {pdf['name']}")
                    if not dry_run:
                        try:
                            move_file(drive, pdf['id'], child_id, dest_ver_id)
                        except Exception as e:
                            print(f"    ❌ 移動失敗：{e}")
                            errors += 1
                            continue

            # 刪子資料夾（若已空）
            if not dry_run:
                remaining = list_files(drive, child_id)
                if not remaining:
                    delete_folder(drive, child_id)

        # 刪錯誤 base 資料夾（若已空）
        if not dry_run:
            remaining_children = list_children(drive, wf_id)
            if not remaining_children:
                delete_folder(drive, wf_id)
                print(f"  ✅ 已刪除舊資料夾：{name}")

        fixed += 1
        time.sleep(0.3)

    print(f"\n{'[DRY RUN] ' if dry_run else ''}✅ 完成：處理 {fixed} 個，錯誤 {errors} 個")

    # 更新 registry
    if not dry_run:
        print("\n更新 registry...")
        updated = 0
        for pid, v in registry.items():
            pf = v.get('productFolder', '')
            if not _VER_RE.search(pf):
                continue
            correct = base_name(pf)
            correct_v = ver_num(pf)
            if correct_v is None: continue
            v['productFolder'] = correct
            v['versionFolder'] = f"v{correct_v}"
            updated += 1
        REGISTRY_PATH.write_text(json.dumps(registry, ensure_ascii=False, indent=2), 'utf-8')
        print(f"  更新了 {updated} 筆 registry")


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    if dry: print("🔍 DRY RUN 模式")
    run(dry_run=dry)
