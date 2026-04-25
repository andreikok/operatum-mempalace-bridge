"""Search surface: semantic + metadata-filter + listing fallback."""


def _seed(client):
    rows = [
        ("mem-1", "User wants dark mode in the chat UI.",
         {"wing": "user-a", "room": "layer0-critical", "tenant_id": "T1"}),
        ("mem-2", "Postgres on port 5433 for the test environment.",
         {"wing": "user-a", "room": "layer1-working", "tenant_id": "T1"}),
        ("mem-3", "Yesterday we shipped the streaming feature.",
         {"wing": "user-a", "room": "layer2-episodic", "tenant_id": "T1"}),
        ("mem-4", "Other tenant's thing.",
         {"wing": "user-b", "room": "layer0-critical", "tenant_id": "T2"}),
    ]
    for drawer_id, content, meta in rows:
        client.post("/drawers", json={
            "drawer_id": drawer_id, "content": content, "metadata": meta,
        })


def test_semantic_search_returns_relevant_hits(client):
    _seed(client)
    r = client.post("/search", json={
        "query": "appearance / theme preference",
        "where": {"tenant_id": {"$eq": "T1"}},
        "n_results": 3,
    })
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] is True
    ids = [h["drawer_id"] for h in out["hits"]]
    assert "mem-1" in ids, f"dark-mode memory should rank for 'appearance'; got {ids}"


def test_search_respects_tenant_filter(client):
    _seed(client)
    r = client.post("/search", json={
        "query": "anything",
        "where": {"tenant_id": {"$eq": "T1"}},
        "n_results": 10,
    })
    out = r.json()
    for h in out["hits"]:
        assert h["metadata"]["tenant_id"] == "T1", \
            f"tenant filter must be hard-enforced: leaked {h}"


def test_metadata_only_listing_when_query_null(client):
    _seed(client)
    r = client.post("/search", json={
        "query": None,
        "where": {"wing": {"$eq": "user-a"}, "room": {"$eq": "layer0-critical"}},
        "n_results": 5,
    })
    out = r.json()
    assert out["ok"] is True
    # Should return mem-1 (user-a + critical) and not the others.
    ids = sorted(h["drawer_id"] for h in out["hits"])
    assert ids == ["mem-1"], f"expected only mem-1; got {ids}"
