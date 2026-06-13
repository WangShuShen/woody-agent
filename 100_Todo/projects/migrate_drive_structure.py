#!/usr/bin/env python3
"""
Drive 結構遷移：
舊：公司/險種/主附約/商品名稱(含版次)/版次資料夾/PDFs
新：公司/險種/主附約/基本商品名稱/v0~vN/PDFs

(第X次部分變更) 從資料夾名稱移到 vX 層
"""

import json
import re
import sys
import time
from pathlib import Path
from collections import defaultdict

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

ROOT = Path(__file__).parent.parent.parent
AUTHORIZED_FILE = ROOT / "000_Agent" / "authorized_user.json"
REGISTRY_PATH   = Path(__file__).parent / "drive_registry.json"
DRIVE_ROOT      = "保單原始條款"
DRIVE_SCOPES    = ["https://www.googleapis.com/auth/drive"]
PROGRESS_FILE   = Path(__file__).parent / "migrate_progress.json"

# ── 資料夾 ID 快取，避免重複 API 請求 ─────────────
_folder_cache: dict = {}


def connect_drive():
    creds = Credentials.from_authorized_user_file(str(AUTHORIZED_FILE), DRIVE_SCOPES)
    return build("drive", "v3", credentials=creds)


def find_folder(drive, name: str, parent_id: str) -> str | None:
    key = f"{parent_id}/{name}"
    if key in _folder_cache:
        return _folder_cache[key]
    safe = name.replace("'", "\\'")
    q = f"name='{safe}' and '{parent_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    res = drive.files().list(q=q, fields="files(id)").execute()
    files = res.get("files", [])
    fid = files[0]["id"] if files else None
    _folder_cache[key] = fid
    return fid


