#!/usr/bin/env python3
"""
uuid_registry.json ↔ Google Drive 同步模組

兩台電腦（南山 206 / 三商美邦 211）各自爬蟲後，
透過同一個 Drive 資料夾共享狀態，避免重複分析同一張保單。

使用：
  from sync_registry import download_registry, upload_registry
"""

import io
import json
from pathlib import Path

REGISTRY_FILENAME = "uuid_registry.json"


def _find_file(drive, name: str, folder_id: str) -> str | None:
    """在 Drive 資料夾中找指定名稱的檔案，回傳 file_id 或 None"""
    q = f"name='{name}' and '{folder_id}' in parents and trashed=false"
    res = drive.files().list(q=q, fields="files(id)").execute()
    files = res.get("files", [])
    return files[0]["id"] if files else None


def download_registry(drive, folder_id: str, local_path: Path) -> dict:
    """
    從 Drive 下載 registry 並與本機版本合併。
    合併策略：本機覆蓋遠端（本機是剛跑完的最新狀態）。
    回傳合併後的 dict，同時覆寫 local_path。
    """
    from googleapiclient.http import MediaIoBaseDownload

    local: dict = {}
    if local_path.exists():
        try:
            local = json.loads(local_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    file_id = _find_file(drive, REGISTRY_FILENAME, folder_id)
    if not file_id:
        print("   ℹ️  Drive 尚無 registry，使用本機版本")
        return local

    try:
        request = drive.files().get_media(fileId=file_id)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        remote: dict = json.loads(buf.getvalue().decode("utf-8"))
        # 遠端優先覆蓋（取最新狀態），本機的新增項目補進來
        merged = {**local, **remote}
        print(f"   ✅ 已同步 Drive registry（遠端 {len(remote)} 筆，本機 {len(local)} 筆，合併 {len(merged)} 筆）")
        local_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        return merged
    except Exception as e:
        print(f"   ⚠️  下載 Drive registry 失敗：{e}，繼續使用本機版本")
        return local


def upload_registry(drive, folder_id: str, state: dict, local_path: Path):
    """把目前 registry 上傳（覆蓋）到 Drive 資料夾"""
    from googleapiclient.http import MediaIoBaseUpload

    content = json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8")
    media = MediaIoBaseUpload(io.BytesIO(content), mimetype="application/json", resumable=False)

    file_id = _find_file(drive, REGISTRY_FILENAME, folder_id)
    try:
        if file_id:
            drive.files().update(fileId=file_id, media_body=media).execute()
        else:
            meta = {"name": REGISTRY_FILENAME, "parents": [folder_id]}
            drive.files().create(body=meta, media_body=media, fields="id").execute()
    except Exception as e:
        print(f"   ⚠️  上傳 registry 到 Drive 失敗：{e}")
