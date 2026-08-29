"""P6 可信代理 IP：get_client_ip 右到左解析 / 回退 WARN / CIDR / 配置 fail-fast。"""

from types import SimpleNamespace

import pytest

from app.config import Settings
from app.core.proxy_ip import get_client_ip, parse_trusted_networks


class FakeRequest:
    """轻量测试桩：具备 request.client.host 与 request.headers.get。"""

    def __init__(self, host: str = "203.0.113.7", xff: str | None = None):
        self.client = SimpleNamespace(host=host)
        self.headers: dict[str, str] = {}
        if xff is not None:
            self.headers["x-forwarded-for"] = xff


def _proxy_settings(forwarded_allow_ips: str = "172.28.0.10") -> Settings:
    return Settings(
        behind_proxy=True,
        forwarded_allow_ips=forwarded_allow_ips,
    )


# ---------- parse_trusted_networks ----------


def test_parse_trusted_networks_supports_ip_and_cidr():
    nets = parse_trusted_networks("172.28.0.10, 10.0.0.0/8")
    assert len(nets) == 2
    assert nets[0].num_addresses == 1  # 精确 IP 展开为 /32
    assert nets[1].num_addresses == 2**24


def test_parse_trusted_networks_blank_ok():
    assert parse_trusted_networks("") == []
    assert parse_trusted_networks("  ,  ") == []


def test_parse_trusted_networks_invalid_raises():
    with pytest.raises(ValueError):
        parse_trusted_networks("not-an-ip")


# ---------- get_client_ip：BEHIND_PROXY=false ----------


def test_direct_ip_when_not_behind_proxy(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: Settings(behind_proxy=False, forwarded_allow_ips=""),
    )
    request = FakeRequest(host="9.9.9.9", xff="1.2.3.4")
    assert get_client_ip(request) == "9.9.9.9"


# ---------- get_client_ip：右到左解析 ----------


def test_single_xff_with_trusted_proxy(monkeypatch):
    monkeypatch.setattr("app.config.get_settings", _proxy_settings)
    request = FakeRequest(host="172.28.0.10", xff="1.2.3.4")
    assert get_client_ip(request) == "1.2.3.4"


def test_forged_multilevel_xff(monkeypatch):
    """客户端伪造多级代理：1.2.3.4, 172.28.0.10 → 右起第一个非可信 = 1.2.3.4。"""
    monkeypatch.setattr("app.config.get_settings", _proxy_settings)
    request = FakeRequest(host="172.28.0.10", xff="1.2.3.4, 172.28.0.10")
    assert get_client_ip(request) == "1.2.3.4"


def test_cidr_whitelist(monkeypatch):
    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: Settings(behind_proxy=True, forwarded_allow_ips="172.28.0.0/16"),
    )
    request = FakeRequest(host="172.28.0.10", xff="9.9.9.9, 172.28.1.2")
    assert get_client_ip(request) == "9.9.9.9"


def test_ipv6_xff(monkeypatch):
    monkeypatch.setattr("app.config.get_settings", _proxy_settings)
    request = FakeRequest(host="172.28.0.10", xff="2001:db8::1, 172.28.0.10")
    assert get_client_ip(request) == "2001:db8::1"


# ---------- get_client_ip：回退直连 + WARN ----------


def _capture_warns(monkeypatch) -> list[tuple[str, str]]:
    """捕获 proxy_ip WARN 事件（现有日志装配会清空 root handlers，caplog 不可靠）。"""
    recorded: list[tuple[str, str]] = []

    def fake_log_event(logger, level, event, **fields):
        recorded.append((event, fields.get("reason", "")))

    monkeypatch.setattr("app.core.proxy_ip.log_event", fake_log_event)
    return recorded


