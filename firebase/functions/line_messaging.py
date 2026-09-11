"""
LINE Messaging API 呼叫共用模組（sdd5.md §4.7）：回覆訊息、下載使用者傳來的檔案內容。

用的是「花蓮職工福利行政小組」這個 LINE 官方帳號的 Messaging API 頻道憑證
（LINE_CHANNEL_ACCESS_TOKEN），跟 sdd3.md 的 LINE_LOGIN_CHANNEL_ID（LIFF 登入用）
是不同的憑證——見 README「功能六」的設定說明。
"""
from __future__ import annotations

import os

import requests

REPLY_URL = "https://api.line.me/v2/bot/message/reply"
CONTENT_URL_TEMPLATE = "https://api-data.line.me/v2/bot/message/{message_id}/content"


def _access_token() -> str:
    return os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")


def reply_text(reply_token: str, text: str) -> None:
    """失敗不拋例外——回覆訊息失敗頂多店家少收到一句提示，不該讓整個 webhook
    處理跟著失敗（LINE 平台重送 webhook 反而可能造成重複處理）。"""
    if not reply_token:
        return
    try:
        requests.post(
            REPLY_URL,
            headers={
                "Authorization": f"Bearer {_access_token()}",
                "Content-Type": "application/json",
            },
            json={"replyToken": reply_token, "messages": [{"type": "text", "text": text}]},
            timeout=10,
        )
    except requests.RequestException as e:
        print(f"line_messaging.reply_text failed: {e}")


def download_content(message_id: str) -> tuple[bytes, str] | None:
    try:
        resp = requests.get(
            CONTENT_URL_TEMPLATE.format(message_id=message_id),
            headers={"Authorization": f"Bearer {_access_token()}"},
            timeout=30,
        )
    except requests.RequestException as e:
        print(f"line_messaging.download_content failed: {e}")
        return None
    if resp.status_code != 200:
        return None
    return resp.content, resp.headers.get("Content-Type", "application/octet-stream")
