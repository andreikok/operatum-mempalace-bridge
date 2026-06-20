# operatum-mempalace-bridge — architecture

> This document is grounded in the code as of the read. Earlier versions of
> this file described a different (aspirational) design — OpenAI embeddings,
> Postgres-backed KG, `/reflect`, port 3950. **None of that is in the code.**
> The descriptions below reflect what actually ships.

A FastAPI service that wraps [MemPalace](https://pypi.org/project/mempalace/)
(`mempalace==3.3.3`, `pyproject.toml:14`) behind a narrow HTTP API. It is the
**only** thing in Operatum that talks to MemPalace; every Node consumer goes
through it over loopback HTTP. It runs as a single-replica Docker sidecar.

## Two adapters, two stores

`src/main.py` boots two process-wide singletons in a FastAPI `lifespan`
(`src/main.py:61-83`) and exposes them to routes via `get_palace()` /
`get_kg()` dependencies:

1. **`ChromaPalaceAdapter`** (`src/adapters/chroma_palace.py`) — wraps
   `mempalace.backends.chroma.ChromaBackend` (`chroma_palace.py:26`). One
   palace (`PalaceRef(id="operatum-shared", namespace="operatum")`,
   `chroma_palace.py:41-45`), one collection `mempalace_drawers`
   (`chroma_palace.py:46-50`). Backs **drawers**, **search**, and **wings**.
   - Tenant isolation is NOT physical: it's a single shared collection, and
     every Node `where` clause carries `tenant_id` (`chroma_palace.py:36-40`).
   - ChromaDB metadata must be flat scalars; `_coerce_metadata`
     (`chroma_palace.py:176-199`) joins lists with `;` and JSON-encodes dicts.
   - ChromaDB rejects multi-key top-level `where`; `_normalise_where`
     (`chroma_palace.py:150-173`) auto-wraps them in `$and`.
   - Persisted under `MEMPALACE_PALACE_PATH` (`/data/palace`).

2. **`KGAdapter`** (`src/adapters/kg_adapter.py`) — wraps
   `mempalace.knowledge_graph.KnowledgeGraph` (`kg_adapter.py:25,36`). Backs
   the **temporal KG**. Persisted in a **separate SQLite file**
   `OPERATUM_BRIDGE_KG_PATH` (`/data/knowledge_graph.sqlite3`) — by mempalace's
   own design, KG concerns (temporal/relational) are distinct from palace
   concerns (verbatim/vector). Both live on the same `/data` volume so backups
   are atomic.

The bridge holds **no other persistence** — no Postgres, no separate embedding
service. Embeddings are ChromaDB's bundled ONNX model (cached under `HF_HOME`,
`Dockerfile:18-23`).

## Wings: a logical layer, not a table

There is no wings store. A wing is a registry row kept inside the palace as a
drawer with id `_wing:<slug>` and `kind=_wing_registry` metadata
(`src/routes/wings.py:1-12,45-61`). `GET /wings` is a metadata-only ChromaDB
`get` filtered on that kind (`wings.py:80-102`). Live data: wing slugs are
`thread-<8hex>` with `purpose` like `"Supervisor for issue …"`, one per
spawned agent thread.

## HTTP surface (`src/main.py:95-99`)

13 endpoints across five routers — see README for the full table. Summary:
`drawers` (CRUD), `search` (POST), `wings` (POST/PATCH/GET), `kg`
(triples/query/invalidate/timeline/stats), `health` (`/healthz`).

Error contract (`src/main.py:102-119`): `KeyError → 404` not_found,
`ValueError → 400` bad_request, both as `{ok:false, error, detail}` so the
Node adapters can branch on shape.

## Relationship to operatum-memory (the Node side)

`operatum-memory` is the Node memory library. When configured with
`OPERATUM_MEMORY_BACKEND=mempalace`, **this bridge is its storage backend**.
Two distinct Node clients hit the bridge:

- **`operatum-memory/src/backends/mempalace.js`** — `MempalaceBackend`
  implements the `Memory` contract (`add/search/promote/demote/expire/stats`)
  by calling `POST /drawers`, `POST /search`, `GET/PATCH/DELETE /drawers/{id}`
  (`mempalace.js:184,204,235,244,267,291,364`). This is the **drawer storage
  path**. There is also a Postgres-independent SQLite backend
  (`backends/sqlite.js`) for deployments that don't run the bridge;
  `backends/index.js:resolveBackendName` picks between them.
