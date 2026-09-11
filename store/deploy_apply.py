"""
把 store/web/apply.html、apply_status.html 部署到 Ubuntu 主機的 {STORE_REMOTE_PATH}/。
用法: python deploy_apply.py

這兩個頁面是給院外店家用的公開申請/查詢頁（sdd5.md），跟 join.html/qa.html 放在
同一層（不是 store/web/liff/ 底下那些需要 LINE 登入的頁面），所以不跟 deploy_liff.py
共用，另外獨立一支——沿用 deploy_liff.py 的做法：部署前把佔位字串換成 .env 對應的
值，換完寫到暫存檔再 scp 上去，原始檔案（含佔位字串）保持不動。

**刻意不動 deploy_store.py**：那支腳本會順便重新匯出並上傳 stores.json，把已經
下線的公開查詢資料重新曝光（見 README.md「功能四」、sdd3.md §4.4 附註），這兩個
新頁面不需要那份資料，獨立一支才不會不小心觸發那個副作用。
"""
import os
import subprocess
import tempfile

from dotenv import load_dotenv

load_dotenv()

SFTP_HOST          = os.getenv("SFTP_HOST")
SFTP_PORT          = os.getenv("SFTP_PORT", "22")
SFTP_USER          = os.getenv("SFTP_USER")
SFTP_KEY_PATH      = os.getenv("SFTP_KEY_PATH")
STORE_REMOTE_PATH  = os.getenv("STORE_REMOTE_PATH")
FUNCTIONS_BASE_URL = os.getenv("STORE_AUTH_FUNCTIONS_BASE_URL")

if not all([SFTP_HOST, SFTP_USER, SFTP_KEY_PATH, STORE_REMOTE_PATH, FUNCTIONS_BASE_URL]):
    raise SystemExit(
        "請確認 .env 已填寫 SFTP_HOST / SFTP_USER / SFTP_KEY_PATH / STORE_REMOTE_PATH / "
        "STORE_AUTH_FUNCTIONS_BASE_URL"
    )

BASE_DIR = os.path.dirname(__file__)
WEB_DIR  = os.path.join(BASE_DIR, "web")
PAGES    = ["apply.html", "apply_status.html"]
REPLACEMENTS = {"__FUNCTIONS_BASE_URL__": FUNCTIONS_BASE_URL}
SSH_TARGET = f"{SFTP_USER}@{SFTP_HOST}"

_SYSNATIVE = r"C:\Windows\Sysnative\OpenSSH"
SSH_EXE = os.path.join(_SYSNATIVE, "ssh.exe") if os.path.exists(_SYSNATIVE) else "ssh"
SCP_EXE = os.path.join(_SYSNATIVE, "scp.exe") if os.path.exists(_SYSNATIVE) else "scp"

SSH_OPTS = ["-o", "StrictHostKeyChecking=accept-new", "-o", "BatchMode=yes"]


def run_ssh(args, check=True):
    result = subprocess.run(args, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise SystemExit(f"指令失敗（exit {result.returncode}）: {' '.join(args)}\n{result.stderr}")
    return result


run_ssh([SSH_EXE, "-i", SFTP_KEY_PATH, "-p", SFTP_PORT, *SSH_OPTS, SSH_TARGET, f"mkdir -p {STORE_REMOTE_PATH}"])

for page in PAGES:
    source_file = os.path.join(WEB_DIR, page)
    if not os.path.exists(source_file):
        raise SystemExit(f"找不到 {source_file}")

    with open(source_file, "r", encoding="utf-8") as f:
        html = f.read()

    for placeholder in REPLACEMENTS:
        if placeholder not in html:
            raise SystemExit(f"{source_file} 裡找不到 {placeholder} 佔位字串，請確認檔案內容")

    for placeholder, value in REPLACEMENTS.items():
        html = html.replace(placeholder, value)

    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as tmp:
        tmp.write(html)
        tmp_path = tmp.name

    try:
        scp_cmd = [SCP_EXE, "-i", SFTP_KEY_PATH, "-P", SFTP_PORT, *SSH_OPTS,
                   tmp_path, f"{SSH_TARGET}:{STORE_REMOTE_PATH}/{page}"]
        result = run_ssh(scp_cmd, check=False)
        if result.returncode != 0:
            print(f"上傳 {page} 失敗，重試一次...\n{result.stderr}")
            run_ssh(scp_cmd)
        print(f"已部署到 {STORE_REMOTE_PATH}/{page}")
    finally:
        os.remove(tmp_path)
