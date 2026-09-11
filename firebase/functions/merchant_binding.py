"""
店家 LINE 官方帳號身分綁定與用印檔案接收（sdd5.md §4.7）。

節流邏輯（連續輸入錯誤鎖定）跟 codes.py 的 codeAttempts 是同一套精神（Firestore
transaction 讀取後立刻遞增，避免同時間多個請求繞過鎖定次數上限，見 sdd3.md
§8.6），但這裡核對的申請編號+查詢碼本來就已經由 applications.py 產生跟驗證過
（sdd5.md §4.4），這裡只是換一個管道（LINE 聊天視窗文字比對）重新核對一次身分，
核對邏輯直接呼叫 applications.get_application()/check_query_code()，不重複實作。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from google.cloud import firestore

import applications

MAX_FAIL_COUNT = 5
LOCK_MINUTES = 15


def get_binding(db: firestore.Client, line_user_id: str) -> dict | None:
    doc = db.collection("merchantLineAuth").document(line_user_id).get()
    return doc.to_dict() if doc.exists else None


def _is_locked(db: firestore.Client, line_user_id: str) -> bool:
    doc = db.collection("merchantBindAttempts").document(line_user_id).get()
    if not doc.exists:
        return False
    locked_until = doc.to_dict().get("lockedUntil")
    return bool(locked_until and locked_until > datetime.now(timezone.utc))


def _record_failed_attempt(db: firestore.Client, line_user_id: str) -> bool:
    """回傳 True 表示這次失敗剛好觸發鎖定。"""
    ref = db.collection("merchantBindAttempts").document(line_user_id)
    transaction = db.transaction()

    @firestore.transactional
    def _run(transaction: firestore.Transaction) -> bool:
        snap = ref.get(transaction=transaction)
        fail_count = (snap.to_dict().get("failCount", 0) if snap.exists else 0) + 1
        just_locked = fail_count >= MAX_FAIL_COUNT
        update = {"failCount": fail_count}
        if just_locked:
            update["lockedUntil"] = datetime.now(timezone.utc) + timedelta(minutes=LOCK_MINUTES)
        transaction.set(ref, update, merge=True)
        return just_locked

    return _run(transaction)


def _reset_attempts(db: firestore.Client, line_user_id: str) -> None:
    db.collection("merchantBindAttempts").document(line_user_id).set({"failCount": 0, "lockedUntil": None})


def parse_bind_text(text: str) -> tuple[str, str] | None:
    """解析聊天視窗打的「申請編號 查詢碼」，中間用任意空白分隔（含全形空白，
    Python str.split() 預設就會處理）。格式不對回傳 None，不在這裡判斷對錯，
    由呼叫端決定要不要當作一次失敗嘗試（sdd5.md §6：文字容錯細節）。"""
    parts = text.strip().split()
    if len(parts) != 2:
        return None
    return parts[0], parts[1]


def try_bind(db: firestore.Client, line_user_id: str, text: str) -> dict:
    """回傳 {"result": "bound"|"locked"|"wrong"|"bad_format", "storeName": ...（僅 bound 時有值）}"""
    if _is_locked(db, line_user_id):
        return {"result": "locked"}

    parsed = parse_bind_text(text)
    if not parsed:
        return {"result": "bad_format"}

    application_id, query_code = parsed
    application = applications.get_application(db, application_id)
    if application and applications.check_query_code(application, query_code):
        db.collection("merchantLineAuth").document(line_user_id).set({
            "applicationId": application_id,
            "boundAt": firestore.SERVER_TIMESTAMP,
        })
        _reset_attempts(db, line_user_id)
        return {"result": "bound", "storeName": application.get("storeName")}

    just_locked = _record_failed_attempt(db, line_user_id)
    return {"result": "locked" if just_locked else "wrong"}


def receive_file(db: firestore.Client, application_id: str, storage_path: str) -> None:
    """webhook 收到已綁定使用者傳來的檔案、上傳到 Storage 後呼叫，更新申請狀態
    （sdd5.md §4.7 步驟③、§4.8）。"""
    db.collection("storeApplications").document(application_id).update({
        "merchantSignedFileUrl": storage_path,
        "merchantSignedAt": firestore.SERVER_TIMESTAMP,
        "status": applications.STATUS_MERCHANT_SIGNED,
    })
