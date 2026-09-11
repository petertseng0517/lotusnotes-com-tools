"""
Cloud Functions（2nd gen, Python）進入點。sdd3.md §5 存取控制的後端。

每個 @https_fn.on_request 是各自獨立網址的 function（不是同一支底下的路由）：
  verify               公開，LIFF 頁呼叫，輸入姓名/Notes ID + 驗證碼
  stores                公開，LIFF 頁呼叫，帶 LINE ID token 取商店清單
  bulletin              公開，LIFF 頁呼叫，帶 LINE ID token 取福利公告（sdd4.md）
  admin_push_stores    admin-only，本機 sync_stores_to_firestore.py 呼叫
  admin_rotate_code    admin-only，本機 broadcast_code.py 呼叫
  admin_import_roster  admin-only，本機 import_roster.py 呼叫
  apply                 公開，store/web/apply.html 呼叫，店家送出加入特約申請（sdd5.md）
  application_status    公開，store/web/apply_status.html 呼叫，帶申請編號+查詢碼查進度
  download_file         公開＋admin 兩種呼叫者皆可，下載店家自有合約書（sdd5.md §4.5.2）
  admin_list_applications   admin-only，本機 apply_review.py 呼叫
  admin_review_application  admin-only，本機 apply_review.py 呼叫
  admin_mark_contract_ready admin-only，本機 generate_contracts.py 呼叫（sdd5.md §4.5.1）

安全模型（見 sdd3.md §5 實作計畫「架構總覽」）：
- 這裡是唯一會碰 Firestore／Storage 的地方，前端/本機都不直接用相關 SDK。
- /verify、/stores、/bulletin、/apply、/application_status、/download_file 給瀏覽器
  呼叫，需要 CORS；/admin/* 只給本機腳本用 requests 打，不會有瀏覽器呼叫，不需要 CORS。
"""
from __future__ import annotations

import json
import os
import secrets

import requests
from firebase_admin import firestore, initialize_app
from firebase_functions import https_fn, options

import applications
import rate_limit
import storage_utils
from admin_auth import is_authorized
from codes import check_and_consume_code, generate_and_activate_code
from line_auth import LineVerifyError, verify_line_id_token
from roster_match import compute_revocations

initialize_app()

# 部署時把 ALLOWED_ORIGIN 設成正式站網域（例如 https://hlm.tzuchi.com.tw），
# 不要用 "*"——/stores 就是要擋掉「任何人都能拿網址直接抓」，來源限制才有意義。
_PUBLIC_CORS = options.CorsOptions(
    cors_origins=[os.environ.get("ALLOWED_ORIGIN", "https://REPLACE_WITH_REAL_DOMAIN")],
    cors_methods=["get", "post"],
)


def _json_response(payload: dict, status: int = 200) -> https_fn.Response:
    return https_fn.Response(json.dumps(payload, default=str), status=status, mimetype="application/json")


def _db():
    return firestore.client()


def _require_verified_user(req: https_fn.Request):
    """
    共用的「這個呼叫者是誰、驗證過了嗎」檢查，/stores、/bulletin 都要用一樣的邏輯，
    避免各自重寫一份、之後改壞其中一個沒同步改到另一個。

    回傳 (line_user_id, None) 表示通過；(None, error_response) 表示要直接回傳這個錯誤。
    """
    auth_header = req.headers.get("Authorization", "")
    id_token = auth_header[7:] if auth_header.startswith("Bearer ") else ""

    try:
        claims = verify_line_id_token(id_token)
    except LineVerifyError as e:
        print(f"{req.path}: LINE token rejected: {e}")
        return None, _json_response({"ok": False, "error": "invalid_id_token"}, 401)

    line_user_id = claims["sub"]
    auth_doc = _db().collection("lineAuth").document(line_user_id).get()
    if not auth_doc.exists or auth_doc.to_dict().get("status") != "verified":
        return None, _json_response({"ok": False, "error": "not_verified"}, 403)

    return line_user_id, None


@https_fn.on_request(cors=_PUBLIC_CORS, secrets=["LINE_LOGIN_CHANNEL_ID"])
def verify(req: https_fn.Request) -> https_fn.Response:
    if req.method != "POST":
        return _json_response({"ok": False, "error": "method_not_allowed"}, 405)

    body = req.get_json(silent=True) or {}
    id_token = body.get("idToken", "")
    notes_id = (body.get("notesId") or "").strip()
    code = (body.get("code") or "").strip()

    if not notes_id or not code:
        return _json_response({"ok": False, "error": "missing_fields"}, 400)

    try:
        claims = verify_line_id_token(id_token)
    except LineVerifyError as e:
        print(f"verify: LINE token rejected: {e}")
        return _json_response({"ok": False, "error": "invalid_id_token"}, 401)

    result = check_and_consume_code(_db(), claims["sub"], notes_id, code)
    status = 200 if result["ok"] else (423 if result.get("locked") else 401)
    return _json_response(result, status)