- **`operatum-memory/src/kg-bridge.js`** — `KgBridge`, a thin client for the
  `/kg/*` endpoints (`kg-bridge.js:69,82,100`). Deliberately separate from
  `MempalaceBackend` because a triple write may happen without touching a
  drawer (`kg-bridge.js:5-13`). Normalises mempalace's `obj` field back to
  `object` on read (`kg-bridge.js:28-30`).

When the backend is `sqlite` (no bridge), both the drawer and KG paths
degrade to no-ops in the library.

## Relationship to the gateway (operatum-ui)

The gateway has its OWN client,
`operatum-ui/gateway/src/lib/mempalace-bridge-client.js`, separate from
`operatum-memory`'s clients. It uses the bridge for **domain-event lineage**,
not memory storage. All calls are **best-effort, fire-and-forget**, gated on
`isBridgeConfigured()` (URL present), with a 5s timeout. If
`OPERATUM_MEMPALACE_BRIDGE_URL` is unset, every call no-ops silently — and
"no KG" is the pre-existing baseline, so unavailability is no regression
(`mempalace-bridge-client.js` header comment).

Call sites:

- **Agent spawn** (`gateway/src/lib/agent-spawn.js:367-418`): on every child
  thread spawn, `createWing({ slug: thread-<8hex>, purpose: spawn_reason,
  tenantId })` + `addTriple({ subject:"thread <id>", predicate:"spawned_by",
  object:"thread <parent>"|"user <id>", source:"agent_spawn" })`.
- **Cascade kill** (`gateway/src/routes/agents.js:297-349`): on thread kill,
  for each descendant `archiveWing(slug)` + `queryTriples(direction:outgoing)`
  then `invalidateTriple(...)` for each live `spawned_by` triple (sets
  `valid_to`).
- **MCP tools** (`gateway/src/routes/operatum-mcp.js:585-620`):
  `memory_kg_query` / `memory_kg_timeline` proxy `queryTriples` / `timeline`;
  they return a "not configured" note when the bridge URL is unset.
- **Admin / health** (`gateway/src/routes/admin.js:1799`,
  `admin-system-health.js:169`, `health.js:73`): call `/healthz` for status.

Ports: gateway resolves `mempalaceBridge` to `8081` (primary) / `18081`
(test) in `gateway/src/lib/paths.js:220-232`; compose binds
`127.0.0.1:${MEMPALACE_BRIDGE_PORT:-8081}:8081`
(`operatum-ui/docker-compose.yml:183`).

## EXPOSES / CONSUMES

**EXPOSES** (HTTP `:8081`, loopback only, no auth):
drawers CRUD · `/search` · wings CRUD/list · `/kg/{triples,query,invalidate,timeline,stats}` · `/healthz`.

**CONSUMES**:
- `mempalace==3.3.3` (Python lib) — `ChromaBackend` + `KnowledgeGraph`.
- ChromaDB (vendored by mempalace) + its bundled ONNX embedding model.
- The `/data` Docker volume (palace dir + KG sqlite + HF cache).
- No network egress beyond first-run model download (`TRANSFORMERS_OFFLINE=0`).

## Live vs aspirational

- **LIVE**: the container `operatum-ui-mempalace-bridge-1` is healthy and in
  active use — `/healthz` reports 673 drawers, 76 entities, 75 `spawned_by`
  triples, 75 wings (`thread-*`). Both the storage path and the spawn-lineage
  path are writing.
- **Narrow in practice**: the KG has exactly one predicate today
  (`spawned_by`). `confidence`, `as_of`, `timeline`, and `incoming`/`both`
  directions are implemented but lightly exercised. The header comments
  mention other predicates (`subscribed_to`, `became_unhealthy`) as examples
  — those are illustrative, not currently written by any call site.

## Where to start reading

- `src/main.py` — app entry, lifespan, adapter singletons, error handlers.
- `src/adapters/chroma_palace.py` — drawer/search/wing storage.
- `src/adapters/kg_adapter.py` — temporal KG.
- `src/routes/{drawers,search,wings,kg,health}.py` — the HTTP surface.
- Node consumers: `operatum-memory/src/backends/mempalace.js`,
  `operatum-memory/src/kg-bridge.js`,
  `operatum-ui/gateway/src/lib/mempalace-bridge-client.js`.
