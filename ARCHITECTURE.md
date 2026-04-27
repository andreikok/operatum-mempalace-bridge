# operatum-mempalace-bridge — architecture

FastAPI service that wraps [MemPalace](https://github.com/MemPalace/mempalace)
for the Node-side `operatum-memory` package. Runs as a Docker
sidecar inside the Operatum compose stack; the gateway talks to
it over loopback HTTP.

This is the **Python side** of Operatum's memory system. Node
services don't speak directly to MemPalace — they go through
this bridge.

## What it owns

- **Drawers**: verbatim memory rows. ChromaDB-indexed for vector
  similarity search. Each drawer has a tenant + user scope.
- **Mempalace KG**: persistent knowledge-graph nodes + edges
  with embeddings.
- **Reflections**: distilled summaries from the Node-side
  consolidation passes — written through this bridge so MemPalace
  can index them too.

The Node-side `operatum-memory` repo has its own Postgres
mirror; this bridge is the source of truth for vector retrieval.
A consolidation cron (in operatum-memory) keeps both sides in
sync.

## HTTP surface

`:3950`:

| Route | Purpose |
|---|---|
| `POST /drawers` | Insert a memory row. Auto-embeds via OpenAI text-embedding-3-small. |
| `GET /drawers/search?q=…` | Vector + keyword search. Returns top-K with cosine scores. |
| `POST /kg/upsert` | Create/update a KG node. |
| `POST /kg/edges` | Create an edge between two nodes. |
| `GET /kg/neighbors/:node_id?depth=1` | Walk the graph from a node. |
| `POST /reflect` | Write a reflection (summarised episode). |

## Why a separate service

- **Python ecosystem**: ChromaDB + the LangChain bits used by
  MemPalace are Python-native. Reimplementing in Node would
  duplicate effort + skew from upstream.
- **Vector DB lifecycle**: Chroma keeps its index files on disk;
  having a dedicated process means index rebuilds don't take
  down the Node services.
- **CPU isolation**: embedding generation can spike CPU; better
  isolated from the user-facing gateway.

## Storage

- ChromaDB persisted at `~/.operatum/chroma/`.
- KG nodes/edges in Postgres (same gateway DB,
  `mempalace_kg_*` tables).
- Reflections in Postgres (`memory_reflections`).

## Integration with operatum-memory (Node)

The Node side calls this bridge for:

- Every `memory_remember` write — the row goes to BOTH the
  Postgres mirror (synchronous) and this bridge for embedding +
  vector indexing (async).
- Every `memory_context` read with semantic intent — does a
  vector query here, then enriches with KG-walk results.
- Reflection consolidation — Node decides when to consolidate,
  this bridge generates the summary embedding.

The two-sided architecture lets simple keyword reads stay in
Postgres (fast) while semantic reads use this bridge.

## Where to start

- `src/main.py` — FastAPI app entry.
- `src/routers/drawers.py` — drawer CRUD + search.
- `src/routers/kg.py` — KG ops.
- `src/embeddings/openai.py` — embedding client.
