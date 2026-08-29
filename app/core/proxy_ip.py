"""可信代理 IP 解析与客户端真实 IP 提取（P6）。

设计（对应 docs/P6_PLAN.md §3.3）：
- 不依赖 uvicorn ``--proxy-headers``（避免信任任意上游伪造头）；
- ``BEHIND_PROXY=false`` 时直接返回直连 IP（``request.client.host``）；
- ``BEHIND_PROXY=true`` 时取 ``X-Forwarded-For``，按逗号拆分成条目，
  从右到左扫描，跳过 ``FORWARDED_ALLOW_IPS`` 中可信 IP/CIDR，
  返回第一个不可信条目（真实客户端）；
- XFF 缺失 / 条目为空 / 含非法 IP / 全部为可信条目 → 回退直连 IP 并记录
  WARN（此时按代理 IP 限流，不静默放过）。
"""

from __future__ import annotations

import ipaddress
import logging
from typing import Any

from app.core.logging import get_logger, log_event

logger = get_logger("app.core.proxy_ip")


def parse_trusted_networks(spec: str) -> list[ipaddress._BaseNetwork]:
    """解析逗号分隔的 IP/CIDR 白名单；空串返回空列表；非法条目直接抛 ValueError（fail-fast）。"""
    networks: list[ipaddress._BaseNetwork] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "/" in part:
            networks.append(ipaddress.ip_network(part, strict=False))
        else:
            addr = ipaddress.ip_address(part)
            prefix = 128 if addr.version == 6 else 32
            networks.append(ipaddress.ip_network(f"{addr}/{prefix}", strict=False))
    return networks


def _is_valid_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def _is_trusted(
    value: str, trusted: list[ipaddress._BaseNetwork]
) -> bool:
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        return False
    return any(addr in net for net in trusted)


def _direct_ip(request: Any) -> str:
    client = getattr(request, "client", None)
    if client is None:
        return "unknown"
    return client.host or "unknown"


def _warn_fallback(direct: str, reason: str, xff: str) -> None:
    log_event(
        logger,
        logging.WARNING,
        "proxy_ip.fallback",
        reason=reason,
        direct=direct,
        xff=xff,
    )


def get_client_ip(request: Any) -> str:
    """返回用于限流 / 反馈指纹的客户端 IP。

    ``request`` 需提供 ``request.client.host`` 与 ``request.headers.get(...)``
    （Starlette Request 与测试桩均满足）。代理配置懒加载自 Settings，
    避免与 app.config 的配置校验形成循环导入。
    """
    from app.config import get_settings

    settings = get_settings()
    direct = _direct_ip(request)
    if not settings.behind_proxy:
        return direct

    xff = request.headers.get("x-forwarded-for", "") or ""
    entries = [entry.strip() for entry in xff.split(",")]
    if not xff.strip() or any(not entry for entry in entries):
        _warn_fallback(direct, "xff_missing_or_empty", xff)
        return direct
    if not all(_is_valid_ip(entry) for entry in entries):
        _warn_fallback(direct, "xff_invalid_ip", xff)
        return direct

    trusted = parse_trusted_networks(settings.forwarded_allow_ips)
    # XFF 形如「客户端, 代理1, 代理2, …」，最右为离服务器最近的代理追加；
    # 右起第一个非可信条目即真实客户端。
    for entry in reversed(entries):
        if not _is_trusted(entry, trusted):
            return entry
    _warn_fallback(direct, "xff_all_trusted", xff)
    return direct
