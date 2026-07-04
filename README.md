# operatum-mempalace-bridge

FastAPI service that wraps [MemPalace](https://pypi.org/project/mempalace/)
(`mempalace==3.3.3`, pinned in `pyproject.toml`) behind a narrow HTTP contract.
It is the **bridge** between the memory palace (MemPalace, a Python library) and
the rest of the Operatum platform: callers speak plain HTTP to this service
instead of importing MemPalace directly.

> **Scope of this document.** Everything below is verified against the source in
> *this* repository, with the file cited inline. This repo does not contain the
> platform-side callers, so their internals are deliberately not described here —
> only the HTTP contract this service exposes to them.

## What it is

- A FastAPI app (`src/main.py`) exposing **14 HTTP endpoints** across five
  routers (`src/routes/{drawers,search,wings,kg,health}.py`, wired in
  `src/main.py:95-99`).
- Two process-wide adapter singletons booted in a FastAPI `lifespan`
  (`src/main.py:61-83`):
  - **`ChromaPalaceAdapter`** (`src/adapters/chroma_palace.py`) over
    `mempalace.backends.chroma.ChromaBackend` — backs **drawers**, **search**,
    and **wings**.
  - **`KGAdapter`** (`src/adapters/kg_adapter.py`) over
    `mempalace.knowledge_graph.KnowledgeGraph` — backs the temporal
    **knowledge graph**.
- No other persistence. Two stores on one `/data` volume: the ChromaDB palace
  dir and a separate KG SQLite file (`src/adapters/kg_adapter.py:4-7`).

## Its role: sync / translation bridge

The service's job is **translation**, not new logic. It maps a small,
Node-friendly HTTP contract onto MemPalace's Python API and normalises the
shape differences between the two sides:

- **Metadata coercion** — ChromaDB metadata must be flat scalars, so
  `_coerce_metadata` drops `None`, semicolon-joins lists, and JSON-encodes
  dicts (`src/adapters/chroma_palace.py:176-199`).
- **Where-clause normalisation** — ChromaDB rejects multi-key top-level filters,
  so `_normalise_where` auto-wraps them in `$and`
  (`src/adapters/chroma_palace.py:150-173`).
- **Field renames** — the KG adapter translates the contract's `object`/`source`
  to MemPalace's `obj`/`adapter_name`, and pre-slugifies entity names
  (lower-case, spaces → `_`) so add/query round-trip cleanly
  (`src/adapters/kg_adapter.py:28-30,40-65`).
- **Error normalisation** — `KeyError → 404 {ok:false,error:"not_found"}` and
  `ValueError → 400 {ok:false,error:"bad_request"}`, so callers never see raw
  ChromaDB internals (`src/main.py:102-119`).

Every success response is `{ "ok": true, ... }` (see each route module).

## What it owns

- **Drawers** — verbatim memory rows, vector-indexed by ChromaDB. Stored in a
  single collection `mempalace_drawers` under one palace
  `PalaceRef(id="operatum-shared", namespace="operatum")`
  (`src/adapters/chroma_palace.py:41-50`).
- **Wings** — a *logical* grouping layer, **not a table**. A wing is a registry
  row kept inside the palace as a drawer with id `_wing:<slug>` and
  `kind="_wing_registry"` metadata; `GET /wings` is a metadata-only list over
  that kind (`src/routes/wings.py:1-12,45-61,80-102`).
- **Knowledge graph** — temporal entity-relationship triples
  `(subject, predicate, object, valid_from, valid_to, confidence, source)` in a
  separate SQLite file (`src/adapters/kg_adapter.py:9-21,40-65`).

## HTTP API (port 8081)

### Drawers (`src/routes/drawers.py`)

| Method / path | Body | Purpose |
|---|---|---|
| `POST /drawers` | `{ drawer_id, content, metadata }` | Insert / upsert one drawer. `content` is vector-indexed; `metadata` is coerced to flat scalars. |
| `GET /drawers/{id}` | — | Fetch one drawer. 404 if missing. |
| `PATCH /drawers/{id}` | `{ metadata?, content? }` | Partial update (read-merge-upsert). 404 if missing. |
| `DELETE /drawers/{id}` | — | Idempotent delete (missing is not an error). |

### Search (`src/routes/search.py`)

| Method / path | Body | Purpose |
|---|---|---|
| `POST /search` | `{ query?, where?, n_results }` | Semantic search. When `query` is null, falls back to a metadata-only `get` (list-by-filter). `where` is a ChromaDB-style filter; `n_results` is clamped to `[1, 100]` (`src/adapters/chroma_palace.py:97-132`). |

### Wings (`src/routes/wings.py`)

| Method / path | Body / query | Purpose |
|---|---|---|
| `POST /wings` | `{ slug, purpose?, tenant_id? }` | Create / upsert a wing registry entry. |
| `PATCH /wings/{slug}` | `{ archived?, purpose? }` | Archive or re-purpose. 404 if the wing does not exist. |
| `GET /wings` | `?tenant_id=&include_archived=` | List wings (excludes archived by default). |

### Knowledge graph (`src/routes/kg.py`)

| Method / path | Body | Purpose |
|---|---|---|
| `POST /kg/triples` | `{ subject, predicate, object, valid_from?, valid_to?, confidence?, source? }` | Add a triple. `predicate` is free-form; entities are slugified. Returns the `(subject, predicate, obj, valid_from)` tuple as `ident`. |
| `POST /kg/query` | `{ entity, as_of?, direction }` | Walk triples touching `entity`. `direction` accepts `outgoing` / `incoming` / `both` (`both` merges the two, deduped); defaults to `both` (`src/adapters/kg_adapter.py:67-87`, `src/routes/kg.py:23-27`). |
| `POST /kg/invalidate` | `{ subject, predicate, object, valid_to }` | Set `valid_to` on a triple, identified by its s/p/o tuple (MemPalace has no opaque triple ids). |
| `GET /kg/timeline/{entity}` | — | Chronological events for an entity. |
| `GET /kg/stats` | — | Entity / triple counts (from `KnowledgeGraph.stats()`). |

### Health

| Method / path | Purpose |
|---|---|
| `GET /healthz` | Liveness + configured palace/kg paths + `drawer_count` + `kg_stats` (`src/routes/health.py:19-29`). |

## Run

```sh
# Build + run standalone
docker build -t operatum-mempalace-bridge:0.1.0 .
docker run --rm -p 8081:8081 -v palace:/data operatum-mempalace-bridge:0.1.0
curl http://localhost:8081/healthz
```

The image runs `uvicorn src.main:app --workers 1` as a non-root user, exposing
port 8081 (`Dockerfile:44-52`).

## Single-replica constraint

Run **exactly one** replica per palace volume. ChromaDB is single-writer; two
processes against the same palace dir corrupt the HNSW segments. The Dockerfile
pins `--workers 1` for this reason (`Dockerfile:48-52`).

## Tests

```sh
pip install -e .[test]
pytest -q
```

Each test boots a fresh palace + KG under a tmpdir via the `client` fixture
(`tests/conftest.py:23-48`). ChromaDB downloads its bundled ONNX embedding model
on first use (cached under `HF_HOME`). Coverage spans drawers, search, wings,
KG, and health (`tests/test_*.py`).

## Environment

Defaults come from `src/main.py:65-68` and the `Dockerfile:18-23` `ENV` block.

| var | default | meaning |
|---|---|---|
| `MEMPALACE_PALACE_PATH` | `/data/palace` | ChromaDB palace dir |
| `OPERATUM_BRIDGE_KG_PATH` | `/data/knowledge_graph.sqlite3` | KG SQLite file |
| `HF_HOME` | `/data/hf-cache` | embedding-model cache |
| `TRANSFORMERS_OFFLINE` | `0` | allow first-run model download |

## Pin

`mempalace==3.3.3` (`pyproject.toml:14`). Bumping it means rebuilding the image.

## License

MIT (`pyproject.toml`).
