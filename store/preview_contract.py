"""
本機快速預覽合約套版效果（sdd5.md §4.5.1 排版調整/確認用）。
用法: python preview_contract.py

用假資料套一次版看排版對不對，不需要 Firebase／SSH 相關 .env 設定（那些是
generate_contracts.py 正式套版+上傳才需要的，這裡只是要看 store/templates/
contract_template.doc 套版後長怎樣）。想換測試資料的話，直接改下面
SAMPLE_APPLICATION 這個字典就好，跑完會自動開啟產生的 PDF。
"""
import os

# generate_contracts.py 一載入就會檢查 SSH/Firebase 相關的 .env 設定（因為它平常
# 還要負責上傳、回寫進度），這裡只是想看排版用不到那些，先給假值騙過檢查即可。
os.environ.setdefault("STORE_AUTH_FUNCTIONS_BASE_URL", "https://example.invalid")
os.environ.setdefault("ADMIN_SHARED_SECRET", "dummy")
os.environ.setdefault("SFTP_HOST", "dummy")
os.environ.setdefault("SFTP_USER", "dummy")
os.environ.setdefault("SFTP_KEY_PATH", "dummy")
os.environ.setdefault("STORE_REMOTE_PATH", "/dummy")
os.environ.setdefault("STORE_PUBLIC_BASE_URL", "https://example.invalid")

import win32com.client as win32
from generate_contracts import render_contract_pdf

# 故意用比較長的店名/地址/優惠內容測試，比短資料更容易看出排版有沒有跑掉。
SAMPLE_APPLICATION = {
    "applicationId": "PREVIEW",
    "storeName": "花蓮縣壽豐鄉在地手作烘焙坊有限公司",
    "discountContent": "出示員工識別證消費滿300元享9折優惠，滿1000元加贈精緻手作餅乾禮盒一份，優惠不得與其他促銷活動併用，詳細品項依店內公告為準。",
    "address": "花蓮縣壽豐鄉中山路二段123號1樓",
    "ownerName": "林小美",
    "taxId": "87654321",
    "contactPerson": "林小美",
    "phone": "03-8651234 / 0912-345678",
    "contractStartDate": "2026-10-01",
    "contractEndDate": "2027-09-30",
}

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "output", "preview_contract.pdf")


def main():
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    word = win32.gencache.EnsureDispatch("Word.Application")
    word.Visible = False
    try:
        render_contract_pdf(word, SAMPLE_APPLICATION, OUTPUT_PATH)
    finally:
        word.Quit()

    print(f"已產生：{OUTPUT_PATH}")
    try:
        os.startfile(OUTPUT_PATH)
    except OSError as e:
        print(f"無法自動開啟，請自行手動開啟上面的路徑（{e}）")


if __name__ == "__main__":
    main()
