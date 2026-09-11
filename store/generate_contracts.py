"""
合約套版與 PDF 產生（sdd5.md §4.5.1）。
用法: python generate_contracts.py

撈出 status=approved 且合約來源為「使用慈濟醫院合約範本」的申請（「使用店家制式
範本」核准當下就已經直接轉 contract_ready，不會走到這裡，見 apply_review.py／
admin_review_application），逐筆套版產生 PDF：

  1. 用 Word COM 開啟已經準備好佔位符的範本（store/templates/contract_template.doc，
     需要先跑過一次 prepare_contract_template.py，見那支腳本的說明），對每個
     {{...}} 佔位符做全文件 Find/Replace（這裡跟 prepare_contract_template.py
     不同——那邊要精準鎖定單一段落避免誤換，這裡佔位符本身已經是唯一字串，同一個
     欄位在文件裡出現兩次也是「兩處都要換成同一個值」，直接對整份文件 ReplaceAll
     即可）。
  2. 同一個 Word COM session 直接匯出 PDF，不落地存 .doc 中繼檔。
  3. 用既有 SSH 金鑰把 PDF 上傳到 Ubuntu 網站伺服器一個隨機路徑（每筆申請各自一組
     token，目錄列表沿用既有伺服器設定回 403）。
  4. 呼叫 admin_mark_contract_ready 把下載網址寫回 Firestore，狀態轉 contract_ready。

套版用的範本檔案處理完一律 Close(SaveChanges=False)，讓 contract_template.doc
這份母片保持乾淨，下一筆申請可以重複拿來套版。
"""
import os
import secrets
import subprocess
import tempfile

import requests
import urllib3
import win32com.client as win32
from dotenv import load_dotenv

load_dotenv()

# 院內網路對外 HTTPS 會被自簽憑證攔截，略過憑證驗證，見 README.md「注意事項」。
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

FUNCTIONS_BASE_URL   = os.getenv("STORE_AUTH_FUNCTIONS_BASE_URL")
ADMIN_SHARED_SECRET  = os.getenv("ADMIN_SHARED_SECRET")
SFTP_HOST            = os.getenv("SFTP_HOST")
SFTP_PORT            = os.getenv("SFTP_PORT", "22")
SFTP_USER            = os.getenv("SFTP_USER")
SFTP_KEY_PATH        = os.getenv("SFTP_KEY_PATH")
STORE_REMOTE_PATH    = os.getenv("STORE_REMOTE_PATH")
STORE_PUBLIC_BASE_URL = os.getenv("STORE_PUBLIC_BASE_URL")

_REQUIRED_ENV = (
    "STORE_AUTH_FUNCTIONS_BASE_URL", "ADMIN_SHARED_SECRET", "SFTP_HOST", "SFTP_USER",
    "SFTP_KEY_PATH", "STORE_REMOTE_PATH", "STORE_PUBLIC_BASE_URL",
)
if not all(os.getenv(name) for name in _REQUIRED_ENV):
    raise SystemExit(f"請確認 .env 已填寫：{' / '.join(_REQUIRED_ENV)}")

HEADERS = {"X-Admin-Secret": ADMIN_SHARED_SECRET}

BASE_DIR = os.path.dirname(__file__)
TEMPLATE_PATH = os.path.join(BASE_DIR, "templates", "contract_template.doc")

_SYSNATIVE = r"C:\Windows\Sysnative\OpenSSH"
SSH_EXE = os.path.join(_SYSNATIVE, "ssh.exe") if os.path.exists(_SYSNATIVE) else "ssh"
SCP_EXE = os.path.join(_SYSNATIVE, "scp.exe") if os.path.exists(_SYSNATIVE) else "scp"
SSH_OPTS = ["-o", "StrictHostKeyChecking=accept-new", "-o", "BatchMode=yes"]
SSH_TARGET = f"{SFTP_USER}@{SFTP_HOST}"

WD_EXPORT_FORMAT_PDF = 17  # wdExportFormatPDF

PLACEHOLDER_FIELDS = (
    "store_name", "discount_content", "address", "owner_name", "tax_id", "contact_person", "phone",
)


