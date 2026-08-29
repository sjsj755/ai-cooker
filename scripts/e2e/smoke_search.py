"""冒烟 5：搜索页检索并渲染结果列表。"""

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
    expect(first_card.locator(".card-title")).to_be_visible()
    expect(first_card.locator(".missing")).to_be_visible()


def main() -> None:
    run_smoke(smoke, "smoke_search")


if __name__ == "__main__":
    main()
