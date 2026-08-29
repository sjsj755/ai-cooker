"""食材联想与菜谱/标签 API。"""


def test_ingredient_search_like(client):
    resp = client.get("/api/ingredients/search", params={"q": "土"})
    assert resp.status_code == 200
    names = [item["name"] for item in resp.json()]
    assert "土豆" in names


def test_ingredient_search_alias(client):
    """别名应能命中词典（aliases JSON 列检索）。"""
    resp = client.get("/api/ingredients/search", params={"q": "马铃薯"})
    assert resp.status_code == 200
    names = [item["name"] for item in resp.json()]
    assert "土豆" in names


def test_ingredient_search_empty_400(client):
    resp = client.get("/api/ingredients/search", params={"q": "   "})
    assert resp.status_code == 400


def test_recipe_not_found(client):
    resp = client.get("/api/recipes/999999")
    assert resp.status_code == 404


def test_tags_list(client):
    resp = client.get("/api/tags")
    assert resp.status_code == 200
    kinds = {item["kind"] for item in resp.json()}
    assert {"过敏原", "忌口", "口味"}.issubset(kinds)
