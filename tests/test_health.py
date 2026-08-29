"""/health 健康检查。"""


def test_health_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"


def test_health_live(client):
    resp = client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_health_ready_ok(client):
    resp = client.get("/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["chroma"] == "ok"


def test_health_ready_degraded(client, monkeypatch):
    import app.api.routes.health as health_mod

    def boom():
        raise RuntimeError("chroma down")

    monkeypatch.setattr(health_mod, "get_chroma_client", boom)
    resp = client.get("/health/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["chroma"] == "error"
