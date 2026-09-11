"""
統一編號真實性查證（sdd5.md §4.10）。apply_review.py 審核時呼叫，供人工判斷參考，
不是自動核准/拒絕的關卡——查無資料或名稱對不上都不擋件（sdd5.md §2 非目標）。

用經濟部商工行政資料開放平臺的兩個資料集，涵蓋範圍不同：
  - 統編查公司名稱（公司登記）：實測完全公開，不需要 IP 白名單，見 lookup_company()。
  - 商業統一編號查商號名稱（商業/商號登記，小吃店、商店這類更常見的登記類型）：
    實測會回「非授權介接之IP」，需要先向經濟部申請把這台機器的固定對外 IP 加入
    白名單才能用——這點跟 sdd5.md §4.10 原本設計「兩個都免申請」不符，是實作時
    才發現的落差（見 sdd5.md §6 已更新的說明）。lookup_business() 仍然實作正確
    的查詢方式（oid、filter 欄位名稱都已用真實範例統編測過），只是目前這台機器
    呼叫下去一定會被擋，describe() 會清楚標示「查證功能未開通」，不會誤判成
    「查無登記資料」而讓審核者誤以為申請造假。
"""
from __future__ import annotations

import requests
import urllib3

# 院內網路對外 HTTPS 會被自簽憑證攔截（跟 broadcast_code.py/import_roster.py 遇到的
# 是同一個環境限制，見 README.md「注意事項」），略過憑證驗證。
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

COMPANY_API_URL = "https://data.gcis.nat.gov.tw/od/data/api/5F64D864-61CB-4D0D-8AD9-492047CC1EA6"
BUSINESS_API_URL = "https://data.gcis.nat.gov.tw/od/data/api/855A3C87-003A-4930-AA4B-2F4130D713DC"

_UNAUTHORIZED_IP_MARKER = "非授權介接"


def _query(url: str, field: str, tax_id: str) -> dict:
    """回傳 {"ok", "blocked", "records", "error"}——ok=False 是呼叫失敗（網路錯誤
    或平台回傳非預期格式），blocked=True 是這個資料集需要 IP 白名單、目前還沒授權。"""
    try:
        resp = requests.get(
            url,
            params={"$format": "json", "$filter": f"{field} eq {tax_id}", "$top": 5},
            timeout=10,
            verify=False,
        )
    except requests.RequestException as e:
        return {"ok": False, "blocked": False, "records": [], "error": str(e)}

    text = resp.text.strip()
    if _UNAUTHORIZED_IP_MARKER in text:
        return {"ok": False, "blocked": True, "records": [], "error": text}

    try:
        records = resp.json() if text else []
    except ValueError:
        return {"ok": False, "blocked": False, "records": [], "error": text}

    if not isinstance(records, list):
        return {"ok": False, "blocked": False, "records": [], "error": text}

    return {"ok": True, "blocked": False, "records": records, "error": None}


def lookup_company(tax_id: str) -> dict:
    """查「統編查公司名稱」（公司登記），已實測不需要 IP 白名單。"""
    result = _query(COMPANY_API_URL, "Business_Accounting_NO", tax_id)
    if result["ok"] and result["records"]:
        record = result["records"][0]
        result["name"] = record.get("Company_Name")
        result["ownerName"] = record.get("Responsible_Name")
        result["statusDesc"] = record.get("Company_Status_Desc")
    return result


def lookup_business(tax_id: str) -> dict:
    """查「商業統一編號查商號名稱」（商業/商號登記）。目前這個資料集需要 IP 白名單，
    這台機器尚未申請，呼叫一律會回 blocked=True——保留這支函式跟已驗證正確的呼叫
    方式，等哪天真的申請到白名單，呼叫端（describe()）不需要跟著改。"""
    return _query(BUSINESS_API_URL, "President_No", tax_id)


def describe(tax_id: str) -> str:
    """給 apply_review.py 印出來的一行文字摘要，涵蓋公司/商業兩種查詢結果。"""
    company = lookup_company(tax_id)
    if company["ok"] and company["records"]:
        extra = f"（{company.get('statusDesc')}）" if company.get("statusDesc") else ""
        return f"政府登記名稱（公司）：{company.get('name')}{extra}"

    business = lookup_business(tax_id)
    if business["blocked"]:
        return "查證功能未開通：「商業統一編號查商號名稱」需要向經濟部申請 IP 白名單，這台機器尚未申請（見 sdd5.md §6）"
    if business["ok"] and business["records"]:
        return f"政府登記資料（商業/商號）：{business['records'][0]}"
    if not company["ok"]:
        return f"查證失敗（{company.get('error') or '未知錯誤'}），請自行判斷"
    return "查無登記資料（不代表申請造假，可能是查不到的特殊單位，請人工判斷）"
