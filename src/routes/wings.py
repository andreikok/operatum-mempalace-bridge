"""
Wings: POST /wings (create), PATCH /wings/{slug} (archive), GET /wings (list).

Wings in mempalace are a logical layer over drawers — there's no
"wings" table to write to. We model wing membership purely via the
`wing` metadata field on each drawer (set by the Node side at
upsert time). These routes therefore manage a small *registry* of
wing slugs + descriptions kept inside the palace as a special
`__wing_registry` drawer (key: `_wing:<slug>`). It's a pragmatic
choice — keeping the bridge stateless beyond the palace volume —
and lets the Node side ask "what wings exist for this tenant".
"""

import json
from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

router = APIRouter()


WING_REG_PREFIX = "_wing:"


class WingCreate(BaseModel):
    slug: str
    purpose: str | None = None
    tenant_id: str | None = None


class WingPatch(BaseModel):
    archived: bool | None = None
    purpose: str | None = None


def _palace():
    from src.main import get_palace
    return get_palace()


def _wing_id(slug: str) -> str:
    return f"{WING_REG_PREFIX}{slug}"


@router.post("")
async def create_wing(body: WingCreate, palace=Depends(_palace)):
    metadata: dict[str, Any] = {
        "kind": "_wing_registry",
        "slug": body.slug,
        "archived": False,
    }
    if body.tenant_id:
        metadata["tenant_id"] = body.tenant_id
    if body.purpose:
        metadata["purpose"] = body.purpose
    palace.upsert_drawer(
        drawer_id=_wing_id(body.slug),
        content=body.purpose or body.slug,
        metadata=metadata,
    )
    return {"ok": True, "slug": body.slug}


@router.patch("/{slug}")
async def patch_wing(slug: str, body: WingPatch, palace=Depends(_palace)):
    update: dict[str, Any] = {}
    if body.archived is not None:
        update["archived"] = bool(body.archived)
    if body.purpose is not None:
        update["purpose"] = body.purpose
    if not update:
        return {"ok": True, "slug": slug, "noop": True}
    try:
        palace.update_drawer(drawer_id=_wing_id(slug), metadata=update)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"wing '{slug}' not found")
    return {"ok": True, "slug": slug, "updated": update}


@router.get("")
async def list_wings(tenant_id: str | None = None,
                     include_archived: bool = False,
                     palace=Depends(_palace)):
    where: dict[str, Any] = {"kind": {"$eq": "_wing_registry"}}
    if tenant_id:
        where["tenant_id"] = {"$eq": tenant_id}
    if not include_archived:
        where["archived"] = {"$eq": False}
    # Wings are listed via a metadata-only get (no vector query) —
    # the search() helper handles `query=None` as a get under the hood.
    hits = palace.search(query=None, where=where, n_results=200)
    wings = [
        {
            "slug": h["metadata"].get("slug"),
            "purpose": h["metadata"].get("purpose"),
            "archived": bool(h["metadata"].get("archived", False)),
            "tenant_id": h["metadata"].get("tenant_id"),
        }
        for h in hits
        if h["metadata"].get("slug")
    ]
    return {"ok": True, "wings": wings, "count": len(wings)}
