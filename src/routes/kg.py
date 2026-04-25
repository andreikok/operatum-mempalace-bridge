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
    triple_id: str
    valid_to: str


def _kg():
    from src.main import get_kg
    return get_kg()


@router.post("/triples")
async def add_triple(body: TripleAdd, kg=Depends(_kg)):
    triple_id = kg.add_triple(
        subject=body.subject,
        predicate=body.predicate,
        object_=body.object_,
        valid_from=body.valid_from,
        valid_to=body.valid_to,
        confidence=body.confidence,
        source=body.source,
    )
    return {"ok": True, "triple_id": triple_id}


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
    kg.invalidate_triple(triple_id=body.triple_id, valid_to=body.valid_to)
    return {"ok": True, "triple_id": body.triple_id}


@router.get("/timeline/{entity}")
async def timeline(entity: str, kg=Depends(_kg)):
    rows = kg.timeline(entity=entity)
    return {"ok": True, "events": rows, "count": len(rows)}


@router.get("/stats")
async def stats(kg=Depends(_kg)):
    return {"ok": True, **kg.stats()}
