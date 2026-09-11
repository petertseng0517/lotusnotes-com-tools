"""
互動式審核特約商店線上申請（sdd5.md §3、§4.4、§4.7、§4.8、§4.10）。
用法:
    python apply_review.py                     # 審核 status=pending 的申請
    python apply_review.py --status merchant_signed  # 處理店家已用印回傳、待人工蓋院方大小章的申請
    python apply_review.py --status approved   # 純瀏覽（例如看目前卡在等套版的申請）
    python apply_review.py --abandon 20260909-03     # 把指定申請標記為放棄/不予受理

`--status pending` 時逐筆審核；`--status merchant_signed` 時逐筆提示是否要
下載檢查店家回傳的用印檔案、確認流程做完（列印、蓋院方大小章、掃描、透過 LINE
Official Account Manager 手動傳回最終檔案給店家）後標記完成；其他狀態單純列出
清單，不出現互動提示。

核准「使用店家制式範本」的申請前，會先下載店家上傳的合約書並用作業系統預設的
關聯程式開啟，讓你人工看內容（比照本專案其他地方假設 Windows 環境的做法）。

核准「使用慈濟醫院合約範本」的申請時，會問合約起訖日（sdd5.md §4.5.1：這是人工
決定的事，不是系統自動算，預設抓「今天起、一年後止」，直接按 Enter 接受，或自己
輸入別的日期覆蓋）。核准後這筆申請會停在 approved，等 Phase 2 的
generate_contracts.py 接手套版產生 PDF；「使用店家制式範本」核准後會直接變成
contract_ready，因為合約內容就是店家自己上傳的那份，沒有套版這個動作。

店家透過 LINE 官方帳號傳回用印掃描檔後（sdd5.md §4.7），狀態會自動變成
merchant_signed，不需要在這裡手動標記——這裡負責的是「人工核對用印是否齊全
之後」的後續動作。
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


def open_remote_file(application_id: str, kind: str):
    """kind 是 "own_template"（店家自有合約書）或 "merchant_signed"（店家用印回傳
    的掃描檔），見 main.py download_file 端點。"""
    resp = requests.get(
        f"{FUNCTIONS_BASE_URL}/download_file",
        params={"applicationId": application_id, "kind": kind},
        headers=HEADERS,
        timeout=30,
        verify=False,
    )
    if resp.status_code != 200:
        print(f"下載檔案失敗（HTTP {resp.status_code}）：{resp.text}")
        return

    ext = CONTENT_TYPE_EXT.get(resp.headers.get("Content-Type", ""), "")
    with tempfile.NamedTemporaryFile("wb", suffix=ext, delete=False) as tmp:
        tmp.write(resp.content)
        tmp_path = tmp.name

    print(f"已下載檔案：{tmp_path}")
    try:
        os.startfile(tmp_path)
    except OSError as e:
        print(f"無法自動開啟檔案，請自行手動開啟上面的路徑（{e}）")


def submit_update_status(application_id: str, status: str, final_doc_url: str = None) -> dict:
    body = {"applicationId": application_id, "status": status}
    if final_doc_url:
        body["finalDocUrl"] = final_doc_url
    resp = requests.post(
        f"{FUNCTIONS_BASE_URL}/admin_update_status",
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
        print(f"狀態更新失敗（HTTP {resp.status_code}）：{result}")
    return result


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
        open_remote_file(app["applicationId"], "own_template")

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


def review_merchant_signed(app: dict):
    """店家已透過 LINE 傳回用印掃描檔，狀態自動變成 merchant_signed（sdd5.md §4.7）；
    這裡處理的是人工核對之後的動作（sdd5.md §4.8）：列印、蓋院方大小章、掃描、
    透過 LINE Official Account Manager 手動傳回最終檔案給店家，都是這支腳本管不到
    的實體動作，這裡只負責在你都做完之後把狀態標記成 completed。"""
    print_application(app)

    action = input("查看店家回傳的用印檔案請按 v，確認流程都做完了請按 c，跳過請直接按 Enter：").strip().lower()
    if action == "v":
        open_remote_file(app["applicationId"], "merchant_signed")
        action = input("確認流程都做完了（列印、蓋院方大小章、掃描、已透過 LINE 傳回最終檔案給店家）請按 c，跳過請直接按 Enter：").strip().lower()

    if action != "c":
        print("已跳過")
        return

    result = submit_update_status(app["applicationId"], "completed")
    print(f"已標記完成：{result}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--status", default="pending", help="要列出的申請狀態（預設 pending）")
    parser.add_argument("--abandon", metavar="APPLICATION_ID", help="把指定申請標記為 abandoned（放棄/不予受理），不會進入其他審核流程")
    args = parser.parse_args()

    if args.abandon:
        print(f"已標記為 abandoned：{submit_update_status(args.abandon, 'abandoned')}")
        return

    apps = fetch_applications(args.status)
    if not apps:
        print(f"目前沒有狀態為「{args.status}」的申請")
        return

    print(f"共 {len(apps)} 筆狀態為「{args.status}」的申請")

    if args.status == "pending":
        for app in apps:
            review_pending(app)
        print("審核完畢")
    elif args.status == "merchant_signed":
        for app in apps:
            review_merchant_signed(app)
        print("處理完畢")
    else:
        for app in apps:
            print_application(app)


if __name__ == "__main__":
    main()
