# operatum-mempalace-bridge

FastAPI service that wraps [MemPalace](https://pypi.org/project/mempalace/)
(`mempalace==3.3.3`) behind a narrow HTTP contract. It is the **Python side**
of Operatum's agent-memory system: Node services never speak to MemPalace
directly — they call this bridge over loopback HTTP.

Runs as a Docker sidecar in the Operatum compose stack
(`operatum-ui/docker-compose.yml`, service `mempalace-bridge`, container
`operatum-ui-mempalace-bridge-1`), bound to `127.0.0.1:8081` only (no auth —
loopback is the security boundary).

Status: **LIVE.** As of this writing the running instance holds 673 drawers,
75 KG triples, and 75 wings.

## What it owns

- **Drawers** — verbatim memory rows, vector-indexed via ChromaDB. This is the
  storage backend for `operatum-memory` when `OPERATUM_MEMORY_BACKEND=mempalace`.
  (`src/adapters/chroma_palace.py`)
- **Wings** — logical, tenant-scoped groupings of memories (typically one per
  agent thread, e.g. `thread-a9602337`). There is no wings table; a wing is a
  registry row stored as a special `_wing:<slug>` drawer with
  `kind=_wing_registry` metadata. (`src/routes/wings.py`)
- **Knowledge graph** — temporal entity-relationship triples
  (`(subject, predicate, object, valid_from, valid_to)`), stored in a separate
  SQLite file. Today the only predicate in use is `spawned_by` (agent-thread
  lineage). (`src/adapters/kg_adapter.py`)

## Why a bridge

MemPalace ships a stdio MCP server with 29 tools shaped to its own model. The
bridge instead exposes HTTP + a narrow contract that maps cleanly onto
`operatum-memory`'s `MempalaceBackend` and `KgBridge` clients, pins a single
`mempalace` release, and gives one place to absorb upstream churn.

## Single-replica constraint

ChromaDB is single-writer. Run **exactly one** replica per palace volume.
The Dockerfile uses `uvicorn --workers 1`; compose sets `restart: unless-stopped`
and a `/healthz` healthcheck. Multi-process against the same palace dir
corrupts the HNSW segments (mempalace 3.3.3's thread-pinning only defends the
single-process case).

## HTTP API (port 8081)

All responses are `{ "ok": true, ... }` on success. Errors are normalised to
a Node-friendly JSON shape: `KeyError → 404 {ok:false,error:"not_found"}`,
`ValueError → 400 {ok:false,error:"bad_request"}` (`src/main.py:102-119`).

### Drawers (`src/routes/drawers.py`)

| Method / path | Body | Purpose |
|---|---|---|
| `POST /drawers` | `{ drawer_id, content, metadata }` | Insert / upsert one drawer. `content` is vector-indexed; `metadata` must be flat scalars (lists are `;`-joined, dicts JSON-stringified). |
| `GET /drawers/{id}` | — | Fetch one drawer. 404 if missing. |
| `PATCH /drawers/{id}` | `{ metadata?, content? }` | Partial update (read-merge-upsert). |
| `DELETE /drawers/{id}` | — | Idempotent delete. |

### Search (`src/routes/search.py`)

| Method / path | Body | Purpose |
|---|---|---|
| `POST /search` | `{ query?, where?, n_results }` | Semantic search. When `query` is null, falls back to a metadata-only `get` (list-by-filter). `where` is a ChromaDB-style filter; multi-key filters are auto-wrapped in `$and`. |

### Wings (`src/routes/wings.py`)

| Method / path | Body / query | Purpose |
|---|---|---|
| `POST /wings` | `{ slug, purpose?, tenant_id? }` | Create / upsert a wing registry entry. |
| `PATCH /wings/{slug}` | `{ archived?, purpose? }` | Archive or re-purpose. |
| `GET /wings` | `?tenant_id=&include_archived=` | List wings (excludes archived by default). |

### Knowledge graph (`src/routes/kg.py`)

| Method / path | Body | Purpose |
|---|---|---|
| `POST /kg/triples` | `{ subject, predicate, object, valid_from?, valid_to?, confidence?, source? }` | Add a triple. Entities are slugified (lowercase, spaces→`_`). |
| `POST /kg/query` | `{ entity, as_of?, direction }` | Walk triples touching `entity`. `direction` ∈ `outgoing` / `incoming` / `both`. |
| `POST /kg/invalidate` | `{ subject, predicate, object, valid_to }` | Set `valid_to` on a triple (identified by its s/p/o tuple — no opaque ids). |
| `GET /kg/timeline/{entity}` | — | Chronological events for an entity. |
| `GET /kg/stats` | — | Entity / triple / current-fact counts. |

### Health

| Method / path | Purpose |
|---|---|
| `GET /healthz` | Liveness + palace/kg paths + `drawer_count` + `kg_stats`. |

## Run

```sh
# Build + run standalone
docker build -t operatum-mempalace-bridge:0.1.0 .
docker run --rm -p 8081:8081 -v palace:/data operatum-mempalace-bridge:0.1.0
curl http://localhost:8081/healthz

# In the stack: built/started by operatum-ui/docker-compose.yml (service mempalace-bridge)
```

## Tests

```sh
pip install -e .[test]
pytest -q
```

Tests boot a temporary palace + KG under a tmpdir
(`tests/conftest.py`). ChromaDB downloads its bundled ONNX embedding model on
first use (cached under `HF_HOME`).

## Env

| var | default | meaning |
|---|---|---|
| `MEMPALACE_PALACE_PATH` | `/data/palace` | ChromaDB palace dir |
| `OPERATUM_BRIDGE_KG_PATH` | `/data/knowledge_graph.sqlite3` | KG SQLite file |
| `HF_HOME` | `/data/hf-cache` | embedding-model cache |

On the gateway side the bridge is opt-in: callers no-op unless
`OPERATUM_MEMPALACE_BRIDGE_URL` is set (`operatum-ui/gateway/src/config.js:186`).

## Pin

`mempalace==3.3.3` (post-HNSW-race-fix). Bumping requires rebuilding the image.

## License

MIT
