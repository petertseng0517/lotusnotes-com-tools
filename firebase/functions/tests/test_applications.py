import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import applications


def _valid_fields(**overrides):
    data = {
        "storeName": "阿美小吃",
        "ownerName": "王小明",
        "taxId": "12345678",
        "address": "花蓮市中華路1號",
        "phone": "03-1234567",
        "email": "test@example.com",
        "businessCategory": "餐飲",
        "discountContent": "消費滿百送飲料",
        "contractSource": applications.CONTRACT_SOURCE_HOSPITAL,
        "consentPersonalData": True,
    }
    data.update(overrides)
    return data


def test_validate_application_fields_accepts_valid_data():
    assert applications.validate_application_fields(_valid_fields()) is None


def test_validate_application_fields_rejects_missing_required_field():
    data = _valid_fields()
    data["storeName"] = ""
    assert applications.validate_application_fields(data) == "missing_storeName"


def test_validate_application_fields_rejects_bad_tax_id():
    assert applications.validate_application_fields(_valid_fields(taxId="1234567")) == "invalid_tax_id"
    assert applications.validate_application_fields(_valid_fields(taxId="1234567a")) == "invalid_tax_id"


def test_validate_application_fields_rejects_bad_contract_source():
    assert applications.validate_application_fields(_valid_fields(contractSource="other")) == "invalid_contract_source"


def test_validate_application_fields_requires_explicit_consent():
    assert applications.validate_application_fields(_valid_fields(consentPersonalData=False)) == "consent_required"
    assert applications.validate_application_fields(_valid_fields(consentPersonalData="true")) == "consent_required"


def test_validate_application_fields_rejects_overlong_field():
    assert applications.validate_application_fields(_valid_fields(storeName="a" * 201)) == "storeName_too_long"


def test_is_honeypot_triggered():
    assert applications.is_honeypot_triggered({applications.HONEYPOT_FIELD: "http://spam.example"}) is True
    assert applications.is_honeypot_triggered({applications.HONEYPOT_FIELD: "  "}) is False
    assert applications.is_honeypot_triggered({}) is False


def test_generate_query_code_length_and_alphabet():
    code = applications.generate_query_code()
    assert len(code) == applications.QUERY_CODE_LENGTH
    assert all(c in applications.QUERY_CODE_ALPHABET for c in code)


def test_check_query_code():
    application = {"queryCode": "ABCD1234"}
    assert applications.check_query_code(application, "ABCD1234") is True
    assert applications.check_query_code(application, "wrong") is False
    assert applications.check_query_code({}, "anything") is False


def test_validate_own_template_file():
    assert applications.validate_own_template_file("application/pdf", 1024) is None
    assert applications.validate_own_template_file("image/png", 1024) == "invalid_file_type"
    assert applications.validate_own_template_file("application/pdf", 0) == "file_too_large"
    assert applications.validate_own_template_file("application/pdf", applications.MAX_OWN_TEMPLATE_BYTES + 1) == "file_too_large"


def test_extension_for_content_type():
    assert applications.extension_for_content_type("application/pdf") == ".pdf"
    assert applications.extension_for_content_type("application/msword") == ".doc"
    assert applications.extension_for_content_type("unknown/type") == ""