def get_or_create_folder(drive, name: str, parent_id: str) -> str:
    fid = find_folder(drive, name, parent_id)
    if fid:
        return fid
    f = drive.files().create(
        body={"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]},
        fields="id",
    ).execute()
    fid = f["id"]
    _folder_cache[f"{parent_id}/{name}"] = fid
    return fid


def list_files_in_folder(drive, folder_id: str) -> list:
    res = drive.files().list(
        q=f"'{folder_id}' in parents and trashed=false and mimeType!='application/vnd.google-apps.folder'",
        fields="files(id,name)",
    ).execute()
    return res.get("files", [])


def move_file(drive, file_id: str, from_id: str, to_id: str):
    for attempt in range(1, 4):
        try:
            drive.files().update(
                fileId=file_id,
                addParents=to_id,
                removeParents=from_id,
                fields="id",
            ).execute()
            return
        except Exception as e:
            if attempt < 3:
                time.sleep(5)
            else:
                raise


def delete_folder(drive, folder_id: str):
    for attempt in range(1, 4):
        try:
            drive.files().delete(fileId=folder_id).execute()
            return
        except Exception as e:
            if attempt < 3:
                time.sleep(5)
            else:
                print(f"      ⚠️  刪除資料夾失敗：{e}")
                return


def load_progress() -> set:
    if PROGRESS_FILE.exists():
        data = json.loads(PROGRESS_FILE.read_text("utf-8"))
        return set(data.get("done", []))
    return set()


def save_progress(done_set: set):
    PROGRESS_FILE.write_text(
        json.dumps({"done": sorted(done_set)}, ensure_ascii=False, indent=2),
        "utf-8",
    )


def base_name(product_name: str) -> str:
    return re.sub(r'\s*\(第\d+次部分變更\)', '', product_name).strip()


def name_version(product_name: str) -> int:
    m = re.search(r'第(\d+)次部分變更', product_name)
    return int(m.group(1)) if m else 0


def migrate(dry_run: bool = False):
    print("🔌 連線 Google Drive...")
    drive = connect_drive()

    print("📂 讀取 registry...")
    registry = json.loads(REGISTRY_PATH.read_text("utf-8"))
    print(f"   共 {len(registry)} 筆")

    # 讀取已完成進度（純記憶體跳過，不打 API）
    completed = set() if dry_run else load_progress()
    print(f"   已完成：{len(completed)} 筆，剩餘：{len(registry) - len(completed)} 筆")

    # 取得根資料夾 ID
    root_id = find_folder(drive, DRIVE_ROOT, "root")
    if not root_id:
        res = drive.files().list(
            q=f"name='{DRIVE_ROOT}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
            fields="files(id)",
        ).execute()
        files = res.get("files", [])
        if not files:
            print(f"❌ 找不到根資料夾：{DRIVE_ROOT}")
            sys.exit(1)
        root_id = files[0]["id"]
    print(f"   根資料夾 ID：{root_id}")

    # 按 base_name 分組
    groups: dict[str, list] = defaultdict(list)
    for pid, v in registry.items():
        b = base_name(v["productName"])
        groups[b].append((pid, v))

    total = len(registry)
    done  = 0
    errors = 0

    for b, entries in sorted(groups.items()):
        # 找出這組的 company / productType / contractType（所有 entries 相同）
        _, first = entries[0]
        company       = first["company"]
        product_type  = first["productType"]
        contract_type = first["contractType"]

        # 若整組都已完成，直接跳過（不打任何 API）
        all_pids = [pid for pid, _ in entries]
        if all(pid in completed for pid in all_pids):
            done += len(entries)
            continue

        # 找新的 base 資料夾路徑（共用）
        cid   = find_folder(drive, company,       root_id)
        if not cid:
            print(f"   ❌ 找不到公司資料夾：{company}")
            errors += 1
            done += len(entries)
            continue
        ptid  = find_folder(drive, product_type,  cid)
        if not ptid:
            print(f"   ❌ 找不到險種資料夾：{product_type}")
            errors += 1
            done += len(entries)
            continue
        ctid  = find_folder(drive, contract_type, ptid)
        if not ctid:
            print(f"   ❌ 找不到主附約資料夾：{contract_type}")
            errors += 1
            done += len(entries)
            continue

        # 快速判斷整組是否已全部遷移：base_name 資料夾存在且含所有 vX 子資料夾
        if not dry_run:
            existing_base = find_folder(drive, b, ctid)
            if existing_base:
                sub = drive.files().list(
                    q=f"'{existing_base}' in parents and trashed=false and mimeType='application/vnd.google-apps.folder'",
                    fields="files(name)",
                ).execute().get("files", [])
                present_vers = {f["name"] for f in sub if re.match(r"v\d+$", f["name"])}
                expected_vers = {f"v{name_version(v['productName'])}" for _, v in entries}
                if present_vers >= expected_vers:
                    newly = [pid for pid, _ in entries if pid not in completed]
                    if newly:
                        for pid in newly:
                            completed.add(pid)
                        done += len(entries)
                        save_progress(completed)
                    else:
                        done += len(entries)
                    continue

        # 新的 base_name 資料夾
        if not dry_run:
            base_folder_id = get_or_create_folder(drive, b, ctid)
        else:
            base_folder_id = f"[DRY:{b}]"

        for pid, v in sorted(entries, key=lambda x: name_version(x[1]["productName"])):
            done += 1

            # 已完成的筆直接跳過
            if pid in completed:
                continue

            ver = name_version(v["productName"])
            ver_label = f"v{ver}"

            # 找到舊的版次資料夾
            old_product_folder_id = find_folder(drive, v["productFolder"], ctid)
            if not old_product_folder_id:
                print(f"   ⚠️  [{done}/{total}] 找不到舊資料夾：{v['productFolder']}")
                completed.add(pid)
                save_progress(completed)
                continue

            old_version_folder_id = find_folder(drive, v["versionFolder"], old_product_folder_id)
            if not old_version_folder_id:
                print(f"   ⚠️  [{done}/{total}] 找不到版次資料夾：{v['versionFolder'][:30]}...")
                completed.add(pid)
                save_progress(completed)
                continue

            # 建新 vX 資料夾
            if not dry_run:
                new_ver_folder_id = get_or_create_folder(drive, ver_label, base_folder_id)
            else:
                new_ver_folder_id = f"[DRY:{ver_label}]"

            # 列出舊資料夾的檔案並移動
            files = list_files_in_folder(drive, old_version_folder_id)
            if not files:
                print(f"   ⚠️  [{done}/{total}] 無檔案：{v['productName'][:30]}")
                completed.add(pid)
                save_progress(completed)
                continue

            file_names = [f["name"] for f in files]
            print(f"   [{done}/{total}] {ver_label} {v['productName'][:35]} → {file_names}")

            if not dry_run:
                for f in files:
                    try:
                        move_file(drive, f["id"], old_version_folder_id, new_ver_folder_id)
                    except Exception as e:
                        print(f"      ❌ 移動 {f['name']} 失敗：{e}")
                        errors += 1

                # 刪舊版次資料夾
                delete_folder(drive, old_version_folder_id)
                # 刪舊商品資料夾（若已空）
                remaining = list_files_in_folder(drive, old_product_folder_id)
                sub_folders = drive.files().list(
                    q=f"'{old_product_folder_id}' in parents and trashed=false",
                    fields="files(id)",
                ).execute().get("files", [])
                if not remaining and not sub_folders:
                    delete_folder(drive, old_product_folder_id)

                # 記錄完成並立即存檔（確保重啟後不重複處理）
                completed.add(pid)
                save_progress(completed)

                # 節流：每 10 筆稍停
                if done % 10 == 0:
                    time.sleep(0.5)

    print(f"\n✅ 完成！處理 {done} 筆，錯誤 {errors} 筆")


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    if dry:
        print("🔍 DRY RUN 模式（不實際修改）")
    migrate(dry_run=dry)
