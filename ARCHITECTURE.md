# operatum-mempalace-bridge — architecture

> Grounded in the source of *this* repository as of the read; each load-bearing
> claim cites the file it came from. Earlier versions of this file described an
> aspirational design (OpenAI embeddings, Postgres-backed KG, a `/reflect`
> endpoint, port 3950) — **none of that is in the code** and it has been removed.
> This document describes only what this repo does; the platform-side callers
> live in other repositories and their internals are intentionally out of scope.

A FastAPI service (`src/main.py`) that wraps
[MemPalace](https://pypi.org/project/mempalace/) (`mempalace==3.3.3`,
`pyproject.toml:14`) behind a narrow HTTP API. It is the **bridge** between the
memory palace and the platform: it translates a small HTTP contract into
MemPalace's Python API and back. It is designed to run as a single-replica
container (`Dockerfile`).

## App shape

`src/main.py` constructs the FastAPI app and mounts five routers
(`src/main.py:95-99`):

- `health` → `/healthz`
- `drawers` (prefix `/drawers`) → CRUD
- `search` → `/search`
- `wings` (prefix `/wings`) → create / patch / list
- `kg` (prefix `/kg`) → triples / query / invalidate / timeline / stats

That is **15 endpoints total** (5 drawers + 1 search + 3 wings + 5 kg +
1 health). The `Surface (11 endpoints …)` note in the `src/main.py` module
docstring is a stale undercount — the router wiring above is authoritative.

Two process-wide adapter singletons are booted in a FastAPI `lifespan`
(`src/main.py:44-83`) and handed to routes through the `get_palace()` /
`get_kg()` dependencies; the routes late-import these to avoid a circular import
at module load (e.g. `src/routes/drawers.py:21-24`).

## Two adapters, two stores

### 1. `ChromaPalaceAdapter` (`src/adapters/chroma_palace.py`)

Wraps `mempalace.backends.chroma.ChromaBackend`
(`src/adapters/chroma_palace.py:26,35`). Backs **drawers**, **search**, and
**wings**.

- One palace `PalaceRef(id="operatum-shared", namespace="operatum",
  local_path=<palace_path>)`, one collection `mempalace_drawers`
  (`src/adapters/chroma_palace.py:41-50`).
- **Tenant isolation is not physical.** It is a single shared collection; the
  caller carries `tenant_id` in every `where` clause. Running a separate palace
  per tenant is deliberately avoided (`src/adapters/chroma_palace.py:36-45`).
- **`_coerce_metadata`** (`:176-199`) — ChromaDB metadata must be flat scalars.
  `None` is dropped, lists/tuples are stringified and `;`-joined (empty list →
  key dropped), dicts are JSON-encoded, other types are `str()`-ed.
- **`_normalise_where`** (`:150-173`) — ChromaDB rejects multi-key top-level
  filters. Single-key filters pass through; multi-field filters are wrapped in a
  `$and` (any top-level operator keys join as siblings).
- **`search`** (`src/adapters/chroma_palace.py`) — with a non-empty `query`,
  runs a vector query clamped to `[1, 100]`; with `query=None`, falls back to a
  metadata-only `get` that honors `n_results` with a minimum of 1 and has no
  100-row cap. The uncapped metadata path is required by callers that enumerate
  a complete filtered scope.
- **Atomic conditional create** — `POST /drawers/create-if-absent` holds one
  adapter lock across the Chroma existence check and insert. A replay succeeds
  only when content and `_coerce_metadata` output exactly match the stored row;
  otherwise it returns 409 without writing. `POST /drawers` retains its
  unconditional upsert semantics.
- Persisted under `MEMPALACE_PALACE_PATH` (`src/main.py:65-66`).

### 2. `KGAdapter` (`src/adapters/kg_adapter.py`)

Wraps `mempalace.knowledge_graph.KnowledgeGraph`
(`src/adapters/kg_adapter.py:25,36`). Backs the **temporal KG**, persisted in a
**separate SQLite file** at `OPERATUM_BRIDGE_KG_PATH`
(`src/main.py:67-68`). The two stores are separate by MemPalace's design — KG
concerns are temporal/relational, palace concerns are verbatim/vector — but sit
on the same `/data` volume so backups are atomic
(`src/adapters/kg_adapter.py:1-7`).

Triple shape and translations (`src/adapters/kg_adapter.py:9-21,40-103`):

- `slugify_entity` lower-cases and replaces spaces with `_`, matching
  MemPalace's own rule so add/query round-trip (`:28-30`).
- `add_triple` best-effort registers both entities, then calls MemPalace's
  `add_triple` with the renamed fields `obj` (not `object`) and `adapter_name`
  (defaulting `source` to `"operatum"`). MemPalace returns no opaque id, so the
  adapter echoes the `(subject, predicate, obj, valid_from)` tuple (`:40-65`).
- `query_entity` passes `direction` straight through to MemPalace for `outgoing`
  / `incoming`, and implements `both` locally by merging the two queries and
  deduping on the `(subject, predicate, obj, valid_from)` key (`:67-87`).
  > **Note:** the inline comment on `TripleQuery.direction`
  > (`src/routes/kg.py:26`) labels the values `'subject' | 'object' | 'both'`,
  > which is stale — the adapter only interprets `outgoing` / `incoming` /
  > `both`. The default is `both` (`src/routes/kg.py:23-27`).
