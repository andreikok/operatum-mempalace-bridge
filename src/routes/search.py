"""POST /search — semantic + metadata-filter retrieval over drawers."""

from typing import Any
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

router = APIRouter()


class SearchRequest(BaseModel):
    # Optional: when null, falls back to metadata-only listing
    # (the operatum retrieval pipeline calls this for "recent
    # thread" + "critical layer" queries that don't need vector
    # ranking).
    query: str | None = None
    where: dict[str, Any] | None = Field(
        default=None,
        description="ChromaDB-style metadata filter, e.g. "
                    "{'tenant_id': {'$eq': '...'}, 'layer': {'$in': ['critical','working']}}",
    )
    n_results: int = 10


def _palace():
    from src.main import get_palace
    return get_palace()


@router.post("/search")
async def search(body: SearchRequest, palace=Depends(_palace)):
    hits = palace.search(
        query=body.query,
        where=body.where,
        n_results=body.n_results,
    )
    return {"ok": True, "hits": hits, "count": len(hits)}
