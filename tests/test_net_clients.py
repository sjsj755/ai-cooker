"""P6.4 共享 httpx 客户端：幂等关闭、关闭后可重建。"""

import asyncio

import app.core.net_clients as nc


def test_close_http_clients_idempotent():
    asyncio.run(nc.close_http_clients())
    asyncio.run(nc.close_http_clients())
    assert nc._clients == {}


def test_clients_recreated_after_close():
    async def scenario():
        c1 = nc.get_llm_http_client()
        await nc.close_http_clients()
        c2 = nc.get_llm_http_client()
        return c1 is not c2

    assert asyncio.run(scenario())
