# operatum-mempalace-bridge

FastAPI service that wraps [MemPalace](https://github.com/MemPalace/mempalace)
for the Node-side `operatum-memory` package. Runs as a docker
sidecar inside the Operatum compose stack; the gateway talks to it
over loopback HTTP.

The bridge is the source of truth for:

- **Drawers** — verbatim memory rows, vector-indexed via ChromaDB.
- **Wings** — logical groupings (per-user, per-thread). Stored as a
  metadata field on drawers + a small registry kept inside the
  palace itself.
- **Knowledge graph** — temporal entity-relationship triples
  (`spawned_by`, `subscribed_to`, `became_unhealthy`, etc.). Stored
  in a separate SQLite file under `/data/knowledge_graph.sqlite3`.

## Why a bridge

`mempalace` ships its own MCP server (`mempalace-mcp`), but it's
stdio-only and exposes 29 tools shaped to its own model. We want
HTTP, our own narrower contract that maps cleanly to
`operatum-memory`'s `MemoryStore` adapter, single-replica health
checks, and a thin layer where we can pin a `mempalace==X.Y.Z`
release and absorb upstream churn cleanly.

## Single-replica constraint

ChromaDB is single-writer. Run **one** replica of this bridge per
palace volume. The compose entry sets `replicas: 1` and
`restart: unless-stopped`. Multi-process is unsupported by
mempalace's HNSW pinning logic; multi-replica corrupts the
segment files.

## Surface (11 endpoints)

```
POST   /drawers                         create / upsert
GET    /drawers/{id}                    fetch
PATCH  /drawers/{id}                    move room or update metadata
DELETE /drawers/{id}
POST   /search                          query / metadata-only listing
POST   /wings                           create or upsert
PATCH  /wings/{slug}                    archive / re-purpose
GET    /wings                           list (tenant-scoped, exclude archived by default)
POST   /kg/triples                      add (subject, predicate, object, validity)
POST   /kg/query                        walk triples touching an entity
POST   /kg/invalidate                   set valid_to on a triple
GET    /kg/timeline/{entity}            chronological events
GET    /kg/stats
GET    /healthz                         liveness + drawer + kg stats
```

## Local dev

```sh
docker build -t operatum-mempalace-bridge:0.1.0 .
docker run --rm -p 8081:8081 -v palace:/data operatum-mempalace-bridge:0.1.0
curl http://localhost:8081/healthz
```

## Tests

```sh
pip install -e .[test]
pytest -q
```

Tests boot a temporary palace + KG per run under a tmpdir. ChromaDB
downloads its bundled ONNX embedding model on first use (~90 MB);
subsequent runs use the cache under `~/.cache/chroma`.

## Env

| var | default | what |
|---|---|---|
| `MEMPALACE_PALACE_PATH` | `/data/palace` | ChromaDB palace dir |
| `OPERATUM_BRIDGE_KG_PATH` | `/data/knowledge_graph.sqlite3` | KG sqlite file |
| `HF_HOME` | `/data/hf-cache` | embedding model cache |

## Pin

`mempalace==3.3.3` — post-HNSW-race-fix release. Bumping requires
rebuilding the docker image.

## License

MIT
