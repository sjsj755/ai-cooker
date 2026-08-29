"""Playwright 冒烟脚本公共工具。

前置条件：本地 FastAPI 服务已启动且数据库已 seed
（E2E_BASE_URL 默认 http://127.0.0.1:8000，可用环境变量覆盖）。
"""

from __future__ import annotations

import os
from typing import Callable

from playwright.sync_api import Page, sync_playwright

BASE_URL = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8000")
DEFAULT_TIMEOUT_MS = 15000


def run_smoke(smoke: Callable[[Page], None], name: str) -> None:
    """启动 headless Chromium 执行冒烟脚本，失败以非零退出码结束。"""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(DEFAULT_TIMEOUT_MS)
        try:
            smoke(page)
            print(f"PASS {name}")
        finally:
            browser.close()