- `invalidate_triple` maps to MemPalace's `invalidate(subject, predicate, obj,
  ended=valid_to)`, keyed on the s/p/o tuple (`:89-99`).
- `stats` and `count` swallow exceptions and return sentinel `-1` values rather
  than propagating (`src/adapters/kg_adapter.py:105-109`,
  `src/adapters/chroma_palace.py:134-138`), so `/healthz` stays up even if a
  store is unreadable.

The bridge holds no other persistence — no Postgres, no separate embedding
service. Embeddings are ChromaDB's bundled ONNX model, downloaded on first use
and cached under `HF_HOME` (`Dockerfile:15-23`).

## Wings: a logical layer, not a table

There is no wings store. A wing is a registry row kept inside the palace as a
drawer with id `_wing:<slug>` and `kind="_wing_registry"` metadata
(`src/routes/wings.py:1-12,22,45-61`). `GET /wings` is a metadata-only `get`
filtered on that kind, optionally narrowed by `tenant_id` and excluding
`archived` rows by default (`src/routes/wings.py:80-102`). `PATCH /wings/{slug}`
merges `archived` / `purpose` into that registry drawer and 404s if it is absent
(`src/routes/wings.py:64-77`).

## Error contract

Three app-level exception handlers normalise failures for the HTTP caller
(`src/main.py:102-119`):

- `KeyError → 404 {ok:false, error:"not_found", detail}` — e.g. a missing drawer
  (`src/adapters/chroma_palace.py:66-70`).
- `ValueError → 400 {ok:false, error:"bad_request", detail}`.
- `DrawerConflictError → 409 {ok:false, error:"conflict", detail}` when a
  conditional create finds different content or normalized metadata.

Route-level `HTTPException`s (e.g. the wing-not-found 404 in
`src/routes/wings.py:75-76`) are surfaced by FastAPI directly.

## Runtime / deployment invariants

- **Single replica only.** ChromaDB is single-writer; the Dockerfile pins
  `uvicorn --workers 1` and documents that multi-process against one palace dir
  corrupts the HNSW segments (`Dockerfile:1-8,48-52`). The same invariant is
  required for the process-local conditional-create lock to cover all callers.
- **One `/data` volume** holds the palace dir, the KG SQLite file, and the HF
  embedding cache (`Dockerfile:15-23`, `src/main.py:65-68`).
- **No inbound auth** in this codebase — the service defines no auth middleware;
  it exposes port 8081 and expects to be reached over a trusted/loopback network
  (`Dockerfile:46`, `src/main.py`). The security boundary is the deployment's
  responsibility, not this code's.
- **First-run network egress only** for the embedding-model download
  (`TRANSFORMERS_OFFLINE=0`, `Dockerfile:21`).

## Relationship to the rest of the platform

This repo is the *only* MemPalace-facing component here; the platform-side
callers live in other repositories and are out of scope for this document. What
this repo assumes about them is visible in its own contract and adapter
docstrings:

- Callers pass stable drawer ids of the form `mem-<uuid>`, passed through to
  MemPalace verbatim (`src/adapters/chroma_palace.py:6-10`,
  `src/routes/drawers.py:10-13`).
- Callers always include `tenant_id` in `where` clauses, since isolation is
  logical rather than physical (`src/adapters/chroma_palace.py:36-40`).
- Wing membership is a `wing` metadata field the caller sets at upsert time;
  these routes only manage the *registry* of wing slugs
  (`src/routes/wings.py:1-12`).

Their internal file layout, call sites, and configuration are not described here
to keep this document scoped to this repository.

## Live vs dormant paths

Verified from the code, not from a running instance:

- **Exercised by tests** (`tests/test_*.py`): drawer CRUD, semantic search and
  the `query=None` listing fallback, wing create/list/archive, KG
  add/query/invalidate/timeline, and `/healthz`.
- **Implemented but lightly exercised:** the KG `as_of` temporal filter, the
  `incoming` / `both` query directions, `GET /kg/timeline`, and per-triple
  `confidence` — all wired through the adapter but thin on test coverage, and
  `predicate` is free-form (the example predicates in
  `src/adapters/kg_adapter.py:12` such as `subscribed_to` / `became_unhealthy`
  are illustrative, not enforced).
- **Stale-but-harmless comments** flagged inline above: the `11 endpoints`
  docstring count (`src/main.py:8`) and the `direction` label comment
  (`src/routes/kg.py:26`). Neither affects behaviour.

This repo makes no source-verifiable claim about how many drawers, wings, or
triples a deployed instance currently holds — those are runtime facts, not code.

## Where to start reading

- `src/main.py` — app entry, lifespan, adapter singletons, error handlers.
- `src/adapters/chroma_palace.py` — drawer / search / wing storage + coercion.
- `src/adapters/kg_adapter.py` — temporal KG + field translation.
- `src/routes/{drawers,search,wings,kg,health}.py` — the HTTP surface.
- `tests/conftest.py` — how the palace + KG are booted per test.
