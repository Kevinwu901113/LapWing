"""基于 ChromaDB 的长期向量记忆。"""

import asyncio
import hashlib
import logging
import re
from pathlib import Path
from typing import Any

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
