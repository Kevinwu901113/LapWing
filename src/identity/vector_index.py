"""Identity claim vector index — thin wrapper over a single Chroma collection.

Used by IdentityRetriever to score claims by query relevance, and by the
outbox drain in IdentityStore to keep the collection in sync with claim
revisions. Embeddings come from Chroma's default embedding function;
metadata fields (sensitivity, claim_type, confidence, source_file) ride
along so retrieval can apply where-filters server-side.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

try:
    import chromadb
except ImportError:  # pragma: no cover
    chromadb = None

logger = logging.getLogger("lapwing.identity.vector_index")

_COLLECTION_NAME = "identity_claims"


def _normalize_metadata(meta: dict[str, Any] | None) -> dict[str, Any]:
    """Coerce values into Chroma-acceptable scalars."""
    out: dict[str, Any] = {}
    for k, v in dict(meta or {}).items():
        if v is None:
            out[k] = ""
        elif isinstance(v, (str, int, float, bool)):
            out[k] = v
        else:
            out[k] = str(v)
    return out


class IdentityVectorIndex:
    """Single Chroma collection for identity claims with cosine similarity."""

    COLLECTION_NAME = _COLLECTION_NAME

    def __init__(self, persist_dir: Path | str) -> None:
        if chromadb is None:
            raise RuntimeError("chromadb 未安装，无法启用 IdentityVectorIndex")
        path = Path(persist_dir)
        path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(path))
        self._collection = self._client.get_or_create_collection(
            name=_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        self._lock = asyncio.Lock()

    async def upsert(
        self,
        *,
        claim_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not text.strip():
            return
        meta = _normalize_metadata(metadata)
        async with self._lock:
            await asyncio.to_thread(
                self._collection.upsert,
                ids=[claim_id],
                documents=[text],
                metadatas=[meta],
            )

    async def delete(self, claim_id: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._collection.delete, ids=[claim_id])

    async def count(self) -> int:
        async with self._lock:
            return await asyncio.to_thread(self._collection.count)

    async def query(
        self,
        *,
        query_text: str,
        n_results: int = 10,
        where: dict[str, Any] | None = None,
    ) -> list[tuple[str, float]]:
        """Return [(claim_id, distance), ...] sorted by distance ascending.

        ``where`` follows Chroma syntax — e.g. ``{"sensitivity": {"$in": ["public"]}}``.
        Distance is the cosine distance Chroma reports; callers convert to a
        similarity score with ``1 - distance``.
        """
        if not query_text.strip():
            return []
        async with self._lock:
            count = await asyncio.to_thread(self._collection.count)
            if count == 0:
                return []
            raw = await asyncio.to_thread(
                self._collection.query,
                query_texts=[query_text],
                n_results=min(n_results, count),
                where=where,
                include=["distances"],
            )
        ids = (raw.get("ids") or [[]])[0]
        distances = (raw.get("distances") or [[]])[0]
        return [
            (ids[i], float(distances[i]) if i < len(distances) else 1.0)
            for i in range(len(ids))
        ]


async def drain_outbox(store, vector_index: IdentityVectorIndex) -> dict:
    """Apply every pending identity_index_outbox row to the vector index.

    For each row:
      - action='upsert_vector' → look up current claim, upsert into Chroma
      - action='delete_vector' → delete by claim_id

    Returns counts {'upserted', 'deleted', 'skipped', 'failed'}. A row that
    references a missing or non-active claim is marked processed and counted
    as skipped — the outbox semantics treat consumed rows as resolved
    regardless of outcome.
    """
    from src.identity.auth import create_system_auth
    auth = create_system_auth()
    counts = {"upserted": 0, "deleted": 0, "skipped": 0, "failed": 0}
    pending = await store._get_pending_outbox()
    for row in pending:
        outbox_id = row["outbox_id"]
        claim_id = row["claim_id"]
        action = row["action"]
        try:
            if action == "delete_vector":
                await vector_index.delete(claim_id)
                counts["deleted"] += 1
            elif action == "upsert_vector":
                claim = await store.get_claim(claim_id, auth)
                if claim is None or (claim.status not in ("active",) and getattr(claim.status, "value", None) != "active"):
                    counts["skipped"] += 1
                else:
                    await vector_index.upsert(
                        claim_id=claim_id,
                        text=claim.object_val or "",
                        metadata={
                            "claim_type": claim.claim_type if isinstance(claim.claim_type, str) else getattr(claim.claim_type, "value", str(claim.claim_type)),
                            "sensitivity": claim.sensitivity if isinstance(claim.sensitivity, str) else getattr(claim.sensitivity, "value", str(claim.sensitivity)),
                            "confidence": float(claim.confidence),
                            "source_file": claim.source_file or "",
                            "owner": claim.owner if isinstance(claim.owner, str) else getattr(claim.owner, "value", str(claim.owner)),
                        },
                    )
                    counts["upserted"] += 1
            else:
                counts["skipped"] += 1
            await store.mark_outbox_processed(outbox_id)
        except Exception:
            logger.warning("outbox row %s (%s/%s) failed", outbox_id, action, claim_id, exc_info=True)
            counts["failed"] += 1
            # Leave processed_at NULL so a future drain can retry.
    return counts
