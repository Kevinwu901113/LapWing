"""基于 ChromaDB 的长期向量记忆。"""

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    import chromadb
except ImportError:  # pragma: no cover - 依赖缺失时延迟到运行期报错
    chromadb = None

logger = logging.getLogger("lapwing.memory.vector_store")

_MAX_COLLECTION_NAME = 63
_MAX_SAFE_CHAT_SEGMENT = 40


def _safe_collection_name(chat_id: str) -> str:
    """将 chat_id 转换为 Chroma 允许的 collection 名称。"""
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(chat_id)).strip("_-")
    if not normalized:
        normalized = "chat"

    prefix = normalized[:_MAX_SAFE_CHAT_SEGMENT].strip("_-") or "chat"
    digest = hashlib.sha1(str(chat_id).encode("utf-8")).hexdigest()[:12]
    name = f"chat_{prefix}_{digest}"
    name = name[:_MAX_COLLECTION_NAME].strip("_-")
    if len(name) < 3:
        return f"chat_{digest}"
    return name


def _normalize_chroma_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Convert app metadata into values accepted by Chroma."""
    normalized: dict[str, Any] = {}
    for key, value in dict(metadata or {}).items():
        if value is None:
            normalized[key] = ""
        elif isinstance(value, (list, tuple)):
            items = [item for item in value if item is not None]
            if not items:
                continue
            normalized[key] = [
                item if isinstance(item, (str, int, float, bool)) else str(item)
                for item in items
            ]
        elif isinstance(value, (str, int, float, bool)):
            normalized[key] = value
        else:
            normalized[key] = str(value)
    return normalized


class VectorStore:
    """封装 ChromaDB PersistentClient，并提供 async 接口。"""

    def __init__(self, db_path: Path):
        if chromadb is None:
            raise RuntimeError("chromadb 未安装，无法启用长期向量记忆")

        self._db_path = Path(db_path)
        self._db_path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(self._db_path))
        self._lock = asyncio.Lock()

    async def upsert(
        self,
        chat_id: str,
        doc_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """写入或更新一条向量记忆。"""
        if not text.strip():
            return

        payload = dict(metadata or {})
        payload.setdefault("chat_id", chat_id)
        payload = _normalize_chroma_metadata(payload)

        async with self._lock:
            await asyncio.to_thread(
                self._upsert_sync,
                chat_id,
                str(doc_id),
                text,
                payload,
            )

    async def search(
        self,
        chat_id: str,
        query: str,
        n_results: int = 3,
    ) -> list[dict[str, Any]]:
        """按语义检索相关记忆。"""
        if not query.strip():
            return []

        async with self._lock:
            raw = await asyncio.to_thread(
                self._search_sync,
                chat_id,
                query,
                n_results,
            )

        documents = (raw.get("documents") or [[]])[0]
        metadatas = (raw.get("metadatas") or [[]])[0]
        distances = (raw.get("distances") or [[]])[0]

        hits: list[dict[str, Any]] = []
        for index, text in enumerate(documents):
            if not text:
                continue
            hits.append(
                {
                    "text": text,
                    "metadata": metadatas[index] if index < len(metadatas) else {},
                    "distance": distances[index] if index < len(distances) else None,
                }
            )
        return hits

    async def delete_chat(self, chat_id: str) -> None:
        """删除指定 chat 的整个向量记忆集合。"""
        async with self._lock:
            await asyncio.to_thread(self._delete_chat_sync, chat_id)

    def _get_collection(self, chat_id: str):
        return self._client.get_or_create_collection(_safe_collection_name(chat_id))

    def _upsert_sync(
        self,
        chat_id: str,
        doc_id: str,
        text: str,
        metadata: dict[str, Any],
    ) -> None:
        collection = self._get_collection(chat_id)
        collection.upsert(
            ids=[doc_id],
            documents=[text],
            metadatas=[metadata],
        )
        logger.debug(f"[vector] upsert chat={chat_id} doc={doc_id}")

    def _search_sync(self, chat_id: str, query: str, n_results: int) -> dict[str, Any]:
        collection = self._get_collection(chat_id)
        result = collection.query(
            query_texts=[query],
            n_results=n_results,
        )
        logger.debug(f"[vector] search chat={chat_id} query={query!r} hits={len((result.get('ids') or [[]])[0])}")
        return result

    def _delete_chat_sync(self, chat_id: str) -> None:
        collection_name = _safe_collection_name(chat_id)
        try:
            self._client.delete_collection(collection_name)
            logger.info(f"[vector] 已删除 chat={chat_id} 的向量记忆集合")
        except Exception as exc:
            message = str(exc).lower()
            if "not found" in message or "does not exist" in message:
                logger.debug(f"[vector] chat={chat_id} 无可删除向量记忆集合")
                return
            raise


# ---------------------------------------------------------------------------
# MemoryVectorStore — 全局单一记忆向量库
# ---------------------------------------------------------------------------


@dataclass
class RecallResult:
    """recall() 返回的单条记忆检索结果。

    ``metadata`` (Step 7) 保留写入时的完整元数据 dict，让上层子系统（Episodic /
    Semantic）能读回它们在 ``add`` 时附加的自定义字段（date / title /
    source_trajectory_ids / category 等）而不用二次查询 collection。
    """
    note_id: str
    file_path: str
    content: str
    score: float                  # 综合排序分
    semantic_similarity: float
    note_type: str
    trust: str
    created_at: str
    parent_note: str | None
    metadata: dict = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.metadata is None:
            object.__setattr__(self, "metadata", {})


@dataclass(frozen=True)
class VectorHit:
    """Generic hit for auxiliary ChromaDB collections."""

    doc_id: str
    text: str
    score: float
    metadata: dict[str, Any]


class MemoryVectorStore:
    """记忆向量库。基于 ChromaDB 单一 collection，为 recall() 提供语义检索 + 排序。"""

    COLLECTION_NAME = "lapwing_memory"

    # recall() 排序权重
    W_SEMANTIC = 0.50
    W_RECENCY = 0.20
    W_TRUST = 0.10
    W_SUMMARY_DEPTH = 0.15
    W_ACCESS_COUNT = 0.05

    TRUST_SCORES = {"self": 1.0, "verified": 0.8, "inferred": 0.5, "external": 0.2}
    MAX_PER_CLUSTER = 2  # 同一簇最多保留条数

    def __init__(self, persist_dir: str = "data/chroma"):
        if chromadb is None:
            raise RuntimeError("chromadb 未安装，无法启用 MemoryVectorStore")

        db_path = Path(persist_dir)
        db_path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(db_path))
        self.collection = self._client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        self._lock = asyncio.Lock()

    async def upsert_collection(
        self,
        *,
        collection: str,
        doc_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Upsert text into a named auxiliary collection."""
        if not text.strip():
            return
        async with self._lock:
            coll = await asyncio.to_thread(
                self._client.get_or_create_collection,
                name=collection,
                metadata={"hnsw:space": "cosine"},
            )
            await asyncio.to_thread(
                coll.upsert,
                ids=[str(doc_id)],
                documents=[text],
                metadatas=[_normalize_chroma_metadata(metadata)],
            )

    async def query_collection(
        self,
        *,
        collection: str,
        query_text: str,
        n_results: int = 3,
    ) -> list[VectorHit]:
        """Query a named auxiliary collection with cosine similarity."""
        if not query_text.strip():
            return []
        async with self._lock:
            coll = await asyncio.to_thread(
                self._client.get_or_create_collection,
                name=collection,
                metadata={"hnsw:space": "cosine"},
            )
            count = await asyncio.to_thread(coll.count)
            if count == 0:
                return []
            raw = await asyncio.to_thread(
                coll.query,
                query_texts=[query_text],
                n_results=min(n_results, count),
                include=["documents", "metadatas", "distances"],
            )

        ids = (raw.get("ids") or [[]])[0]
        documents = (raw.get("documents") or [[]])[0]
        metadatas = (raw.get("metadatas") or [[]])[0]
        distances = (raw.get("distances") or [[]])[0]
        hits: list[VectorHit] = []
        for idx, doc in enumerate(documents):
            if not doc:
                continue
            dist = distances[idx] if idx < len(distances) else 1.0
            hits.append(VectorHit(
                doc_id=ids[idx] if idx < len(ids) else "",
                text=doc,
                score=max(0.0, 1.0 - float(dist)),
                metadata=dict(metadatas[idx] if idx < len(metadatas) else {}),
            ))
        return hits

    async def add(self, note_id: str, content: str, metadata: dict) -> None:
        """Upsert 一条笔记到向量库。"""
        stored_meta = dict(metadata)
        stored_meta.setdefault("access_count", 0)
        stored_meta.setdefault("parent_note", "")
        stored_meta = _normalize_chroma_metadata(stored_meta)

        async with self._lock:
            await asyncio.to_thread(
                self.collection.upsert,
                ids=[note_id],
                documents=[content],
                metadatas=[stored_meta],
            )
        logger.debug(f"[memory_vector] upsert note_id={note_id}")

    async def recall(
        self,
        query: str,
        top_k: int = 10,
        *,
        where: dict | None = None,
    ) -> list[RecallResult]:
        """语义检索 + 综合评分 + 簇去重，返回 top_k 条。

        ``where`` 是 ChromaDB 元数据 filter（Step 7 新增）。None 表示查全量；
        例如 ``{"note_type": "episodic"}`` 限定只查情景层。Filter 应用在
        Chroma 侧，比 Python 端 post-filter 更高效。
        """
        # 空库保护
        async with self._lock:
            count = await asyncio.to_thread(self.collection.count)
        if count == 0:
            return []

        n_fetch = min(top_k * 3, 50, count)

        query_kwargs: dict = {
            "query_texts": [query],
            "n_results": n_fetch,
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            query_kwargs["where"] = where

        async with self._lock:
            raw = await asyncio.to_thread(
                self.collection.query,
                **query_kwargs,
            )

        ids = (raw.get("ids") or [[]])[0]
        documents = (raw.get("documents") or [[]])[0]
        metadatas = (raw.get("metadatas") or [[]])[0]
        distances = (raw.get("distances") or [[]])[0]

        now = datetime.now(tz=timezone.utc)

        scored: list[tuple[float, float, str, str, dict]] = []  # (score, sim, id, doc, meta)
        for idx, doc in enumerate(documents):
            if not doc:
                continue
            meta = metadatas[idx] if idx < len(metadatas) else {}
            dist = distances[idx] if idx < len(distances) else 1.0
            note_id = ids[idx] if idx < len(ids) else ""

            # 语义相似度
            sim = max(0.0, 1.0 - dist)

            # 时间衰减
            recency = 0.0
            created_at_str = meta.get("created_at", "")
            if created_at_str:
                try:
                    created_dt = datetime.fromisoformat(created_at_str)
                    if created_dt.tzinfo is None:
                        created_dt = created_dt.replace(tzinfo=timezone.utc)
                    age_days = max(0.0, (now - created_dt).total_seconds() / 86400)
                    recency = 2 ** (-age_days / 7)
                except ValueError:
                    recency = 0.5

            # 信任分
            trust = meta.get("trust", "inferred")
            trust_score = self.TRUST_SCORES.get(trust, 0.5)

            # 摘要深度加成
            note_type = meta.get("note_type", "")
            summary_boost = 1.0 if note_type == "summary" else 0.5

            # 访问频次归一
            access_count = int(meta.get("access_count", 0))
            access_norm = min(access_count / 10.0, 1.0)

            score = (
                self.W_SEMANTIC * sim
                + self.W_RECENCY * recency
                + self.W_TRUST * trust_score
                + self.W_SUMMARY_DEPTH * summary_boost
                + self.W_ACCESS_COUNT * access_norm
            )
            scored.append((score, sim, note_id, doc, meta))

        # 按分数降序排列
        scored.sort(key=lambda x: x[0], reverse=True)

        # 簇去重：同簇最多保留 MAX_PER_CLUSTER 条
        selected: list[tuple[float, float, str, str, dict]] = []
        cluster_counts: list[int] = []  # 与 selected 等长，记录同簇已选数

        for item in scored:
            score, sim, note_id, doc, meta = item
            # 判断是否属于已有某个簇
            assigned_cluster: int | None = None
            for ci, sel in enumerate(selected):
                overlap = self._content_overlap(doc, sel[3])
                if overlap > 0.7:
                    assigned_cluster = ci
                    break

            if assigned_cluster is None:
                # 新簇
                selected.append(item)
                cluster_counts.append(1)
            elif cluster_counts[assigned_cluster] < self.MAX_PER_CLUSTER:
                selected.append(item)
                cluster_counts[assigned_cluster] += 1
            # 否则该簇已满，跳过

            if len(selected) >= top_k:
                break

        # 构造返回结果，并增加 access_count
        results: list[RecallResult] = []
        for score, sim, note_id, doc, meta in selected:
            self._increment_access(note_id)
            results.append(RecallResult(
                note_id=note_id,
                file_path=meta.get("file_path", ""),
                content=doc,
                score=score,
                semantic_similarity=sim,
                note_type=meta.get("note_type", ""),
                trust=meta.get("trust", ""),
                created_at=meta.get("created_at", ""),
                parent_note=meta.get("parent_note") or None,
                metadata=dict(meta),
            ))
        return results

    async def remove(self, note_id: str) -> None:
        """删除指定笔记，找不到时静默忽略。"""
        try:
            async with self._lock:
                await asyncio.to_thread(self.collection.delete, ids=[note_id])
            logger.debug(f"[memory_vector] removed note_id={note_id}")
        except Exception as exc:
            msg = str(exc).lower()
            if "not found" in msg or "does not exist" in msg:
                logger.debug(f"[memory_vector] note_id={note_id} 不存在，忽略删除")
                return
            raise

    async def rebuild(self, notes: list[dict]) -> None:
        """全量重建：删除旧 collection，重建后逐条写入。"""
        async with self._lock:
            await asyncio.to_thread(
                self._client.delete_collection, self.COLLECTION_NAME
            )
            self.collection = await asyncio.to_thread(
                self._client.get_or_create_collection,
                name=self.COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
        # 逐条写入（无需额外加锁，rebuild 调用方自行保证独占）
        for note in notes:
            meta = dict(note.get("meta", {}))
            meta.setdefault("access_count", 0)
            meta.setdefault("parent_note", "")
            meta = _normalize_chroma_metadata(meta)
            await asyncio.to_thread(
                self.collection.upsert,
                ids=[note["note_id"]],
                documents=[note["content"]],
                metadatas=[meta],
            )
        logger.info(f"[memory_vector] rebuild 完成，共 {len(notes)} 条")

    def _increment_access(self, note_id: str) -> None:
        """同步方法：将指定笔记的 access_count +1。"""
        try:
            result = self.collection.get(ids=[note_id], include=["metadatas"])
            metas = result.get("metadatas") or []
            if not metas:
                return
            meta = dict(metas[0])
            meta["access_count"] = int(meta.get("access_count", 0)) + 1
            self.collection.update(ids=[note_id], metadatas=[meta])
        except Exception as exc:
            logger.debug(f"[memory_vector] _increment_access failed for {note_id}: {exc}")

    def _content_overlap(self, a: str, b: str, n: int = 3) -> float:
        """N-gram 重叠率：|交集| / min(|a_ngrams|, |b_ngrams|)。空串返回 0.0。"""
        if not a or not b:
            return 0.0
        tokens_a = a.lower().split()
        tokens_b = b.lower().split()
        if len(tokens_a) < n or len(tokens_b) < n:
            # 文本过短时退化为词级 Jaccard
            set_a = set(tokens_a)
            set_b = set(tokens_b)
            denom = min(len(set_a), len(set_b))
            return len(set_a & set_b) / denom if denom else 0.0
        ngrams_a = {tuple(tokens_a[i:i + n]) for i in range(len(tokens_a) - n + 1)}
        ngrams_b = {tuple(tokens_b[i:i + n]) for i in range(len(tokens_b) - n + 1)}
        denom = min(len(ngrams_a), len(ngrams_b))
        if denom == 0:
            return 0.0
        return len(ngrams_a & ngrams_b) / denom
