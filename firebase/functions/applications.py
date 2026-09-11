"""
特約商店線上申請（sdd5.md）核心邏輯：驗證表單欄位、產生申請編號/查詢碼、
建立/查詢/審核 storeApplications 文件。main.py 的 apply／application_status／
admin_list_applications／admin_review_application 端點都呼叫這裡，不直接操作
Firestore，好讓這些邏輯可以脫離 Cloud Functions 環境單獨測試（比照 codes.py）。

狀態機（sdd5.md §4.2）：
    pending -> approved -> contract_ready -> merchant_signed -> completed
            -> rejected
自動套版（generate_contracts.py，sdd5.md §4.5.1）跟 LINE 身分綁定／用印回傳
（sdd5.md §4.7、§4.8）是 Phase 2／Phase 3 才做的事，這裡只實作到「核准後，
若為 own_template 直接進 contract_ready；若為 hospital_template 停在 approved，
等 Phase 2 的 generate_contracts.py 接手」。
"""
from __future__ import annotations

import hmac
import re
import secrets as pysecrets
import string
from datetime import datetime, timezone

from google.cloud import firestore

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_CONTRACT_READY = "contract_ready"
STATUS_MERCHANT_SIGNED = "merchant_signed"
STATUS_COMPLETED = "completed"
STATUS_ABANDONED = "abandoned"

CONTRACT_SOURCE_HOSPITAL = "hospital_template"
CONTRACT_SOURCE_OWN = "own_template"
CONTRACT_SOURCES = (CONTRACT_SOURCE_HOSPITAL, CONTRACT_SOURCE_OWN)

TAX_ID_RE = re.compile(r"^\d{8}$")
SHORT_FIELD_MAX = 200
LONG_FIELD_MAX = 2000

REQUIRED_SHORT_FIELDS = ("storeName", "ownerName", "address", "phone", "email", "businessCategory")
OPTIONAL_SHORT_FIELDS = ("contactPerson",)
REQUIRED_LONG_FIELDS = ("discountContent",)
OPTIONAL_LONG_FIELDS = ("note",)

# 蜜罐欄位：一般使用者在 apply.html 上看不到，只有機器人爬蟲填表機會填。收到非空值
# 直接當成濫用，回傳假的成功結果但不寫入 Firestore（見 main.py apply()）。
HONEYPOT_FIELD = "website"

# 店家自有合約書上傳限制（sdd5.md §4.5.2、§5、§6#14，暫定值，之後可視實務調整）。
ALLOWED_OWN_TEMPLATE_CONTENT_TYPES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
MAX_OWN_TEMPLATE_BYTES = 10 * 1024 * 1024

_OWN_TEMPLATE_EXTENSIONS = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}

# 查詢碼：排除容易看錯/唸錯的字元（0/O、1/I/L），跟 codes.py 的驗證碼同一套考量。
QUERY_CODE_ALPHABET = "".join(c for c in string.ascii_uppercase + string.digits if c not in "0O1IL")
QUERY_CODE_LENGTH = 8


def is_honeypot_triggered(data: dict) -> bool:
    return bool((data.get(HONEYPOT_FIELD) or "").strip())


def extension_for_content_type(content_type: str) -> str:
    return _OWN_TEMPLATE_EXTENSIONS.get(content_type, "")


def validate_own_template_file(content_type: str, size: int) -> str | None:
    """回傳 None 表示通過，否則回傳錯誤代碼。"""
    if content_type not in ALLOWED_OWN_TEMPLATE_CONTENT_TYPES:
        return "invalid_file_type"
    if size <= 0 or size > MAX_OWN_TEMPLATE_BYTES:
        return "file_too_large"
    return None


def validate_application_fields(data: dict) -> str | None:
    """回傳 None 表示通過，否則回傳一個機器可讀的錯誤代碼。前端/後端都要驗證一次
    （sdd5.md §5：apply 是完全公開的端點，不能只信任前端）。"""
    for field in REQUIRED_SHORT_FIELDS:
        value = (data.get(field) or "").strip()
        if not value:
            return f"missing_{field}"
        if len(value) > SHORT_FIELD_MAX:
            return f"{field}_too_long"

    for field in OPTIONAL_SHORT_FIELDS:
        if len(data.get(field) or "") > SHORT_FIELD_MAX:
            return f"{field}_too_long"

    for field in REQUIRED_LONG_FIELDS:
        value = (data.get(field) or "").strip()
        if not value:
            return f"missing_{field}"
        if len(value) > LONG_FIELD_MAX:
            return f"{field}_too_long"

    for field in OPTIONAL_LONG_FIELDS:
        if len(data.get(field) or "") > LONG_FIELD_MAX:
            return f"{field}_too_long"

    if not TAX_ID_RE.match((data.get("taxId") or "").strip()):
        return "invalid_tax_id"

    if data.get("contractSource") not in CONTRACT_SOURCES:
        return "invalid_contract_source"

    if data.get("consentPersonalData") is not True:
        return "consent_required"

    return None


def generate_query_code() -> str:
    return "".join(pysecrets.choice(QUERY_CODE_ALPHABET) for _ in range(QUERY_CODE_LENGTH))


def _next_application_id(db: firestore.Client, today: str) -> str:
    """每天各自獨立編號，格式 {YYYYMMDD}-{序號}（例如 20260909-03），方便電話溝通報號碼。
    用 transaction 避免同一秒內多筆申請拿到同一個序號。"""
    counter_ref = db.collection("applicationCounters").document(today)
    transaction = db.transaction()

    @firestore.transactional
    def _run(transaction: firestore.Transaction) -> int:
        snap = counter_ref.get(transaction=transaction)
        seq = (snap.to_dict().get("seq", 0) if snap.exists else 0) + 1
        transaction.set(counter_ref, {"seq": seq})
        return seq

    seq = _run(transaction)
    return f"{today}-{seq:02d}"


