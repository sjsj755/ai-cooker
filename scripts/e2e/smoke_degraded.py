"""冒烟 4：拦截 recommend 返回 degraded=true，断言琥珀横幅 + notice 且页面不白屏。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import expect  # noqa: E402

from _common import BASE_URL, run_smoke  # noqa: E402


def smoke(page) -> None:
    page.goto(f"{BASE_URL}/", wait_until="domcontentloaded")

    payload = {
        "recipes": [
            {
                "recipe_id": 1,
                "title": "降级示例菜",
                "match_score": 0.8,
                "missing_ingredients": [],
                "difficulty": 1,
                "cook_time_minutes": 20,
                "steps": [{"instruction": "步骤一"}],
                "tips": None,
            }
        ],
        "degraded": True,
        "notice": "AI 文案不可用，已展示菜谱原文",
    }

    def fulfill(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payload, ensure_ascii=False),
        )

    page.route("**/api/recipes/recommend", fulfill)

    chip_input = page.locator("#ingredient-chips input")
    chip_input.fill("土豆")
    chip_input.press("Enter")
    page.locator("#recommend-btn").click()

    banner = page.locator("#banner .banner-warn")
    expect(banner).to_be_visible()
    expect(banner).to_contain_text("AI 文案不可用，已展示菜谱原文")
    expect(page.locator("#results-cards .card").first).to_be_visible()
    # 不白屏：提交按钮恢复可用，页面仍可交互
    expect(page.locator("#recommend-btn")).to_be_enabled()


def main() -> None:
    run_smoke(smoke, "smoke_degraded")


if __name__ == "__main__":
    main()
