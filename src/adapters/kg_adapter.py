"""
kg_adapter.py — wraps mempalace.knowledge_graph.KnowledgeGraph.

The KG is a separate SQLite file from the palace (mempalace's
design — KG concerns are temporal/relational, palace concerns are
verbatim/vector). Both share the same /data volume in the docker
sidecar so backups are atomic from the orchestration layer.

Triple shape:
  subject     — entity name (e.g. "thread <uuid>", "agent <uuid>")
  predicate   — verb ("spawned_by", "subscribed_to", "became_unhealthy")
  object      — entity name OR free-text fact
  valid_from  — ISO8601 timestamp; defaults to NOW
  valid_to    — ISO8601 timestamp or None (still valid)
  confidence  — 0..1 float; defaults to 1.0
  source      — provenance ("agent_spawn", "deploy_verifier", etc.)

Entity ids are slugified by mempalace (lower-case, spaces → _). We
pre-slugify here so the route layer's "entity" parameter matches
exactly when querying.
"""

from typing import Any

from mempalace.knowledge_graph import KnowledgeGraph


def slugify_entity(name: str) -> str:
    """Match mempalace's own slugify rule so query/add round-trip cleanly."""
    return name.lower().replace(" ", "_")


class KGAdapter:
    def __init__(self, db_path: str):
        self._db_path = db_path
        self._kg = KnowledgeGraph(db_path=db_path)

    # ── Triples ────────────────────────────────────────────────────

    def add_triple(self, *, subject: str, predicate: str, object_: str,
                   valid_from: str | None = None,
                   valid_to: str | None = None,
                   confidence: float = 1.0,
                   source: str | None = None) -> str:
        """Insert one triple. Returns the inserted triple's id (mempalace
        assigns this internally)."""
        # Ensure both endpoints are registered as entities first;
        # mempalace's add_triple expects them to exist.
        s = slugify_entity(subject)
        o = slugify_entity(object_)
        try:
            self._kg.add_entity(name=subject)
        except Exception:  # noqa: BLE001 — already-exists is fine
            pass
        try:
            self._kg.add_entity(name=object_)
        except Exception:
            pass
        triple_id = self._kg.add_triple(
            subject=s, predicate=predicate, object=o,
            valid_from=valid_from, valid_to=valid_to,
            confidence=confidence, source_adapter=source or "operatum",
        )
        return triple_id

    def query_entity(self, *, entity: str, as_of: str | None = None,
                     direction: str = "both") -> list[dict[str, Any]]:
        """Walk triples touching `entity`. Direction: 'subject'/'object'/'both'."""
        return self._kg.query_entity(
            name=slugify_entity(entity),
            as_of=as_of,
            direction=direction,
        )

    def invalidate_triple(self, *, triple_id: str, valid_to: str) -> None:
        self._kg.invalidate(triple_id=triple_id, valid_to=valid_to)

    def timeline(self, *, entity: str) -> list[dict[str, Any]]:
        """Chronological events for `entity`. Used for "what happened to
        app X over time" queries."""
        return self._kg.timeline(name=slugify_entity(entity))

    def stats(self) -> dict[str, Any]:
        try:
            return self._kg.stats()
        except Exception:  # noqa: BLE001
            return {"entities": -1, "triples": -1}

    def close(self) -> None:
        try:
            self._kg.close()
        except Exception:  # noqa: BLE001
            pass
