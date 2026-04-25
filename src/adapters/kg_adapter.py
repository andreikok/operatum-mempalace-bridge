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
                   source: str | None = None) -> dict[str, Any]:
        """Insert one triple. Returns a tuple identifier
        { subject, predicate, object } — mempalace's add_triple
        doesn't return an opaque id, the (s,p,o) tuple itself is the
        identity (with valid_from for temporal disambiguation)."""
        s = slugify_entity(subject)
        o = slugify_entity(object_)
        # add_entity(name=...) is the mempalace-3.3.3 signature.
        try: self._kg.add_entity(name=subject)
        except Exception: pass  # noqa: BLE001
        try: self._kg.add_entity(name=object_)
        except Exception: pass
        # mempalace's add_triple uses `obj` (not `object`) +
        # `adapter_name` (not `source_adapter`). Returns None on
        # success.
        self._kg.add_triple(
            subject=s, predicate=predicate, obj=o,
            valid_from=valid_from, valid_to=valid_to,
            confidence=confidence, adapter_name=source or "operatum",
        )
        return {"subject": s, "predicate": predicate, "obj": o,
                "valid_from": valid_from}

    def query_entity(self, *, entity: str, as_of: str | None = None,
                     direction: str = "outgoing") -> list[dict[str, Any]]:
        """Walk triples touching `entity`. mempalace direction is
        'outgoing' / 'incoming'; we accept 'both' as a convenience
        and merge the two queries."""
        name = slugify_entity(entity)
        if direction == "both":
            out = self._kg.query_entity(name=name, as_of=as_of, direction="outgoing")
            inc = self._kg.query_entity(name=name, as_of=as_of, direction="incoming")
            seen = set()
            merged: list[dict[str, Any]] = []
            for r in (out or []) + (inc or []):
                key = (r.get("subject"), r.get("predicate"), r.get("obj"),
                       r.get("valid_from"))
                if key in seen:
                    continue
                seen.add(key)
                merged.append(r)
            return merged
        return self._kg.query_entity(
            name=name, as_of=as_of, direction=direction)

    def invalidate_triple(self, *, subject: str, predicate: str,
                          object_: str, valid_to: str) -> None:
        """mempalace's invalidate is keyed on the (s, p, o) tuple
        plus an `ended` timestamp. Triples don't carry opaque ids
        in mempalace's model."""
        self._kg.invalidate(
            subject=slugify_entity(subject),
            predicate=predicate,
            obj=slugify_entity(object_),
            ended=valid_to,
        )

    def timeline(self, *, entity: str) -> list[dict[str, Any]]:
        """Chronological events for `entity`."""
        return self._kg.timeline(entity_name=slugify_entity(entity))

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
