"""Tests for the S2 multi-platform access service (Phase 2.4 / 多平台接入)."""

import pytest

from src.knowledge.models import PlatformType
from src.knowledge.service import PlatformService


def test_connect_and_list_account():
    ps = PlatformService(provider_name="mock")
    acct = ps.connect_account("whatsapp", "wa-1", "Test Account")
    assert acct.account_id == "wa-1"
    assert acct.status.value == "active"
    assert len(ps.list_accounts()) == 1


def test_platform_codes_match_enum_values():
    ps = PlatformService(provider_name="mock")
    assert set(ps.list_platforms()) == {p.value for p in PlatformType}


def test_send_message_accepts_string_or_enum():
    ps = PlatformService(provider_name="mock")
    ps.connect_account(PlatformType.WHATSAPP, "wa-1")
    as_string = ps.send_message("whatsapp", "wa-1", "hi")
    as_enum = ps.send_message(PlatformType.WHATSAPP, "wa-1", "hi")
    assert as_string["platform"] == "whatsapp"
    assert as_enum["platform"] == "whatsapp"
    assert as_string["sent"] is True


def test_unknown_platform_is_rejected():
    ps = PlatformService(provider_name="mock")
    with pytest.raises(ValueError):
        ps.connect_account("not-a-real-platform", "x-1")


def test_get_account_round_trips():
    ps = PlatformService(provider_name="mock")
    ps.connect_account("telegram", "tg-9")
    got = ps.get_account("telegram", "tg-9")
    assert got is not None
    assert got.account_id == "tg-9"
    assert ps.get_account("telegram", "missing") is None
