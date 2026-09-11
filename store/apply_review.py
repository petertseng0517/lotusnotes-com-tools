"""
互動式審核特約商店線上申請（sdd5.md §3、§4.4、§4.10）。
用法: python apply_review.py [--status pending]

預設列出 status=pending 的申請逐筆審核；也可以指定其他狀態單純瀏覽
（例如 --status approved 看目前卡在等套版的申請），這種情況只會列出清單，
不會出現審核提示。

核准「使用店家制式範本」的申請前，會先下載店家上傳的合約書並用作業系統預設的
關聯程式開啟，讓你人工看內容（比照本專案其他地方假設 Windows 環境的做法）。

核准「使用慈濟醫院合約範本」的申請時，會問合約起訖日（sdd5.md §4.5.1：這是人工
決定的事，不是系統自動算，預設抓「今天起、一年後止」，直接按 Enter 接受，或自己
輸入別的日期覆蓋）。核准後這筆申請會停在 approved，等 Phase 2 的
generate_contracts.py 接手套版產生 PDF；「使用店家制式範本」核准後會直接變成
contract_ready，因為合約內容就是店家自己上傳的那份，沒有套版這個動作。
"""
import argparse
import os
import tempfile
from datetime import date

import requests
import urllib3
from dotenv import load_dotenv

from tax_id_lookup import describe as describe_tax_id

load_dotenv()

# 院內網路對外 HTTPS 會被自簽憑證攔截（跟 broadcast_code.py/import_roster.py 遇到的
# 是同一個環境限制），略過憑證驗證，見 README.md「注意事項」。
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

FUNCTIONS_BASE_URL = os.getenv("STORE_AUTH_FUNCTIONS_BASE_URL")
ADMIN_SHARED_SECRET = os.getenv("ADMIN_SHARED_SECRET")

if not all([FUNCTIONS_BASE_URL, ADMIN_SHARED_SECRET]):
    raise SystemExit("請確認 .env 已填寫 STORE_AUTH_FUNCTIONS_BASE_URL / ADMIN_SHARED_SECRET")

HEADERS = {"X-Admin-Secret": ADMIN_SHARED_SECRET}

CONTENT_TYPE_EXT = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}


def fetch_applications(status: str) -> list:
    resp = requests.get(
        f"{FUNCTIONS_BASE_URL}/admin_list_applications",
        params={"status": status} if status else {},
        headers=HEADERS,
        timeout=30,
        verify=False,
    )
    if resp.status_code != 200:
        raise SystemExit(f"查詢失敗（HTTP {resp.status_code}）：{resp.text}")
    return resp.json().get("applications", [])


def submit_review(application_id: str, decision: str, review_note: str,
                   contract_start_date: str = None, contract_end_date: str = None) -> dict:
    body = {"applicationId": application_id, "decision": decision, "reviewNote": review_note}
    if contract_start_date:
        body["contractStartDate"] = contract_start_date
    if contract_end_date:
        body["contractEndDate"] = contract_end_date
    resp = requests.post(
        f"{FUNCTIONS_BASE_URL}/admin_review_application",
        json=body,
        headers=HEADERS,
        timeout=30,
        verify=False,
    )
    try:
        result = resp.json()
    except ValueError:
        result = {"ok": False, "error": resp.text}
    if resp.status_code != 200:
        print(f"審核送出失敗（HTTP {resp.status_code}）：{result}")
    return result


def open_own_template_file(application_id: str):
    resp = requests.get(
        f"{FUNCTIONS_BASE_URL}/download_file",
        params={"applicationId": application_id, "kind": "own_template"},
        headers=HEADERS,
        timeout=30,
        verify=False,
    )
    if resp.status_code != 200:
        print(f"下載店家上傳的合約書失敗（HTTP {resp.status_code}）：{resp.text}")
        return

    ext = CONTENT_TYPE_EXT.get(resp.headers.get("Content-Type", ""), "")
    with tempfile.NamedTemporaryFile("wb", suffix=ext, delete=False) as tmp:
        tmp.write(resp.content)
        tmp_path = tmp.name

    print(f"已下載店家上傳的合約書：{tmp_path}")
    try:
        os.startfile(tmp_path)
    except OSError as e:
        print(f"無法自動開啟檔案，請自行手動開啟上面的路徑（{e}）")


def default_contract_dates() -> tuple:
    """暫定規則（sdd5.md §4.5.1）：今天起、一年後止，2/29 遇到非閏年就退一天。"""
    today = date.today()
    try:
        end = today.replace(year=today.year + 1)
    except ValueError:
        end = today.replace(month=2, day=28, year=today.year + 1)
    return today.isoformat(), end.isoformat()


def print_application(app: dict):
    print("-" * 50)
    print(f"申請編號：{app['applicationId']}　狀態：{app['status']}")
    print(f"店名：{app['storeName']}　負責人：{app['ownerName']}　統編：{app['taxId']}")
    print(f"地址：{app['address']}　電話：{app['phone']}　Email：{app['email']}")
    print(f"聯絡人：{app.get('contactPerson') or '（同負責人）'}　營業類別：{app['businessCategory']}")
    print(f"優惠內容：{app['discountContent']}")
    if app.get("note"):
        print(f"備註：{app['note']}")
    source_label = "慈濟醫院合約範本" if app["contractSource"] == "hospital_template" else "店家制式範本"
    print(f"合約來源：{source_label}")
    print(describe_tax_id(app["taxId"]))


def review_pending(app: dict):
    print_application(app)

    if app["contractSource"] == "own_template":
        print("這筆是「使用店家制式範本」，先下載店家上傳的合約書供你人工檢查內容...")
        open_own_template_file(app["applicationId"])

    decision = input("核准請按 a，婉拒請按 r，跳過請直接按 Enter：").strip().lower()
    if decision not in ("a", "r"):
        print("已跳過")
        return

    if decision == "r":
        note = input("婉拒理由：").strip()
        print(f"已婉拒：{submit_review(app['applicationId'], 'rejected', note)}")
        return

    note = input("審核備註（選填）：").strip()
    contract_start_date = contract_end_date = None
    if app["contractSource"] == "hospital_template":
        default_start, default_end = default_contract_dates()
        start_input = input(f"合約起始日（YYYY-MM-DD，直接 Enter 使用今天 {default_start}）：").strip()
        contract_start_date = start_input or default_start
        end_input = input(f"合約到期日（YYYY-MM-DD，直接 Enter 使用一年後 {default_end}）：").strip()
        contract_end_date = end_input or default_end

    result = submit_review(app["applicationId"], "approved", note, contract_start_date, contract_end_date)
    print(f"已核准：{result}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--status", default="pending", help="要列出的申請狀態（預設 pending）")
    args = parser.parse_args()

    apps = fetch_applications(args.status)
    if not apps:
        print(f"目前沒有狀態為「{args.status}」的申請")
        return

    print(f"共 {len(apps)} 筆狀態為「{args.status}」的申請")

    if args.status != "pending":
        for app in apps:
            print_application(app)
        return

    for app in apps:
        review_pending(app)

    print("審核完畢")


if __name__ == "__main__":
    main()
