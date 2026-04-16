"""笔记/记忆 REST API — Phase 5。

提供 NoteStore 文件树浏览、内容读取、关键词搜索、语义检索。
"""

import logging

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger("lapwing.api.routes.notes_v2")

router = APIRouter(prefix="/api/v2/notes", tags=["notes-v2"])

_note_store = None
_memory_vector_store = None


def init(note_store, memory_vector_store=None) -> None:
    global _note_store, _memory_vector_store
    _note_store = note_store
    _memory_vector_store = memory_vector_store


@router.get("/tree")
async def get_notes_tree(path: str = Query("", description="子目录路径")):
    """笔记目录树。"""
    if _note_store is None:
        return {"path": path, "entries": []}
    entries = _note_store.list_notes(path or None)
    return {"path": path, "entries": entries}


@router.get("/content")
async def get_note_content(
    note_id: str = Query(None, description="笔记 ID"),
    path: str = Query(None, description="文件路径"),
):
    """获取笔记内容。传 note_id 或 path。"""
    if _note_store is None:
        raise HTTPException(status_code=503, detail="NoteStore not available")
    if not note_id and not path:
        raise HTTPException(status_code=400, detail="Provide note_id or path")

    result = _note_store.read(note_id or path)
    if not result:
        raise HTTPException(status_code=404, detail="Note not found")

    return {
        "meta": result["meta"],
        "content": result["content"],
        "file_path": result["file_path"],
    }


@router.get("/search")
async def search_notes(
    q: str = Query(..., description="关键词"),
    limit: int = Query(20, ge=1, le=100),
):
    """关键词搜索。"""
    if _note_store is None:
        return {"query": q, "results": []}
    results = _note_store.search_keyword(q, limit=limit)
    return {"query": q, "results": results}


@router.get("/recall")
async def recall_notes(
    q: str = Query(..., description="语义查询"),
    top_k: int = Query(10, ge=1, le=50),
):
    """语义检索（向量库）。"""
    if _memory_vector_store is None:
        return {"query": q, "results": []}

    results = await _memory_vector_store.recall(q, top_k=top_k)
    return {
        "query": q,
        "results": [
            {
                "note_id": r.note_id,
                "content": r.content[:500],
                "score": r.score,
                "note_type": r.note_type,
                "file_path": r.file_path,
            }
            for r in results
        ],
    }
