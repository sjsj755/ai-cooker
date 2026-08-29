"""冒烟 6：搜索页详情抽屉打开并展示步骤 / 来源外链。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import expect  # noqa: E402

from _common import BASE_URL, run_smoke  # noqa: E402


def smoke(page) -> None:
    page.goto(f"{BASE_URL}/search.html", wait_until="domcontentloaded")

    page.locator("#search-q").fill("土豆")
    page.locator("#search-btn").click()

    first_card = page.locator("#search-results-cards .card").first
    expect(first_card).to_be_visible()
    first_card.get_by_role("button", name="查看详情").click()

    drawer = page.locator(".drawer")
    expect(drawer).to_be_visible()
    expect(drawer.locator(".recipe-steps").first).to_be_visible()
    source = drawer.locator(".drawer-source")
    expect(source).to_be_visible()
    assert source.get_attribute("rel") == "noopener noreferrer"
    assert source.get_attribute("target") == "_blank"


def main() -> None:
    run_smoke(smoke, "smoke_search_detail")


if __name__ == "__main__":
    main()
