"""Wings registry: create + list + archive."""


def test_create_and_list_wings(client):
    client.post("/wings", json={
        "slug": "user-abc12345",
        "purpose": "drako@operatum.com personal wing",
        "tenant_id": "T1",
    })
    client.post("/wings", json={
        "slug": "thread-deadbeef",
        "purpose": "fractal-renderer build thread",
        "tenant_id": "T1",
    })
    r = client.get("/wings", params={"tenant_id": "T1"})
    assert r.status_code == 200, r.text
    out = r.json()
    slugs = sorted(w["slug"] for w in out["wings"])
    assert "user-abc12345" in slugs
    assert "thread-deadbeef" in slugs


def test_archive_wing_excludes_from_default_list(client):
    client.post("/wings", json={
        "slug": "thread-x", "tenant_id": "T1",
    })
    p = client.patch("/wings/thread-x", json={"archived": True})
    assert p.status_code == 200, p.text

    # Default list excludes archived.
    r = client.get("/wings", params={"tenant_id": "T1"})
    assert "thread-x" not in [w["slug"] for w in r.json()["wings"]]

    # Explicit include_archived=true brings it back.
    r2 = client.get("/wings", params={
        "tenant_id": "T1", "include_archived": "true",
    })
    assert "thread-x" in [w["slug"] for w in r2.json()["wings"]]


def test_patch_unknown_wing_returns_404(client):
    r = client.patch("/wings/nope", json={"archived": True})
    assert r.status_code == 404, r.text


def test_tenant_filter_isolates_wings(client):
    client.post("/wings", json={"slug": "user-tenantA", "tenant_id": "TA"})
    client.post("/wings", json={"slug": "user-tenantB", "tenant_id": "TB"})
    a = client.get("/wings", params={"tenant_id": "TA"}).json()
    b = client.get("/wings", params={"tenant_id": "TB"}).json()
    a_slugs = {w["slug"] for w in a["wings"]}
    b_slugs = {w["slug"] for w in b["wings"]}
    assert "user-tenantA" in a_slugs and "user-tenantB" not in a_slugs
    assert "user-tenantB" in b_slugs and "user-tenantA" not in b_slugs
