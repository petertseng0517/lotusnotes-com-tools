import base64
import hashlib
import hmac
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from line_webhook_auth import verify_signature


def _sign(secret: str, body: bytes) -> str:
    return base64.b64encode(hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()).decode("utf-8")


def test_verify_signature_matches():
    body = b'{"events": []}'
    signature = _sign("topsecret", body)
    assert verify_signature("topsecret", body, signature) is True


def test_verify_signature_wrong_secret():
    body = b'{"events": []}'
    signature = _sign("topsecret", body)
    assert verify_signature("othersecret", body, signature) is False


def test_verify_signature_tampered_body():
    body = b'{"events": []}'
    signature = _sign("topsecret", body)
    assert verify_signature("topsecret", body + b"tampered", signature) is False


def test_verify_signature_empty_inputs():
    assert verify_signature("", b"body", "sig") is False
    assert verify_signature("secret", b"body", "") is False
