"""Knowledge graph routes — temporal entity-relationship triples."""

from typing import Any
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

router = APIRouter()


class TripleAdd(BaseModel):
    subject: str
    predicate: str
    # `object` is a Python builtin; pydantic v2 lets us alias.
    object_: str = Field(..., alias="object")
    valid_from: str | None = None
    valid_to: str | None = None
    confidence: float = 1.0
    source: str | None = None

    model_config = {"populate_by_name": True}


class TripleQuery(BaseModel):
    entity: str
    as_of: str | None = None
    direction: str = "both"   # 'subject' | 'object' | 'both'


class TripleInvalidate(BaseModel):
    """mempalace identifies triples by (subject, predicate, object)
    rather than by an opaque id. The Node-side caller queries first,
    then sends the tuple it wants to end + the timestamp."""
    subject: str
    predicate: str
    object_: str = Field(..., alias="object")
    valid_to: str

    model_config = {"populate_by_name": True}


def _kg():
    from src.main import get_kg
    return get_kg()


@router.post("/triples")
async def add_triple(body: TripleAdd, kg=Depends(_kg)):
    ident = kg.add_triple(
        subject=body.subject,
        predicate=body.predicate,
        object_=body.object_,
        valid_from=body.valid_from,
        valid_to=body.valid_to,
        confidence=body.confidence,
        source=body.source,
    )
    # Return the tuple as a stable ident the caller can echo back to
    # /kg/invalidate. mempalace itself doesn't use opaque ids.
    return {"ok": True, "ident": ident}


@router.post("/query")
async def query_triples(body: TripleQuery, kg=Depends(_kg)):
    rows = kg.query_entity(
        entity=body.entity,
        as_of=body.as_of,
        direction=body.direction,
    )
    return {"ok": True, "triples": rows, "count": len(rows)}


@router.post("/invalidate")
async def invalidate_triple(body: TripleInvalidate, kg=Depends(_kg)):
    kg.invalidate_triple(
        subject=body.subject,
        predicate=body.predicate,
        object_=body.object_,
        valid_to=body.valid_to,
    )
    return {
        "ok": True,
        "subject": body.subject,
        "predicate": body.predicate,
        "object": body.object_,
    }


@router.get("/timeline/{entity}")
async def timeline(entity: str, kg=Depends(_kg)):
    rows = kg.timeline(entity=entity)
    return {"ok": True, "events": rows, "count": len(rows)}


@router.get("/stats")
async def stats(kg=Depends(_kg)):
    return {"ok": True, **kg.stats()}