def test_missing_xff_falls_back_with_warn(monkeypatch):
    monkeypatch.setattr("app.config.get_settings", _proxy_settings)
    warns = _capture_warns(monkeypatch)
    request = FakeRequest(host="172.28.0.10", xff=None)
    assert get_client_ip(request) == "172.28.0.10"
    assert ("proxy_ip.fallback", "xff_missing_or_empty") in warns


def test_all_trusted_xff_falls_back_with_warn(monkeypatch):
    monkeypatch.setattr("app.config.get_settings", _proxy_settings)
    warns = _capture_warns(monkeypatch)
    request = FakeRequest(host="172.28.0.10", xff="172.28.0.10")
    assert get_client_ip(request) == "172.28.0.10"
    assert ("proxy_ip.fallback", "xff_all_trusted") in warns


def test_empty_entry_falls_back_with_warn(monkeypatch):
    monkeypatch.setattr("app.config.get_settings", _proxy_settings)
    warns = _capture_warns(monkeypatch)
    request = FakeRequest(host="172.28.0.10", xff="1.2.3.4, ")
    assert get_client_ip(request) == "172.28.0.10"
    assert ("proxy_ip.fallback", "xff_missing_or_empty") in warns


def test_invalid_xff_falls_back_with_warn(monkeypatch):
    monkeypatch.setattr("app.config.get_settings", _proxy_settings)
    warns = _capture_warns(monkeypatch)
    request = FakeRequest(host="172.28.0.10", xff="999.1.1.1, 172.28.0.10")
    assert get_client_ip(request) == "172.28.0.10"
    assert ("proxy_ip.fallback", "xff_invalid_ip") in warns


# ---------- 配置 fail-fast ----------


def test_settings_requires_forwarded_allow_ips_when_behind_proxy():
    with pytest.raises(ValueError, match="FORWARDED_ALLOW_IPS"):
        Settings(behind_proxy=True, forwarded_allow_ips="")


def test_settings_rejects_invalid_allow_ips():
    with pytest.raises(ValueError, match="FORWARDED_ALLOW_IPS"):
        Settings(behind_proxy=True, forwarded_allow_ips="not-an-ip")


def test_settings_direct_mode_allows_blank_whitelist():
    settings = Settings(behind_proxy=False, forwarded_allow_ips="")
    assert settings.behind_proxy is False


def test_settings_p6_defaults():
    settings = Settings()
    assert settings.docs_enabled is True
    assert settings.allowed_hosts == ""
    assert settings.security_headers_enabled is True


# ---------- 一致性：限流 key 与反馈指纹同一 IP 语义 ----------


def test_limiter_key_func_uses_client_ip(monkeypatch):
    """slowapi key_func 与 get_client_ip 共用同一解析（同请求同 key）。"""
    monkeypatch.setattr("app.config.get_settings", _proxy_settings)
    from app.core.rate_limit import client_ip_key_func

    assert client_ip_key_func(FakeRequest(host="172.28.0.10", xff="7.7.7.7")) == "7.7.7.7"
    assert client_ip_key_func(FakeRequest(host="172.28.0.10", xff="7.7.7.7")) == get_client_ip(
        FakeRequest(host="172.28.0.10", xff="7.7.7.7")
    )


def test_feedback_fingerprint_uses_client_ip(monkeypatch):
    """反馈指纹与限流桶基于同一 IP 解析：同 IP 同指纹。"""
    monkeypatch.setattr(
        "app.api.routes.feedback.get_settings",
        lambda: Settings(
            behind_proxy=True,
            forwarded_allow_ips="172.28.0.10",
            feedback_salt="test-salt",
        ),
    )
    from app.api.routes.feedback import client_fingerprint

    fp = client_fingerprint(FakeRequest(host="172.28.0.10", xff="7.7.7.7"))
    assert len(fp) == 64
    assert all(c in "0123456789abcdef" for c in fp)
    assert "7.7.7.7" not in fp  # 不落明文 IP
    assert client_fingerprint(
        FakeRequest(host="172.28.0.10", xff="7.7.7.7")
    ) == fp
