"""
公開表單（/apply）的每日每 IP 頻率限制（sdd5.md §4.3、§5）。

用 Firestore transaction 讀取後立刻遞增，避免同一秒內多個請求都讀到「還沒超過」
而一起放行——跟 codes.py 的 codeAttempts 鎖定用同一個手法。
"""
from __future__ import annotations

from datetime import datetime, timezone

from google.cloud import firestore

DEFAULT_DAILY_LIMIT = 5


def check_and_increment(db: firestore.Client, ip: str, daily_limit: int = DEFAULT_DAILY_LIMIT) -> bool:
    """
    回傳 True 表示這次請求可以放行（已計入次數），False 表示今天這個 IP 已超過上限。

    key 用「IP_日期」而不是「IP」本身，這樣每天自然重置，不需要額外寫一支清資料的排程。
    """
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    doc_ref = db.collection("applyRateLimits").document(f"{ip}_{today}")

    transaction = db.transaction()

    @firestore.transactional
    def _run(transaction: firestore.Transaction) -> bool:
        snap = doc_ref.get(transaction=transaction)
        count = snap.to_dict().get("count", 0) if snap.exists else 0
        if count >= daily_limit:
            return False
        transaction.set(doc_ref, {"count": count + 1}, merge=True)
        return True

    return _run(transaction)
