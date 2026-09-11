import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from merchant_binding import parse_bind_text


def test_parse_bind_text_normal():
    assert parse_bind_text("20260909-03 AB12CD34") == ("20260909-03", "AB12CD34")


def test_parse_bind_text_extra_whitespace():
    assert parse_bind_text("  20260909-03   AB12CD34  ") == ("20260909-03", "AB12CD34")


def test_parse_bind_text_fullwidth_space():
    # LINE 手機輸入法有時候會打出全形空白，Python str.split() 預設就能處理。
    assert parse_bind_text("20260909-03　AB12CD34") == ("20260909-03", "AB12CD34")


def test_parse_bind_text_missing_parts():
    assert parse_bind_text("20260909-03") is None
    assert parse_bind_text("") is None


def test_parse_bind_text_too_many_parts():
    assert parse_bind_text("20260909-03 AB12CD34 extra") is None
