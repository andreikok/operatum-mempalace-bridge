"""End-to-end drawer CRUD against a real (per-test) palace."""

from concurrent.futures import ThreadPoolExecutor


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


def test_create_if_absent_is_idempotent_after_metadata_normalisation(client):
    body = {
        "drawer_id": "plan-distillation-001",
        "content": "Reviewed conclusion",
        "metadata": {
            "source_type": "operator-plan-distillation-v1",
            "source_id": "plan-1:generation-1",
            "tags": ["strategic", "distillation_conclusion"],
            "expires_at": None,
        },
    }

    created = client.post("/drawers/create-if-absent", json=body)
    assert created.status_code == 200, created.text
    assert created.json() == {
        "ok": True,
        "created": True,
        "drawer": {
            "drawer_id": "plan-distillation-001",
            "content": "Reviewed conclusion",
            "metadata": {
                "source_type": "operator-plan-distillation-v1",
                "source_id": "plan-1:generation-1",
                "tags": "strategic;distillation_conclusion",
            },
        },
    }

    repeated = client.post("/drawers/create-if-absent", json=body)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json() == {**created.json(), "created": False}


def test_create_if_absent_conflicts_without_overwriting(client):
    original = {
        "drawer_id": "plan-distillation-conflict",
        "content": "Original reviewed conclusion",
        "metadata": {"source_id": "plan-1:generation-1"},
    }
    assert client.post("/drawers/create-if-absent", json=original).status_code == 200

    for changed in (
        {**original, "content": "Different conclusion"},
        {**original, "metadata": {"source_id": "plan-1:generation-2"}},
    ):
        conflict = client.post("/drawers/create-if-absent", json=changed)
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["error"] == "conflict"

    stored = client.get("/drawers/plan-distillation-conflict").json()
    assert stored["content"] == original["content"]
    assert stored["metadata"] == original["metadata"]


def test_concurrent_create_if_absent_has_one_winner(client):
    def create(content):
        return client.post("/drawers/create-if-absent", json={
            "drawer_id": "plan-distillation-race",
            "content": content,
            "metadata": {"source_id": "plan-1:generation-1"},
        })

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(create, ["first candidate", "second candidate"]))

    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = next(response.json()["drawer"] for response in responses
                  if response.status_code == 200)
    assert client.get("/drawers/plan-distillation-race").json()["content"] == winner["content"]


def test_concurrent_identical_create_if_absent_creates_once(client):
    body = {
        "drawer_id": "plan-distillation-identical-race",
        "content": "One reviewed conclusion",
        "metadata": {
            "source_id": "plan-1:generation-1",
            "tags": ["strategic", "distillation_conclusion"],
        },
    }

    def create():
        return client.post("/drawers/create-if-absent", json=body)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _request: create(), range(2)))

    assert [response.status_code for response in responses] == [200, 200]
    payloads = [response.json() for response in responses]
    assert sorted(payload["created"] for payload in payloads) == [False, True]

    expected_drawer = {
        "drawer_id": body["drawer_id"],
        "content": body["content"],
        "metadata": {
            "source_id": "plan-1:generation-1",
            "tags": "strategic;distillation_conclusion",
        },
    }
    assert all(payload["drawer"] == expected_drawer for payload in payloads)
    assert client.get(f"/drawers/{body['drawer_id']}").json() == {
        "ok": True,
        **expected_drawer,
    }
    assert client.get("/healthz").json()["drawer_count"] == 1