def run_ssh(args, check=True):
    result = subprocess.run(args, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise SystemExit(f"指令失敗（exit {result.returncode}）: {' '.join(args)}\n{result.stderr}")
    return result


def fetch_approved_applications() -> list:
    resp = requests.get(
        f"{FUNCTIONS_BASE_URL}/admin_list_applications",
        params={"status": "approved"},
        headers=HEADERS,
        timeout=30,
        verify=False,
    )
    if resp.status_code != 200:
        raise SystemExit(f"查詢失敗（HTTP {resp.status_code}）：{resp.text}")
    apps = resp.json().get("applications", [])
    # own_template 核准後會直接轉 contract_ready，理論上不會出現在這裡；多一層篩選
    # 純粹是防呆，避免資料異常時誤套版。
    return [a for a in apps if a.get("contractSource") == "hospital_template"]


def roc_date_parts(iso_date: str) -> tuple[str, str, str]:
    """西元日期字串（YYYY-MM-DD）轉民國年/月/日，月/日不補零，符合合約書慣用寫法。"""
    year, month, day = iso_date.split("-")
    return str(int(year) - 1911), str(int(month)), str(int(day))


def replace_all(doc, target: str, replacement: str):
    rng = doc.Content
    f = rng.Find
    f.ClearFormatting()
    f.Text = target
    f.Replacement.ClearFormatting()
    f.Replacement.Text = replacement
    f.Forward = True
    f.Wrap = 1  # wdFindContinue：這裡要換掉全文件裡「每一個」出現的地方
    if not f.Execute(Replace=2):  # wdReplaceAll
        raise SystemExit(f"範本裡找不到佔位符 {target!r}，範本檔案可能損壞或版本不對，請重新跑 prepare_contract_template.py")


def render_contract_pdf(word, app: dict, pdf_path: str):
    if not os.path.exists(TEMPLATE_PATH):
        raise SystemExit(
            f"找不到 {TEMPLATE_PATH}，需要先執行一次 prepare_contract_template.py 產生已插入佔位符的範本"
        )

    doc = word.Documents.Open(TEMPLATE_PATH)
    try:
        start_y, start_m, start_d = roc_date_parts(app["contractStartDate"])
        end_y, end_m, end_d = roc_date_parts(app["contractEndDate"])
        # 簽約日期沿用合約起始日，不另外蒐集（sdd5.md §4.5.1）。
        sign_y, sign_m, sign_d = start_y, start_m, start_d

        values = {
            "store_name": app["storeName"],
            "discount_content": app["discountContent"],
            "address": app["address"],
            "owner_name": app["ownerName"],
            "tax_id": app["taxId"],
            "contact_person": app.get("contactPerson") or "",
            "phone": app["phone"],
            "start_y": start_y, "start_m": start_m, "start_d": start_d,
            "end_y": end_y, "end_m": end_m, "end_d": end_d,
            "sign_y": sign_y, "sign_m": sign_m, "sign_d": sign_d,
        }
        for field, value in values.items():
            replace_all(doc, "{{" + field + "}}", value)

        doc.ExportAsFixedFormat(OutputFileName=pdf_path, ExportFormat=WD_EXPORT_FORMAT_PDF)
    finally:
        doc.Close(SaveChanges=False)  # 範本母片保持乾淨，不落地存修改後的 .doc


def upload_and_get_url(pdf_path: str) -> str:
    token = secrets.token_hex(24)
    remote_dir = f"{STORE_REMOTE_PATH}/contracts"
    remote_path = f"{remote_dir}/{token}.pdf"

    run_ssh([SSH_EXE, "-i", SFTP_KEY_PATH, "-p", SFTP_PORT, *SSH_OPTS, SSH_TARGET, f"mkdir -p {remote_dir}"])

    scp_cmd = [SCP_EXE, "-i", SFTP_KEY_PATH, "-P", SFTP_PORT, *SSH_OPTS, pdf_path, f"{SSH_TARGET}:{remote_path}"]
    result = run_ssh(scp_cmd, check=False)
    if result.returncode != 0:
        print(f"上傳失敗，重試一次...\n{result.stderr}")
        run_ssh(scp_cmd)

    return f"{STORE_PUBLIC_BASE_URL.rstrip('/')}/contracts/{token}.pdf"


def mark_contract_ready(application_id: str, contract_url: str):
    resp = requests.post(
        f"{FUNCTIONS_BASE_URL}/admin_mark_contract_ready",
        json={"applicationId": application_id, "contractUrl": contract_url},
        headers=HEADERS,
        timeout=30,
        verify=False,
    )
    if resp.status_code != 200:
        raise SystemExit(f"回寫失敗（HTTP {resp.status_code}）：{resp.text}")


def main():
    apps = fetch_approved_applications()
    if not apps:
        print("目前沒有待套版的申請（status=approved 且合約來源為慈濟醫院範本）")
        return

    print(f"共 {len(apps)} 筆待套版")
    word = win32.gencache.EnsureDispatch("Word.Application")
    word.Visible = False
    try:
        for app in apps:
            application_id = app["applicationId"]
            print(f"套版中：{application_id}（{app['storeName']}）...")
            with tempfile.TemporaryDirectory() as tmp_dir:
                pdf_path = os.path.join(tmp_dir, f"{application_id}.pdf")
                render_contract_pdf(word, app, pdf_path)
                contract_url = upload_and_get_url(pdf_path)
            mark_contract_ready(application_id, contract_url)
            print(f"完成：{contract_url}")
    finally:
        word.Quit()

    print("全部套版完成")


if __name__ == "__main__":
    main()