@https_fn.on_request(cors=_PUBLIC_CORS, secrets=["LINE_LOGIN_CHANNEL_ID"])
def stores(req: https_fn.Request) -> https_fn.Response:
    _, error = _require_verified_user(req)
    if error:
        return error

    data_doc = _db().collection("storeData").document("current").get()
    payload = data_doc.to_dict() if data_doc.exists else {"generated_at": None, "stores": []}
    return _json_response(payload)


@https_fn.on_request(cors=_PUBLIC_CORS, secrets=["LINE_LOGIN_CHANNEL_ID", "BULLETIN_SECRET_SLUG"])
def bulletin(req: https_fn.Request) -> https_fn.Response:
    """
    福利公告查詢（sdd4.md）。公告資料不存 Firestore，存在 Ubuntu 網站伺服器一個沒有
    任何頁面連結、路徑是亂碼的目錄下——這裡驗證身分通過後才代替呼叫者去抓那個網址，
    瀏覽器/LIFF 頁本身永遠不知道那個網址，見 bulletin/deploy_bulletin.py。
    """
    _, error = _require_verified_user(req)
    if error:
        return error

    base_url = os.environ.get("BULLETIN_BASE_URL", "").rstrip("/")
    slug = os.environ.get("BULLETIN_SECRET_SLUG", "")
    if not base_url or not slug:
        return _json_response({"ok": False, "error": "server_misconfigured"}, 500)

    try:
        resp = requests.get(f"{base_url}/{slug}/bulletin.json", timeout=10)
    except requests.RequestException as e:
        print(f"bulletin: upstream fetch failed: {e}")
        return _json_response({"ok": False, "error": "upstream_error"}, 502)

    if resp.status_code != 200:
        print(f"bulletin: upstream returned {resp.status_code}")
        return _json_response({"ok": False, "error": "upstream_error"}, 502)

    data = resp.json()
    image_base = f"{base_url}/{slug}/images"
    for b in data.get("bulletins", []):
        b["images"] = [f"{image_base}/{name}" for name in b.get("images", [])]

    return _json_response(data)


@https_fn.on_request(secrets=["ADMIN_SHARED_SECRET"])
def admin_push_stores(req: https_fn.Request) -> https_fn.Response:
    if not is_authorized(req.headers):
        return _json_response({"ok": False, "error": "unauthorized"}, 401)

    body = req.get_json(silent=True) or {}
    stores_list = body.get("stores")
    if not isinstance(stores_list, list):
        return _json_response({"ok": False, "error": "bad_payload"}, 400)

    _db().collection("storeData").document("current").set(
        {"generated_at": body.get("generated_at"), "stores": stores_list}
    )
    return _json_response({"ok": True, "count": len(stores_list)})


@https_fn.on_request(secrets=["ADMIN_SHARED_SECRET"])
def admin_rotate_code(req: https_fn.Request) -> https_fn.Response:
    if not is_authorized(req.headers):
        return _json_response({"ok": False, "error": "unauthorized"}, 401)

    body = req.get_json(silent=True) or {}
    label = (body.get("label") or "").strip()
    if not label:
        return _json_response({"ok": False, "error": "missing_label"}, 400)
    days_valid = body.get("daysValid", 90)
    result = generate_and_activate_code(_db(), label=label, days_valid=days_valid)
    return _json_response({"ok": True, **result})


@https_fn.on_request(secrets=["ADMIN_SHARED_SECRET"])
def admin_import_roster(req: https_fn.Request) -> https_fn.Response:
    if not is_authorized(req.headers):
        return _json_response({"ok": False, "error": "unauthorized"}, 401)

    body = req.get_json(silent=True) or {}
    roster = body.get("roster")
    force = bool(body.get("force", False))
    commit = bool(body.get("commit", False))

    if not isinstance(roster, list):
        return _json_response({"ok": False, "error": "bad_payload"}, 400)

    db = _db()
    verified_docs = db.collection("lineAuth").where("status", "==", "verified").stream()
    verified_users = [
        {"lineUserId": doc.id, "notesId": doc.to_dict().get("notesId", "")} for doc in verified_docs
    ]

    result = compute_revocations(verified_users, roster, force=force)

    if result["ok"] and commit and result["toRevoke"]:
        now = firestore.SERVER_TIMESTAMP
        batch = db.batch()
        for user in result["toRevoke"]:
            ref = db.collection("lineAuth").document(user["lineUserId"])
            batch.set(ref, {"status": "revoked", "revokedAt": now}, merge=True)
        batch.commit()
        result["committed"] = True
    else:
        result["committed"] = False

    return _json_response(result, 200 if result["ok"] else 400)


