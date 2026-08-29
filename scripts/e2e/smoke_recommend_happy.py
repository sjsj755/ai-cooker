"""冒烟 1：推荐主页核心链路——输入食材 → 提交 → 卡片缺料 / 步骤折叠；
点击“做法”展开后断言步骤可见、aria-expanded=true、焦点留在按钮；提交期间按钮 disabled。

说明：真实 LLM recommend 在本环境实测约 5.9s，超过前端 5s 超时契约，
故本冒烟对 recommend 响应做确定性 mock（页面 / UI 逻辑 / 卡片渲染仍为真实链路）。
"""

from __future__ import annotations

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import expect  # noqa: E402

from _common import BASE_URL, run_smoke  # noqa: E402


def smoke(page) -> None:
    # 新版 Playwright 同步 API 的路由处理器运行在驱动线程，处理器内 sleep 会阻塞
    # 后续断言（click 返回时请求已完成、按钮已恢复）。改为在浏览器侧延迟请求：
    # POST（recommend）延迟 2s 断言“提交期间按钮 disabled”，详情 GET 延迟 1.5s
    # 断言加载骨架；响应仍由路由 mock。
    page.add_init_script(
        """
        (() => {
          const originalFetch = window.fetch;
          window.fetch = (...args) => {
            const init = args[1] || {};
            const url =
              typeof args[0] === "string"
                ? args[0]
                : (args[0] && args[0].url) || "";
            let delay = 0;
            if (init.method === "POST") {
              delay = 2000;
            } else if (/\\/api\\/recipes\\/\\d+$/.test(url)) {
              delay = 1500;
            }
            return new Promise((resolve, reject) =>
              setTimeout(() => originalFetch(...args).then(resolve, reject), delay)
            );
          };
        })();
        """
    )
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
                "seasonings": [
                    {"name": "盐", "amount": "适量"},
                    {"name": "食用油", "amount": "少许"},
                ],
            }
        ],
        "degraded": False,
        "notice": None,
    }

    # 断言提交期间按钮 disabled；响应内容 mock 保证确定性
    def mock_route(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payload, ensure_ascii=False),
        )

    page.route("**/api/recipes/recommend", mock_route)
    submit = page.locator("#recommend-btn")
    submit.click()
    expect(submit).to_be_disabled()

    first_card = page.locator("#results-cards .card").first
    expect(first_card).to_be_visible()
    expect(first_card.locator(".missing")).to_be_visible()
    expect(first_card.locator(".card-title")).to_have_text("酸辣土豆丝")

    # 默认折叠：步骤区不可见，按钮 aria-expanded=false
    steps_wrap = first_card.locator(".recipe-steps-wrap")
    toggle = first_card.locator(".card-toggle")
    expect(steps_wrap).to_be_hidden()
    expect(toggle).to_have_attribute("aria-expanded", "false")

    # 点击“做法”展开：步骤可见、aria-expanded=true、焦点留在按钮
    toggle.click()
    expect(first_card.locator(".recipe-steps")).to_be_visible()
    expect(toggle).to_have_attribute("aria-expanded", "true")
    expect(toggle).to_be_focused()

    # P4.2：所需调料行（与缺料区分）
    seasonings_row = first_card.locator(".seasonings")
    expect(seasonings_row).to_be_visible()
    expect(seasonings_row.locator(".chip-seasoning")).to_have_count(2)

    # P5：反馈闭环——收藏提交成功后按钮 disabled + aria-pressed=true（请求 mock）。
    # 放在抽屉交互之前，避免抽屉遮罩拦截点击。
    def mock_feedback(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"id": 1}, ensure_ascii=False),
        )

    page.route("**/api/feedback", mock_feedback)
    like_btn = first_card.locator('.feedback-btn[data-action="like"]')
    dislike_btn = first_card.locator('.feedback-btn[data-action="dislike"]')
    like_btn.click()
    expect(like_btn).to_be_disabled()
    expect(like_btn).to_have_attribute("aria-pressed", "true")
    expect(dislike_btn).to_be_disabled()
    expect(dislike_btn).to_have_attribute("aria-pressed", "false")

    # P4.2：查看详情 → 先出加载骨架，数据返回后展示食材 / 调料区块
    detail_payload = {
        "id": 1,
        "title": "酸辣土豆丝",
        "description": "经典家常菜，酸辣爽口。",
        "source_url": "https://example.com/recipe/1",
        "difficulty": 1,
        "cook_time_minutes": 20,
        "servings": 2,
        "steps": [{"instruction": "土豆去皮切丝，清水冲洗去淀粉。"}],
        "ingredients": [
            {"name": "土豆", "amount": "500g"},
            {"name": "鸡蛋", "amount": "2 个"},
        ],
        "seasonings": [
            {"name": "盐", "amount": "适量"},
            {"name": "食用油", "amount": "少许"},
        ],
    }
    page.route(
        "**/api/recipes/1",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(detail_payload, ensure_ascii=False),
        ),
    )

    first_card.get_by_role("button", name="查看详情").click()
    expect(page.locator(".drawer-loading")).to_be_visible()
    drawer = page.locator(".drawer")
    expect(drawer.locator(".drawer-ingredients")).to_be_visible()
    expect(drawer.locator(".drawer-seasonings")).to_be_visible()
    expect(drawer.locator(".drawer-ingredients li").first).to_contain_text("土豆")

    page.unroute("**/api/recipes/recommend")
    page.unroute("**/api/recipes/1")
    page.unroute("**/api/feedback")


def main() -> None:
    run_smoke(smoke, "smoke_recommend_happy")


if __name__ == "__main__":
    main()
