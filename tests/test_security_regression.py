"""P5 安全回归清单：SQL 注入参数化 / CORS 默认关闭 / 无硬编码密钥。"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

SQL_INJECTION_PAYLOADS = [
    "' OR 1=1 --",
    "'; DROP TABLE recipes; --",
    "土豆' UNION SELECT * FROM user_feedback --",
    '") OR ("1"="1',
]


def test_search_sql_injection_payloads_parameterized(client):
    """注入 payload 全部参数化：不抛 500、不回显 SQL 错误。"""
    for payload in SQL_INJECTION_PAYLOADS:
        resp = client.get("/api/recipes/search", params={"q": payload})
        assert resp.status_code in {200, 400}, resp.status_code
        text = resp.text
        assert "syntax error" not in text.lower()
        assert "mysql" not in text.lower()


def test_ingredients_search_sql_injection_payloads_parameterized(client):
    for payload in SQL_INJECTION_PAYLOADS:
        resp = client.get("/api/ingredients/search", params={"q": payload})
        assert resp.status_code in {200, 400}, resp.status_code
        assert "syntax error" not in resp.text.lower()


def test_recommend_injection_payload_no_sql_leak(client):
    resp = client.post(
        "/api/recipes/recommend",
        json={"ingredients": ["土豆' OR 1=1 --", "鸡蛋"], "exclude_tags": []},
    )
    # 无 LLM key 时走降级路径：200 且不含 SQL 错误细节
    assert resp.status_code in {200, 400, 503}
    assert "syntax error" not in resp.text.lower()


def test_cors_disabled_by_default(client):
    resp = client.get("/api/tags", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in resp.headers


def test_cors_enabled_only_with_whitelist():
    from app.config import Settings

    assert Settings().cors_origins == []


def test_no_hardcoded_secrets_in_app_or_scripts():
    patterns = ["sk-", "api_key=", "api-key=", "secret="]
    skip_dirs = {".venv", ".git", ".pytest_cache", "__pycache__", ".uv-cache"}
    targets = [PROJECT_ROOT / "app", PROJECT_ROOT / "scripts"]
    for root in targets:
        for path in root.rglob("*"):
            if path.suffix.lower() not in {".py", ".js", ".sh", ".toml", ".md"}:
                continue
            if any(part in skip_dirs for part in path.parts):
                continue
            content = path.read_text(encoding="utf-8", errors="ignore")
            for pattern in patterns:
                assert pattern not in content, f"{path} 疑似硬编码密钥: {pattern}"