def _client_ip(req: https_fn.Request) -> str:
    """Cloud Functions 前面有代理，真正的來源 IP 在 X-Forwarded-For 的第一個值。"""
    forwarded = req.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return req.remote_addr or "unknown"


@https_fn.on_request(cors=_PUBLIC_CORS)
def apply(req: https_fn.Request) -> https_fn.Response:
    """
    店家線上申請加入特約（sdd5.md §4.3、§4.4）。完全公開、無需登入，必須假設會被
    任何人呼叫——欄位驗證、蜜罐欄位、IP 頻率限制都在這裡把關，不能只信任前端。

    選「使用店家制式範本」時，前端用 multipart/form-data 把檔案跟其他欄位一起送來；
    選「使用慈濟醫院合約範本」則是一般 JSON body，兩種都要能處理。
    """
    if req.method != "POST":
        return _json_response({"ok": False, "error": "method_not_allowed"}, 405)

    is_multipart = (req.content_type or "").startswith("multipart/form-data")
    if is_multipart:
        data = dict(req.form)
        # multipart 表單值全部是字串，consentPersonalData 要轉回 bool 才能通過驗證。
        data["consentPersonalData"] = str(data.get("consentPersonalData", "")).lower() in ("true", "1", "on")
    else:
        data = req.get_json(silent=True) or {}

    if applications.is_honeypot_triggered(data):
        # 靜默拒絕：回一個看起來成功的假結果，但不寫入 Firestore、不佔用 IP 頻率額度，
        # 不讓填表機器人知道自己被擋下來（sdd5.md §4.3）。
        return _json_response({"ok": True, "applicationId": "00000000-00", "queryCode": "00000000"})

    ip = _client_ip(req)
    if not rate_limit.check_and_increment(_db(), ip):
        return _json_response({"ok": False, "error": "rate_limited"}, 429)

    error = applications.validate_application_fields(data)
    if error:
        return _json_response({"ok": False, "error": error}, 400)

    own_template_storage_path = None
    if data.get("contractSource") == applications.CONTRACT_SOURCE_OWN:
        file_storage = req.files.get("ownTemplateFile") if is_multipart else None
        if file_storage is None or not file_storage.filename:
            return _json_response({"ok": False, "error": "missing_own_template_file"}, 400)

        file_bytes = file_storage.read()
        content_type = file_storage.mimetype or "application/octet-stream"
        file_error = applications.validate_own_template_file(content_type, len(file_bytes))
        if file_error:
            return _json_response({"ok": False, "error": file_error}, 400)

        ext = applications.extension_for_content_type(content_type)
        token = secrets.token_hex(16)
        own_template_storage_path = f"applicantUploads/{token}/draft_contract{ext}"
        storage_utils.upload_bytes(own_template_storage_path, file_bytes, content_type)

    result = applications.create_application(_db(), data, ip, own_template_storage_path)
    return _json_response({"ok": True, **result})


@https_fn.on_request(cors=_PUBLIC_CORS)
def application_status(req: https_fn.Request) -> https_fn.Response:
    """申請人自助查詢進度（sdd5.md §4.6）。查詢碼錯誤一律回「查無資料」，不透露是
    編號錯還是碼錯，避免被拿來枚舉。"""
    if req.method != "POST":
        return _json_response({"ok": False, "error": "method_not_allowed"}, 405)

    body = req.get_json(silent=True) or {}
    application_id = (body.get("applicationId") or "").strip()
    query_code = (body.get("queryCode") or "").strip()
    if not application_id or not query_code:
        return _json_response({"ok": False, "error": "missing_fields"}, 400)

    application = applications.get_application(_db(), application_id)
    if not application or not applications.check_query_code(application, query_code):
        return _json_response({"ok": False, "error": "not_found"}, 404)

    payload = {
        "ok": True,
        "status": application["status"],
        "storeName": application["storeName"],
        "reviewNote": application.get("reviewNote") or "",
        "contractSource": application.get("contractSource"),
        # hospital_template 的 contractUrl 是 Ubuntu 上的直接網址（見 §4.5.1），
        # own_template 的檔案在 Storage 裡，前端要自己組 download_file 的網址，
        # 不能直接把 Storage 路徑當網址回傳給瀏覽器。
        "contractUrl": application.get("contractUrl") if application.get("contractSource") == applications.CONTRACT_SOURCE_HOSPITAL else None,
    }
    return _json_response(payload)


