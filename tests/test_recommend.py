"""recommend 占位端点：P0 预期 501。"""


def test_recommend_returns_501(client):
    resp = client.post(
        "/api/recipes/recommend",
        json={"ingredients": ["土豆", "鸡蛋"], "exclude_tags": []},
    )
    assert resp.status_code == 501
    body = resp.json()
    assert body["degraded"] is True
    assert body["notice"]
