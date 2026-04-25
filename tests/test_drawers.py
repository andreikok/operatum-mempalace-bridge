"""End-to-end drawer CRUD against a real (per-test) palace."""


def test_upsert_get_roundtrip(client):
    body = {
        "drawer_id": "mem-test-001",
        "content": "User prefers dark mode and concise replies.",
        "metadata": {
            "wing": "user-abc12345",
            "room": "layer0-critical",
            "tenant_id": "tenant-1",
            "user_id": "user-1",
            "tags_csv": "preference;ui",
            "salience": 0.9,
        },
    }
    r = client.post("/drawers", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    g = client.get("/drawers/mem-test-001")
    assert g.status_code == 200, g.text
    out = g.json()
    assert out["drawer_id"] == "mem-test-001"
    assert "dark mode" in out["content"]
    assert out["metadata"]["wing"] == "user-abc12345"
    assert out["metadata"]["salience"] == 0.9


def test_get_unknown_drawer_returns_404(client):
    r = client.get("/drawers/no-such-id")
    assert r.status_code == 404, r.text
    assert r.json()["error"] == "not_found"


def test_patch_drawer_merges_metadata(client):
    client.post("/drawers", json={
        "drawer_id": "mem-patch-001",
        "content": "original",
        "metadata": {"wing": "user-x", "salience": 0.5},
    })
    r = client.patch("/drawers/mem-patch-001",
                     json={"metadata": {"salience": 0.8}})
    assert r.status_code == 200, r.text
    out = client.get("/drawers/mem-patch-001").json()
    # Old wing preserved, salience overwritten — merge semantics.
    assert out["metadata"]["wing"] == "user-x"
    assert out["metadata"]["salience"] == 0.8
    assert out["content"] == "original"


def test_delete_drawer_idempotent(client):
    client.post("/drawers", json={
        "drawer_id": "mem-del-001",
        "content": "to be deleted",
        "metadata": {},
    })
    r1 = client.delete("/drawers/mem-del-001")
    assert r1.status_code == 200
    # Second delete on the same id must NOT 404 — the caller already
    # considers it gone, this matches the operatum-memory contract.
    r2 = client.delete("/drawers/mem-del-001")
    assert r2.status_code == 200


def test_metadata_arrays_get_flattened_to_csv(client):
    """ChromaDB metadata is scalar-only. The adapter must coerce arrays."""
    body = {
        "drawer_id": "mem-arr-001",
        "content": "tagged thing",
        "metadata": {
            "wing": "user-y",
            "tags": ["one", "two", "three"],
        },
    }
    r = client.post("/drawers", json=body)
    assert r.status_code == 200, r.text
    out = client.get("/drawers/mem-arr-001").json()
    # Lists came in as ['one', 'two', 'three']; the adapter
    # semicolon-joins them so chroma will accept the value.
    assert out["metadata"]["tags"] == "one;two;three"
