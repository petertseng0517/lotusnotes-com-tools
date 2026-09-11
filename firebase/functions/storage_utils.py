"""
Firebase Storage 讀寫共用模組（sdd5.md §4.5.2、§4.7）。

這是本專案第一個需要碰 Cloud Storage 的地方——只有「檔案即時從外部送進來，由
Cloud Functions 當下處理」這種情境才會用到（店家上傳自有合約書、日後 LINE 回傳
用印檔案），跟其他既有流程（本機批次匯出 + SSH/SCP）性質不同，見 sdd5.md §4.7
「為什麼檔案存 Firebase Storage」的說明。

Storage 安全性規則整個鎖死（allow read, write: if false），前端/瀏覽器完全不能
直接讀寫，一律要經過這裡的 Admin SDK，存取權限判斷都在 Cloud Functions 端點裡
（例如比對 queryCode），不是靠 Storage 規則本身。
"""
from __future__ import annotations

import os

from firebase_admin import storage


def _bucket():
    # 這個環境變數不能叫 FIREBASE_STORAGE_BUCKET——firebase deploy 會直接拒絕
    # .env 裡任何以 FIREBASE_/X_GOOGLE_/EXT_/KIT_ 開頭的變數名稱（保留字首），
    # 部署時實測到這個錯誤才改名，見 firebase/functions/.env。
    bucket_name = os.environ.get("STORAGE_BUCKET_NAME", "")
    if not bucket_name:
        raise RuntimeError("環境變數缺少 STORAGE_BUCKET_NAME")
    return storage.bucket(bucket_name)


def upload_bytes(path: str, data: bytes, content_type: str) -> None:
    blob = _bucket().blob(path)
    blob.upload_from_string(data, content_type=content_type)


def read_bytes(path: str) -> tuple[bytes, str] | None:
    """找不到檔案回傳 None，呼叫端自行決定要回 404 還是別的錯誤。"""
    blob = _bucket().blob(path)
    if not blob.exists():
        return None
    blob.reload()
    return blob.download_as_bytes(), (blob.content_type or "application/octet-stream")
