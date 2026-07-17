"""Search surface: semantic + metadata-filter + listing fallback."""

from pathlib import Path


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


def test_metadata_listing_returns_more_than_100_rows(client):
    """Metadata-only search must NOT cap at 100.

    The retention sweeper and quota enforcer pass n_results=1_000_000 to
    request the full scope for ranking. If the bridge caps at 100, scopes
    larger than 100 can never be correctly swept or quota-enforced.
    """
    for i in range(110):
        client.post("/drawers", json={
            "drawer_id": f"bulk-{i}",
            "content": f"memory {i}",
            "metadata": {
                "room": "layer2-episodic",
                "tenant_id": "T-bulk",
                "user_id": "u-bulk",
            },
        })
    r = client.post("/search", json={
        "query": None,
        "where": {"tenant_id": {"$eq": "T-bulk"}},
        "n_results": 200,
    })
    out = r.json()
    assert out["ok"] is True, r.text
    assert len(out["hits"]) == 110, (
        f"metadata-only search must return all 110 rows (no 100-row cap); "
        f"got {len(out['hits'])}"
    )


def test_100_cap_appears_only_before_the_first_collection_get_call():
    """Source-contract pin, mirroring the gateway's co-located check
    (operatum-ui/gateway/test/memory-retention.test.js, ~L428): the
    `min(n_results, 100)` cap must appear BEFORE the first
    `collection.get(` call in the file. That gateway test scans the raw
    source with no method scoping, so an unrelated earlier `.get()` call
    (e.g. from a CRUD helper) sorted ahead of the search method would
    make the cap look like it also covers the null-query recall path.
    """
    src_path = Path(__file__).resolve().parents[1] / "src" / "adapters" / "chroma_palace.py"
    lines = src_path.read_text().splitlines()
    cap_idx = next(i for i, l in enumerate(lines) if "min(n_results, 100)" in l)
    get_idx = next(i for i, l in enumerate(lines) if "collection.get(" in l)
    assert cap_idx < get_idx, (
        f"min(n_results, 100) (line {cap_idx + 1}) must appear before the first "
        f"collection.get( call (line {get_idx + 1}) in the file, or the gateway's "
        "source-contract test misreads the cap as covering the null-query path"
    )
