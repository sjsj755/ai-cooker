"""冒烟 2：食材联想下拉出现并可点选插入 chip。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import expect  # noqa: E402

from _common import BASE_URL, run_smoke  # noqa: E402


def smoke(page) -> None:
    page.goto(f"{BASE_URL}/", wait_until="domcontentloaded")

    chip_input = page.locator("#ingredient-chips input")
    chip_input.fill("土豆")

    suggest_list = page.locator("#ingredient-suggest .suggest-list")
    expect(suggest_list).to_be_visible()
    first_item = suggest_list.locator(".suggest-item").first
    expect(first_item).to_be_visible()

    first_item.click()
    expect(page.locator("#ingredient-chips .chip")).to_have_count(1)
    expect(chip_input).to_have_value("")


def main() -> None:
    run_smoke(smoke, "smoke_autocomplete")


if __name__ == "__main__":
    main()
