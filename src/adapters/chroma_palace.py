"""
chroma_palace.py — thin wrapper over mempalace.backends.chroma so the
route modules don't have to know about PalaceRef / BaseCollection
shapes.

Drawer-id contract: the Node side passes drawer ids of the form
`mem-<uuid>` (the operatum-memory schema's stable id). We pass them
through to mempalace verbatim — mempalace's drawer ids can be any
string, the canonical form just isn't used unless we'd want
mempalace_search to pick them up by key.

Wing routing contract:
  user-<short>     — durable user-scoped memories (preferences,
                     tooling, durable critical)
  thread-<short>   — thread-scoped memories (episodic, short)
                     drops on the next archive sweep when the thread
                     is closed.

Both wings live in the same `mempalace_drawers` collection as
metadata; mempalace doesn't enforce per-wing collections (per its
RFC 003 §2.2 — wings are a logical layer).
"""

from typing import Any

from mempalace.backends.chroma import ChromaBackend
from mempalace.backends.base import PalaceRef


class ChromaPalaceAdapter:
    """One palace, one collection, one (inode, mtime)-keyed client."""

    def __init__(self, palace_path: str):
        self._palace_path = palace_path
        self._backend = ChromaBackend()
        # Synthesise a PalaceRef the backend understands. tenant
        # isolation comes from the metadata `tenant_id` field that
        # the Node side ALWAYS includes in `where` clauses — we
        # don't run a separate palace per tenant (would not scale
        # past a few tenants without segment churn).
        self._palace = PalaceRef(
            id="operatum-shared",
            local_path=palace_path,
            namespace="operatum",
        )
        self._collection = self._backend.get_collection(
            palace=self._palace,
            collection_name="mempalace_drawers",
            create=True,
        )

    # ── Drawer CRUD ────────────────────────────────────────────────

    def upsert_drawer(self, *, drawer_id: str, content: str,
                      metadata: dict[str, Any]) -> None:
        """Insert or update one drawer."""
        # ChromaDB metadata MUST be flat scalars (str/int/float/bool).
        # We coerce here so the route handlers don't have to.
        flat = _coerce_metadata(metadata)
        self._collection.upsert(
            ids=[drawer_id],
            documents=[content],
            metadatas=[flat],
        )

    def get_drawer(self, drawer_id: str) -> dict[str, Any]:
        """Fetch one drawer by id. Raises KeyError if missing."""
        result = self._collection.get(ids=[drawer_id])
        if not result.documents or not result.documents[0]:
            raise KeyError(drawer_id)
        return {
            "drawer_id": result.ids[0],
            "content": result.documents[0],
            "metadata": result.metadatas[0] if result.metadatas else {},
        }

    def delete_drawer(self, drawer_id: str) -> None:
        """Idempotent delete — missing is not an error (caller already
        considers the drawer gone)."""
        self._collection.delete(ids=[drawer_id])

    def update_drawer(self, *, drawer_id: str,
                      metadata: dict[str, Any] | None = None,
                      content: str | None = None) -> None:
        """Partial update. Pulls the existing drawer, merges, upserts."""
        cur = self.get_drawer(drawer_id)
        new_meta = {**(cur["metadata"] or {}), **(metadata or {})}
        new_content = content if content is not None else cur["content"]
        self.upsert_drawer(
            drawer_id=drawer_id,
            content=new_content,
            metadata=new_meta,
        )

    # ── Search ─────────────────────────────────────────────────────

    def search(self, *, query: str | None,
               where: dict[str, Any] | None = None,
               n_results: int = 10) -> list[dict[str, Any]]:
        """Semantic search with optional metadata filter.
        `query=None` falls back to a metadata-only get (for "list
        recent in this wing/room" use cases that don't need vector
        ranking)."""
        normalised = _normalise_where(where)
        if query:
            qr = self._collection.query(
                query_texts=[query],
                n_results=max(1, min(n_results, 100)),
                where=normalised,
            )
            ids = qr.ids[0] if qr.ids else []
            docs = qr.documents[0] if qr.documents else []
            metas = qr.metadatas[0] if qr.metadatas else []
            dists = qr.distances[0] if qr.distances else []
        else:
            gr = self._collection.get(
                where=normalised,
                limit=max(1, min(n_results, 100)),
            )
            ids = gr.ids or []
            docs = gr.documents or []
            metas = gr.metadatas or []
            dists = [None] * len(ids)
        out = []
        for i, drawer_id in enumerate(ids):
            out.append({
                "drawer_id": drawer_id,
                "content": docs[i] if i < len(docs) else "",
                "metadata": metas[i] if i < len(metas) else {},
                "distance": dists[i] if i < len(dists) else None,
            })
        return out

    def count(self) -> int:
        try:
            return self._collection.count()
        except Exception:  # noqa: BLE001
            return -1

    # ── Lifecycle ──────────────────────────────────────────────────

    def close(self) -> None:
        """Close the underlying client. Safe to call multiple times."""
        try:
            self._backend.close_palace(self._palace)
        except Exception:  # noqa: BLE001 — chromadb has no clean stop
            pass


def _normalise_where(where: dict[str, Any] | None) -> dict[str, Any] | None:
    """ChromaDB rejects multi-key top-level wheres — every filter
    must live inside a single operator. Wrap multi-key dicts in
    `$and` automatically so the Node-side caller doesn't have to
    track this. Single-key wheres pass through unchanged.

    Example:
      {tenant_id: {$eq: 'T1'}, kind: {$eq: 'wing'}}
        → {$and: [{tenant_id: {$eq: 'T1'}}, {kind: {$eq: 'wing'}}]}
    """
    if not where:
        return None
    operator_keys = [k for k in where.keys() if k.startswith("$")]
    field_keys = [k for k in where.keys() if not k.startswith("$")]
    if not field_keys:
        return where
    if len(field_keys) == 1 and not operator_keys:
        return where
    # Multi-field — wrap in $and. Operator keys at top level
    # (e.g. existing $or) merge into the $and as siblings.
    clauses = [{k: where[k]} for k in field_keys]
    for op in operator_keys:
        clauses.append({op: where[op]})
    return {"$and": clauses}


def _coerce_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    """ChromaDB metadata only accepts scalars. Lists/dicts must be
    flattened — we encode arrays as semicolon-joined strings (the
    same convention mempalace uses for `entities`)."""
    out: dict[str, Any] = {}
    for k, v in meta.items():
        if v is None:
            continue
        if isinstance(v, (str, int, float, bool)):
            out[k] = v
        elif isinstance(v, (list, tuple)):
            # Filter out non-scalars and join. Empty list → drop the
            # key (Chroma can't store empty arrays).
            scalars = [str(x) for x in v if x is not None]
            if scalars:
                out[k] = ";".join(scalars)
        elif isinstance(v, dict):
            # Dicts get JSON-stringified. Caller can choose to put
            # them in `content` if they need vector indexing.
            import json
            out[k] = json.dumps(v, separators=(",", ":"))
        else:
            out[k] = str(v)
    return out
