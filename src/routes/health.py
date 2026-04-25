"""GET /healthz — liveness + a few cheap stats."""

import os
from fastapi import APIRouter, Depends

router = APIRouter()


def _palace():
    from src.main import get_palace
    return get_palace()


def _kg():
    from src.main import get_kg
    return get_kg()


@router.get("/healthz")
async def healthz(palace=Depends(_palace), kg=Depends(_kg)):
    return {
        "ok": True,
        "palace_path": os.environ.get(
            "MEMPALACE_PALACE_PATH", "/data/palace"),
        "kg_path": os.environ.get(
            "OPERATUM_BRIDGE_KG_PATH", "/data/knowledge_graph.sqlite3"),
        "drawer_count": palace.count(),
        "kg_stats": kg.stats(),
    }
