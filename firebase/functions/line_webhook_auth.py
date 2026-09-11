"""
LINE webhook 簽章驗證（sdd5.md §4.7、§5）。沒有驗證的話任何人都可以偽造 webhook
payload，冒充店家傳假檔案或惡意內容進來——這是 line_webhook 唯一的身分把關依據，
不能省略。
"""
from __future__ import annotations

import base64
import hashlib
import hmac


def verify_signature(channel_secret: str, body: bytes, signature: str) -> bool:
    """channel_secret 是 LINE Messaging API 的 Channel Secret（不是 Channel Access
    Token，也不是 sdd3.md 用的 LINE_LOGIN_CHANNEL_ID——三者是不同用途的憑證）。"""
    if not channel_secret or not signature:
        return False
    expected = base64.b64encode(
        hmac.new(channel_secret.encode("utf-8"), body, hashlib.sha256).digest()
    ).decode("utf-8")
    return hmac.compare_digest(expected, signature)
