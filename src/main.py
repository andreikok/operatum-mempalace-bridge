"""
operatum-mempalace-bridge — FastAPI app entry point.

Sits in front of:
  - mempalace.backends.chroma.ChromaBackend  (drawers + vector search)
  - mempalace.knowledge_graph.KnowledgeGraph  (temporal ER triples)

Surface (11 endpoints, see route modules):
  POST   /drawers              create / upsert
  GET    /drawers/{id}         fetch
  PATCH  /drawers/{id}         move room or update metadata
  DELETE /drawers/{id}
  POST   /search               topic-glob filter + semantic search
  POST   /wings                create or archive
  PATCH  /wings/{slug}
  GET    /wings                list
  POST   /kg/triples           add (entity, predicate, object, validity)
  POST   /kg/query             query by entity, optional as_of
  POST   /kg/invalidate        set valid_to on a triple
  GET    /kg/timeline/{entity} chronological events
  GET    /healthz              liveness + drawer counts + segment counts

Runtime invariants:
  * Single replica only — ChromaDB is single-writer.
  * Palace + KG live at MEMPALACE_PALACE_PATH + OPERATUM_BRIDGE_KG_PATH.
  * Failures never leak chromadb internals; surface a clean 4xx/5xx
    JSON shape the Node-side adapter can interpret.
"""

import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from src.adapters.chroma_palace import ChromaPalaceAdapter
from src.adapters.kg_adapter import KGAdapter
from src.routes import drawers, search, wings, kg, health


# Process-wide singletons. mempalace's ChromaBackend caches a client
# keyed by (inode, mtime) of chroma.sqlite3 — instantiating multiple
# adapters here would just yield the same underlying client, but we
# keep one for clarity.
_palace: ChromaPalaceAdapter | None = None
_kg: KGAdapter | None = None


def get_palace() -> ChromaPalaceAdapter:
    """FastAPI dependency. Routes import this to get the shared adapter."""
    if _palace is None:
        raise RuntimeError("palace adapter not initialised — lifespan failed")
    return _palace


def get_kg() -> KGAdapter:
    if _kg is None:
        raise RuntimeError("kg adapter not initialised — lifespan failed")
    return _kg


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Boot the palace + KG adapters once; tear down on shutdown."""
    global _palace, _kg
    palace_path = os.environ.get(
        "MEMPALACE_PALACE_PATH", "/data/palace")
    kg_path = os.environ.get(
        "OPERATUM_BRIDGE_KG_PATH", "/data/knowledge_graph.sqlite3")
    _palace = ChromaPalaceAdapter(palace_path=palace_path)
    _kg = KGAdapter(db_path=kg_path)
    try:
        yield
    finally:
        try:
            _palace.close()
        except Exception:  # noqa: BLE001 — best-effort shutdown
            pass
        try:
            _kg.close()
        except Exception:
            pass
        _palace = None
        _kg = None


app = FastAPI(
    title="operatum-mempalace-bridge",
    version="0.1.0",
    description="FastAPI wrapper around MemPalace (drawers + KG) for the operatum-memory Node service.",
    lifespan=lifespan,
)

# Wire up the route modules. Each module imports get_palace / get_kg
# from this module and uses Depends() for adapter access.
app.include_router(health.router)
app.include_router(drawers.router, prefix="/drawers", tags=["drawers"])
app.include_router(search.router, tags=["search"])
app.include_router(wings.router, prefix="/wings", tags=["wings"])
app.include_router(kg.router, prefix="/kg", tags=["kg"])


@app.exception_handler(KeyError)
async def key_error_handler(_request, exc: KeyError):
    """Map missing-resource KeyErrors to 404 with a Node-friendly shape."""
    return JSONResponse(status_code=404, content={
        "ok": False,
        "error": "not_found",
        "detail": str(exc).strip("'"),
    })


@app.exception_handler(ValueError)
async def value_error_handler(_request, exc: ValueError):
    """Map ValueErrors (invalid input) to 400."""
    return JSONResponse(status_code=400, content={
        "ok": False,
        "error": "bad_request",
        "detail": str(exc),
    })
