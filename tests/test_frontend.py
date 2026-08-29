"""P4 前端验收：静态路由 / 资源完整性 / 静态安全扫描 / 调用契约。"""

from pathlib import Path

FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"


# ---------- 静态路由 ----------


def test_home_serves_index(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "AI 厨师" in resp.text
    assert 'src="js/recommend.js"' in resp.text


def test_search_page_served(client):
    resp = client.get("/search.html")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert 'src="js/search.js"' in resp.text


def test_unknown_static_path_returns_404(client):
    resp = client.get("/does-not-exist.html")
    assert resp.status_code == 404


def test_docs_and_openapi_not_shadowed(client):
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_api_routes_not_shadowed_by_static_mount(client):
    """静态挂载之后 /api/* 仍正常路由（轻量端点实测 + 422 证明未吞路由）。"""
    assert client.get("/api/tags").status_code == 200
    resp = client.get("/api/ingredients/search", params={"q": "土"})
    assert resp.status_code == 200
    assert resp.json()
    resp = client.get("/api/recipes/search", params={"q": "土豆"})
    assert resp.status_code == 200
    assert "recipes" in resp.json()
    resp = client.get("/api/recipes/999999")
    assert resp.status_code == 404
    assert "detail" in resp.json()
    # recommend 用 422（缺必填字段）证明路由未被静态目录吞掉，同时避免触发 LLM
    resp = client.post("/api/recipes/recommend", json={})
    assert resp.status_code == 422


# ---------- 资源完整性 ----------


def _parse_assets(html: str) -> list[str]:
    """提取 HTML 中的 <link href> 与 <script src>。"""
    import re

    links = re.findall(r'<link[^>]+href="([^"]+)"', html)
    scripts = re.findall(r'<script[^>]+src="([^"]+)"', html)
    return links + scripts


def test_all_page_assets_exist_and_nonempty(client):
    for page in ("/", "/search.html"):
        resp = client.get(page)
        assert resp.status_code == 200
        assets = _parse_assets(resp.text)
        assert assets, f"{page} 未引用任何 css/js 资源"
        for asset in assets:
            rel = asset.lstrip("/")
            path = FRONTEND_DIR / rel
            assert path.is_file(), f"{page} 引用的资源不存在: {asset}"
            assert path.stat().st_size > 0, f"{page} 引用的资源为空: {asset}"


def test_favicon_exists_and_referenced():
    """favicon 资源完整性：文件存在且非空，两页 HTML 均显式引用。"""
    favicon = FRONTEND_DIR / "favicon.svg"
    assert favicon.is_file(), "frontend/favicon.svg 缺失"
    assert favicon.stat().st_size > 0, "frontend/favicon.svg 为空"
    for page_file in ("index.html", "search.html"):
        html = (FRONTEND_DIR / page_file).read_text(encoding="utf-8")
        assert 'href="favicon.svg"' in html, f"{page_file} 未引用 favicon.svg"
        assert 'type="image/svg+xml"' in html, f"{page_file} favicon 缺 type 属性"


def test_index_has_drawer_root():
    """推荐主页抽屉容器（P4.2 查看详情）。"""
    html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    assert 'id="detail-drawer-root"' in html


def test_label_for_targets_exist():
    """每个 <label for> 都必须指向真实元素 id（静态或动态 chip 输入框）。"""
    import re

    # 动态生成的 chip 输入框 id：由页面脚本传给 ui.renderChipInput
    dynamic_ids = {
        "index.html": {"ingredient-input"},
        "search.html": {"search-ingredient-input"},
    }
    for page_file in ("index.html", "search.html"):
        html = (FRONTEND_DIR / page_file).read_text(encoding="utf-8")
        static_ids = set(re.findall(r'id="([^"]+)"', html))
        for label_for in re.findall(r'<label[^>]+for="([^"]+)"', html):
            assert (
                label_for in static_ids or label_for in dynamic_ids[page_file]
            ), f"{page_file}: label for='{label_for}' 无对应元素 id"

    # 动态 id 必须由页面脚本传给渲染组件，且 ui.js 支持 id 选项
    assert 'id: "ingredient-input"' in _read_js("recommend.js")
    assert 'id: "search-ingredient-input"' in _read_js("search.js")
    assert "opts.id" in _read_js("ui.js")


# ---------- P4.1 静态契约 ----------


def test_collapse_aria_and_focus_contract():
    """P4.1 折叠契约：ui.js 处理 hidden / aria-expanded / data-toggle-id；
    recommend.js 传入 expandedId 且含 preventScroll 焦点恢复（锁定无状态 + aria 同步 + 焦点保持）。"""
    ui_js = _read_js("ui.js")
    recommend_js = _read_js("recommend.js")

    # ui.js：展开容器常驻 hidden + aria 同步 + 稳定 data-toggle-id + 折叠回调入参
    assert "hidden" in ui_js
    assert "aria-expanded" in ui_js
    assert "aria-controls" in ui_js
    assert "data-toggle-id" in ui_js
    assert "onToggleSteps" in ui_js

    # recommend.js：展开状态由页面脚本持有（expandedCardId），渲染传入 expandedId，
    # 全量重建后按 data-toggle-id 恢复焦点（preventScroll）
    assert "expandedCardId" in recommend_js
    assert "expandedId" in recommend_js
    assert "preventScroll" in recommend_js


def test_drawer_manager_contract():
    """P4.2 抽屉管理器契约：状态机在 createDetailDrawerManager.js，两页均实例化。"""
    manager_js = _read_js("createDetailDrawerManager.js")
    ui_js = _read_js("ui.js")
    recommend_js = _read_js("recommend.js")
    search_js = _read_js("search.js")

    assert "detailCache" in manager_js
    assert "openedDetailId" in manager_js
    assert "detailTrigger" in manager_js
    assert "isConnected" in manager_js
    assert "drawer-open" in manager_js
    assert "/api/recipes/${recipeId}" in manager_js
    assert "createDetailDrawerManager(" in recommend_js
    assert "createDetailDrawerManager(" in search_js
    # ui.js 仍负责抽屉外壳（滚动锁定 + 聚焦关闭按钮）
    assert "drawer-open" in ui_js
    assert "closeBtn.focus" in ui_js


def test_loading_skeleton_and_seasonings_contract():
    """P4.2 感知性能与用料契约：ui.js 加载骨架 / 食材调料区块；recommend.js 增量折叠。"""
    ui_js = _read_js("ui.js")
    recommend_js = _read_js("recommend.js")

    assert "renderDrawerLoading" in ui_js
    assert "spinner" in ui_js
    assert "drawer-ingredients" in ui_js
    assert "drawer-seasonings" in ui_js
    assert "seasonings" in ui_js
    assert "lastRenderedResults" in recommend_js
    assert "preventScroll" in recommend_js


# ---------- 静态安全扫描 ----------


DANGEROUS_DOM_PATTERNS = ["innerHTML", "insertAdjacentHTML", "document.write", "eval("]
SECRET_PATTERNS = ["sk-", "api_key=", "api-key=", "secret="]


def test_frontend_has_no_dangerous_dom_api():
    files = [
        p
        for p in FRONTEND_DIR.rglob("*")
        if p.suffix.lower() in {".html", ".js", ".css"}
    ]
    assert files
    for path in files:
        content = path.read_text(encoding="utf-8")
        for pattern in DANGEROUS_DOM_PATTERNS:
            assert pattern not in content, f"{path.name} 含危险 DOM API: {pattern}"


def test_frontend_has_no_hardcoded_secrets():
    files = [
        p
        for p in FRONTEND_DIR.rglob("*")
        if p.suffix.lower() in {".html", ".js", ".css"}
    ]
    for path in files:
        content = path.read_text(encoding="utf-8")
        for pattern in SECRET_PATTERNS:
            assert pattern not in content, f"{path.name} 疑似硬编码密钥: {pattern}"


# ---------- 前端调用契约 ----------


def _read_js(name: str) -> str:
    return (FRONTEND_DIR / "js" / name).read_text(encoding="utf-8")


def test_frontend_api_paths_match_backend_contract():
    recommend_js = _read_js("recommend.js")
    search_js = _read_js("search.js")
    api_js = _read_js("api.js")

    # 推荐主页：推荐 / 联想 / 标签
    assert "/api/recipes/recommend" in recommend_js
    assert "/api/ingredients/search" in recommend_js
    assert "/api/tags" in recommend_js

    # 搜索页：检索 / 联想 / 标签（详情路径在 createDetailDrawerManager.js 断言）
    assert "/api/recipes/search" in search_js
    assert "/api/ingredients/search" in search_js
    assert "/api/tags" in search_js

    # 请求层必须实现任务级 registry 与超时常量
    assert "createTaskRegistry" in api_js
    assert "AbortController" in api_js
    assert "REQUEST_TIMEOUT_MS" in api_js
    # 抽屉管理器封装详情请求路径
    assert "/api/recipes/${recipeId}" in _read_js("createDetailDrawerManager.js")

    # 与后端路由表比对：契约路径全部在 OpenAPI 中注册
    from app.main import app

    paths = app.openapi()["paths"]
    assert "/api/recipes/recommend" in paths
    assert "/api/recipes/search" in paths
    assert "/api/recipes/{recipe_id}" in paths
    assert "/api/ingredients/search" in paths
    assert "/api/tags" in paths


# ---------- P5 反馈闭环契约 ----------


def test_feedback_buttons_contract():
    """P5：反馈按钮存在、aria-pressed 同步、请求走 POST /api/feedback。"""
    ui_js = _read_js("ui.js")
    recommend_js = _read_js("recommend.js")
    search_js = _read_js("search.js")

    # ui.js：按钮渲染 + 状态同步 + 回调入参
    assert "feedback-btn" in ui_js
    assert "aria-pressed" in ui_js
    assert "onFeedback" in ui_js
    assert "feedbackState" in ui_js
    assert "收藏" in ui_js
    assert "不喜欢" in ui_js

    # 两页脚本：feedback 任务类型 + 状态持有 + 请求路径
    for page_js in (recommend_js, search_js):
        assert 'run("feedback"' in page_js
        assert "/api/feedback" in page_js
        assert "feedbackByRecipe" in page_js
        assert "lastFeedbackRetry" in page_js

    # 与后端路由表比对
    from app.main import app

    assert "/api/feedback" in app.openapi()["paths"]


def test_feedback_buttons_no_dangerous_dom_api():
    """反馈实现沿用 createElement + textContent，无危险 DOM API（含新代码）。"""
    ui_js = _read_js("ui.js")
    recommend_js = _read_js("recommend.js")
    search_js = _read_js("search.js")
    for content in (ui_js, recommend_js, search_js):
        for pattern in DANGEROUS_DOM_PATTERNS:
            assert pattern not in content
