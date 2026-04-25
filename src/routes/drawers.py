"""POST/GET/PATCH/DELETE /drawers/{id} — CRUD on memory drawers."""

from typing import Any
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

router = APIRouter()


class DrawerWrite(BaseModel):
    drawer_id: str = Field(..., description="Stable id from caller (e.g. mem-<uuid>).")
    content: str = Field(..., description="Verbatim text — vector-indexed.")
    metadata: dict[str, Any] = Field(default_factory=dict)


class DrawerPatch(BaseModel):
    metadata: dict[str, Any] | None = None
    content: str | None = None


def _palace():
    # Late import to dodge circular import at module load.
    from src.main import get_palace
    return get_palace()


@router.post("")
async def upsert_drawer(body: DrawerWrite, palace=Depends(_palace)):
    palace.upsert_drawer(
        drawer_id=body.drawer_id,
        content=body.content,
        metadata=body.metadata,
    )
    return {"ok": True, "drawer_id": body.drawer_id}


@router.get("/{drawer_id}")
async def get_drawer(drawer_id: str, palace=Depends(_palace)):
    return {"ok": True, **palace.get_drawer(drawer_id)}


@router.patch("/{drawer_id}")
async def patch_drawer(drawer_id: str, body: DrawerPatch, palace=Depends(_palace)):
    palace.update_drawer(
        drawer_id=drawer_id,
        metadata=body.metadata,
        content=body.content,
    )
    return {"ok": True, "drawer_id": drawer_id}


@router.delete("/{drawer_id}")
async def delete_drawer(drawer_id: str, palace=Depends(_palace)):
    palace.delete_drawer(drawer_id)
    return {"ok": True, "drawer_id": drawer_id, "deleted": True}
