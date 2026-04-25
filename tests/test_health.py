"""Health endpoint — what the docker compose health-check hits."""


def test_healthz_responds_with_palace_paths(client):
    r = client.get("/healthz")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] is True
    assert "palace_path" in out
    assert "kg_path" in out
    # Counts may be 0 on a fresh palace; key presence is what matters.
    assert "drawer_count" in out
    assert "kg_stats" in out
