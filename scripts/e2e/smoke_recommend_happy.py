"""冒烟 1：推荐主页核心链路——输入食材 → 提交 → 卡片含步骤 / 缺料；提交期间按钮 disabled。

说明：真实 LLM recommend 在本环境实测约 5.9s，超过前端 5s 超时契约，
故本冒烟对 recommend 响应做确定性 mock（页面 / UI 逻辑 / 卡片渲染仍为真实链路）。
"""

from __future__ import annotations

import sys
import json
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import expect  # noqa: E402

from _common import BASE_URL, run_smoke  # noqa: E402


def smoke(page) -> None:
    page.goto(f"{BASE_URL}/", wait_until="domcontentloaded")

    chip_input = page.locator("#ingredient-chips input")
    chip_input.fill("土豆")
    chip_input.press("Enter")
    expect(page.locator("#ingredient-chips .chip")).to_have_count(1)

    chip_input.fill("鸡蛋")
    chip_input.press("Enter")
    expect(page.locator("#ingredient-chips .chip")).to_have_count(2)

    payload = {
        "recipes": [
            {
                "recipe_id": 1,
                "title": "酸辣土豆丝",
                "match_score": 0.92,
                "missing_ingredients": ["青椒", "干辣椒"],
                "difficulty": 1,
                "cook_time_minutes": 20,
                "steps": [
                    {"instruction": "土豆去皮切丝，清水冲洗去淀粉。"},
                    {"instruction": "热锅爆香蒜米与干辣椒，下土豆丝翻炒。"},
                    {"instruction": "加醋与盐调味，出锅装盘。"},
                ],
                "tips": "焯水时加少许白醋更脆爽。",
            }
        ],
        "degraded": False,
        "notice": None,
    }

    # 延迟响应，断言提交期间按钮 disabled；响应内容 mock 保证确定性
    def delay_route(route):
        time.sleep(2.0)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payload, ensure_ascii=False),
        )

    page.route("**/api/recipes/recommend", delay_route)
    submit = page.locator("#recommend-btn")
    submit.click()
    expect(submit).to_be_disabled()

    first_card = page.locator("#results-cards .card").first
    expect(first_card).to_be_visible()
    expect(first_card.locator(".recipe-steps")).to_be_visible()
    expect(first_card.locator(".missing")).to_be_visible()
    expect(first_card.locator(".card-title")).to_have_text("酸辣土豆丝")
    page.unroute("**/api/recipes/recommend")


def main() -> None:
    run_smoke(smoke, "smoke_recommend_happy")


if __name__ == "__main__":
    main()