def create_application(db: firestore.Client, data: dict, ip: str, own_template_storage_path: str | None) -> dict:
    """呼叫前必須先通過 validate_application_fields()，這裡不重複做欄位驗證。"""
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    application_id = _next_application_id(db, today)
    query_code = generate_query_code()

    doc = {
        "storeName": data["storeName"].strip(),
        "ownerName": data["ownerName"].strip(),
        "taxId": data["taxId"].strip(),
        "address": data["address"].strip(),
        "phone": data["phone"].strip(),
        "email": data["email"].strip(),
        "contactPerson": (data.get("contactPerson") or "").strip(),
        "businessCategory": data["businessCategory"].strip(),
        "discountContent": data["discountContent"].strip(),
        "note": (data.get("note") or "").strip(),
        "contractSource": data["contractSource"],
        "ownTemplateStoragePath": own_template_storage_path,
        "consentPersonalData": True,
        "queryCode": query_code,
        "status": STATUS_PENDING,
        "reviewNote": "",
        "contractUrl": None,
        "contractStartDate": None,
        "contractEndDate": None,
        "finalDocUrl": None,
        "merchantSignedFileUrl": None,
        "submittedIp": ip,
        "submittedAt": firestore.SERVER_TIMESTAMP,
        "reviewedAt": None,
        "contractGeneratedAt": None,
        "merchantSignedAt": None,
        "completedAt": None,
    }
    db.collection("storeApplications").document(application_id).set(doc)
    return {"applicationId": application_id, "queryCode": query_code}


def get_application(db: firestore.Client, application_id: str) -> dict | None:
    doc = db.collection("storeApplications").document(application_id).get()
    if not doc.exists:
        return None
    data = doc.to_dict()
    data["applicationId"] = doc.id
    return data


def check_query_code(application: dict, query_code: str) -> bool:
    """常數時間比對，避免 timing attack，跟 admin_auth.check_secret 同樣考量。"""
    expected = application.get("queryCode", "")
    return bool(expected) and hmac.compare_digest(query_code or "", expected)


def mark_contract_ready(db: firestore.Client, application_id: str, contract_url: str) -> dict:
    """Phase 2（generate_contracts.py）套版產生 PDF、上傳完成後回寫用（sdd5.md §4.5.1）。
    只有已核准（approved）的 hospital_template 申請可以轉 contract_ready；own_template
    的申請核准當下就已經直接轉 contract_ready 了（見 review_application），不會走到這裡。"""
    ref = db.collection("storeApplications").document(application_id)
    snap = ref.get()
    if not snap.exists:
        return {"ok": False, "error": "not_found"}
    if snap.to_dict().get("status") != STATUS_APPROVED:
        return {"ok": False, "error": "not_approved"}

    ref.update({
        "status": STATUS_CONTRACT_READY,
        "contractUrl": contract_url,
        "contractGeneratedAt": firestore.SERVER_TIMESTAMP,
    })
    return {"ok": True, "status": STATUS_CONTRACT_READY}


def list_applications(db: firestore.Client, status: str | None = None) -> list[dict]:
    query = db.collection("storeApplications")
    if status:
        query = query.where("status", "==", status)
    results = []
    for doc in query.stream():
        data = doc.to_dict()
        data["applicationId"] = doc.id
        results.append(data)
    results.sort(key=lambda a: a.get("applicationId", ""))
    return results


def review_application(
    db: firestore.Client,
    application_id: str,
    decision: str,
    review_note: str,
    contract_start_date: str | None = None,
    contract_end_date: str | None = None,
) -> dict:
    """
    核准/婉拒一筆申請（sdd5.md §4.4、§4.5.2）。

    hospital_template 核准後停在 approved，等 generate_contracts.py（Phase 2）套版
    完才會轉 contract_ready；own_template 核准當下就直接轉 contract_ready，因為
    contractUrl 就是店家自己上傳的那份檔案，沒有套版這個動作（見 sdd5.md §4.5.2）。
    """
    if decision not in ("approved", "rejected"):
        return {"ok": False, "error": "invalid_decision"}

    ref = db.collection("storeApplications").document(application_id)
    snap = ref.get()
    if not snap.exists:
        return {"ok": False, "error": "not_found"}

    data = snap.to_dict()
    if data.get("status") != STATUS_PENDING:
        return {"ok": False, "error": "not_pending"}

    update = {"reviewNote": review_note, "reviewedAt": firestore.SERVER_TIMESTAMP}

    if decision == "rejected":
        update["status"] = STATUS_REJECTED
        ref.update(update)
        return {"ok": True, "status": STATUS_REJECTED}

    if data.get("contractSource") == CONTRACT_SOURCE_HOSPITAL:
        if not contract_start_date or not contract_end_date:
            return {"ok": False, "error": "missing_contract_dates"}
        update["status"] = STATUS_APPROVED
        update["contractStartDate"] = contract_start_date
        update["contractEndDate"] = contract_end_date
    else:
        storage_path = data.get("ownTemplateStoragePath")
        if not storage_path:
            return {"ok": False, "error": "missing_own_template_file"}
        update["status"] = STATUS_CONTRACT_READY
        update["contractUrl"] = storage_path
        update["contractGeneratedAt"] = firestore.SERVER_TIMESTAMP

    ref.update(update)
    return {"ok": True, "status": update["status"]}
