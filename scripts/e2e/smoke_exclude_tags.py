"""冒烟 3：忌口多选提交，请求体携带 exclude_tags 与食材。

recommend 响应做 mock（理由见 smoke_recommend_happy），请求体由 route 捕获后断言。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import expect  # noqa: E402

from _common import BASE_URL, run_smoke  # noqa: E402


def smoke(page) -> None:
    page.goto(f"{BASE_URL}/", wait_until="domcontentloaded")

    tag_btn = page.locator("#tags-picker .tag-item").first
    expect(tag_btn).to_be_visible()
    tag_name = tag_btn.inner_text().strip()
    tag_btn.click()
    expect(tag_btn).to_have_class(re.compile("active"))

    captured: dict[str, str] = {}

    def capture(route):
        captured["body"] = route.request.post_data or ""
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "recipes": [
                        {
                            "recipe_id": 1,
                            "title": "示例菜",
                            "match_score": 0.9,
                            "missing_ingredients": [],
                            "difficulty": 1,
                            "cook_time_minutes": 15,
                            "steps": [{"instruction": "步骤一"}],
                            "tips": None,
                        }
                    ],
                    "degraded": False,
                    "notice": None,
                },
                ensure_ascii=False,
            ),
        )

    page.route("**/api/recipes/recommend", capture)
    chip_input = page.locator("#ingredient-chips input")
    chip_input.fill("土豆")
    chip_input.press("Enter")
    page.locator("#recommend-btn").click()

    expect(page.locator("#results-cards .card").first).to_be_visible()
    body = json.loads(captured["body"])
    assert "土豆" in body["ingredients"], body
    assert tag_name in body["exclude_tags"], body
    page.unroute("**/api/recipes/recommend")


def main() -> None:
    run_smoke(smoke, "smoke_exclude_tags")


if __name__ == "__main__":
    main()