@https_fn.on_request(cors=_PUBLIC_CORS, secrets=["ADMIN_SHARED_SECRET"])
def download_file(req: https_fn.Request) -> https_fn.Response:
    """
    店家自有合約書下載（sdd5.md §4.5.2）。這是本設計裡 Firebase Storage 檔案唯一的
    對外出口——bucket 規則整個鎖死，一律經這裡的 Admin SDK 讀取，存取權限判斷在
    這裡做，不是靠 Storage 規則本身。

    兩種呼叫者，任一通過即可：
      - 申請人自己：帶 applicationId + queryCode（跟 application_status 同一套驗證）
      - 職工福利小組審核用：帶 X-Admin-Secret（apply_review.py 呼叫，下載下來人工看內容）
    """
    application_id = (req.args.get("applicationId") or "").strip()
    kind = (req.args.get("kind") or "").strip()
    if kind != "own_template":
        return _json_response({"ok": False, "error": "unsupported_kind"}, 400)

    application = applications.get_application(_db(), application_id)
    if not application:
        return _json_response({"ok": False, "error": "not_found"}, 404)

    query_code = (req.args.get("queryCode") or "").strip()
    authorized = is_authorized(req.headers) or applications.check_query_code(application, query_code)
    if not authorized:
        return _json_response({"ok": False, "error": "not_found"}, 404)

    storage_path = application.get("ownTemplateStoragePath")
    if not storage_path:
        return _json_response({"ok": False, "error": "not_found"}, 404)

    result = storage_utils.read_bytes(storage_path)
    if result is None:
        return _json_response({"ok": False, "error": "not_found"}, 404)

    file_bytes, content_type = result
    return https_fn.Response(
        file_bytes,
        status=200,
        mimetype=content_type,
        headers={"Content-Disposition": 'attachment; filename="contract"'},
    )


@https_fn.on_request(secrets=["ADMIN_SHARED_SECRET"])
def admin_mark_contract_ready(req: https_fn.Request) -> https_fn.Response:
    """本機 generate_contracts.py 套版、上傳 PDF 完成後呼叫（sdd5.md §4.5.1）。"""
    if not is_authorized(req.headers):
        return _json_response({"ok": False, "error": "unauthorized"}, 401)

    body = req.get_json(silent=True) or {}
    application_id = (body.get("applicationId") or "").strip()
    contract_url = (body.get("contractUrl") or "").strip()
    if not application_id or not contract_url:
        return _json_response({"ok": False, "error": "missing_fields"}, 400)

    result = applications.mark_contract_ready(_db(), application_id, contract_url)
    return _json_response(result, 200 if result.get("ok") else 400)


@https_fn.on_request(secrets=["ADMIN_SHARED_SECRET"])
def admin_list_applications(req: https_fn.Request) -> https_fn.Response:
    if not is_authorized(req.headers):
        return _json_response({"ok": False, "error": "unauthorized"}, 401)

    status = req.args.get("status") or None
    results = applications.list_applications(_db(), status=status)
    return _json_response({"ok": True, "applications": results})


@https_fn.on_request(secrets=["ADMIN_SHARED_SECRET"])
def admin_review_application(req: https_fn.Request) -> https_fn.Response:
    if not is_authorized(req.headers):
        return _json_response({"ok": False, "error": "unauthorized"}, 401)

    body = req.get_json(silent=True) or {}
    application_id = (body.get("applicationId") or "").strip()
    decision = (body.get("decision") or "").strip()
    review_note = (body.get("reviewNote") or "").strip()
    contract_start_date = (body.get("contractStartDate") or "").strip() or None
    contract_end_date = (body.get("contractEndDate") or "").strip() or None

    if not application_id or not decision:
        return _json_response({"ok": False, "error": "missing_fields"}, 400)

    result = applications.review_application(
        _db(), application_id, decision, review_note, contract_start_date, contract_end_date
    )
    return _json_response(result, 200 if result.get("ok") else 400)
