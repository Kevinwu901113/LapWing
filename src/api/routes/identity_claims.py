"""身份主张 API 端点 — 查询、隐私操作、重建、健康检查。

与 identity.py（soul/voice/constitution 文件 CRUD）使用不同前缀 /api/identity，
避免路由冲突。
"""

import dataclasses
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("lapwing.api.routes.identity_claims")

router = APIRouter(prefix="/api/identity", tags=["identity-claims"])

# 模块级存储/标志/解析器引用
_store = None
_flags = None
_parser = None


def init(*, store=None, flags=None, parser=None):
    global _store, _flags, _parser
    _store = store
    _flags = flags
    _parser = parser


def _require_store():
    """要求 store 已初始化，否则返回 503。"""
    if _store is None:
        raise HTTPException(status_code=503, detail="Identity store not initialized")
    return _store


def _get_auth():
    """创建鉴权上下文。桌面端 = OWNER = kevin 全量作用域。"""
    from src.identity.auth import create_kevin_auth
    return create_kevin_auth(session_id="api")


# ---------------------------------------------------------------------------
# 请求体模型
# ---------------------------------------------------------------------------

class KillswitchRequest(BaseModel):
    enabled: bool


class RedactRequest(BaseModel):
    reason: str
    source_already_redacted: bool = False


class EraseRequest(BaseModel):
    reason: str


class RebuildRequest(BaseModel):
    confirm: bool = False


# ---------------------------------------------------------------------------
# 辅助：将 IdentityClaim 数据类转换为可序列化字典
# ---------------------------------------------------------------------------

def _claim_to_dict(claim) -> dict:
    """将 IdentityClaim 数据类实例转换为普通字典。"""
    if claim is None:
        return {}
    if dataclasses.is_dataclass(claim) and not isinstance(claim, type):
        d = dataclasses.asdict(claim)
    else:
        d = dict(claim)
    # 将枚举值转为字符串（dataclasses.asdict 已处理，兜底）
    for k, v in d.items():
        if hasattr(v, "value"):
            d[k] = v.value
    return d


# ---------------------------------------------------------------------------
# 读路由
# ---------------------------------------------------------------------------

@router.get("/claims")
async def list_claims(status: Optional[str] = None):
    """列出所有身份主张，可按状态过滤（active / deprecated / erased / redacted）。"""
    store = _require_store()
    auth = _get_auth()
    try:
        claims = await store.list_claims(auth, status=status)
        return {"claims": [_claim_to_dict(c) for c in claims]}
    except Exception as exc:
        logger.error("list_claims 失败: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/claims/{claim_id}")
async def get_claim(claim_id: str):
    """按 claim_id 读取单条主张。"""
    store = _require_store()
    auth = _get_auth()
    try:
        claim = await store.get_claim(claim_id, auth)
        if claim is None:
            raise HTTPException(status_code=404, detail=f"Claim {claim_id!r} not found")
        return _claim_to_dict(claim)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("get_claim 失败: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/claims/{claim_id}/evidence")
async def get_claim_evidence(claim_id: str):
    """读取某条主张的证据列表。"""
    store = _require_store()
    auth = _get_auth()
    try:
        evidence = await store.get_evidence(claim_id, auth)
        return {"claim_id": claim_id, "evidence": evidence}
    except Exception as exc:
        logger.error("get_evidence 失败: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/claims/{claim_id}/revisions")
async def get_claim_revisions(claim_id: str):
    """读取某条主张的修订历史。"""
    store = _require_store()
    auth = _get_auth()
    try:
        revisions = await store.get_revisions(claim_id, auth)
        result = []
        for r in revisions:
            d = dataclasses.asdict(r) if dataclasses.is_dataclass(r) and not isinstance(r, type) else dict(r)
            for k, v in d.items():
                if hasattr(v, "value"):
                    d[k] = v.value
            result.append(d)
        return {"claim_id": claim_id, "revisions": result}
    except Exception as exc:
        logger.error("get_revisions 失败: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/claims/{claim_id}/neighbors")
async def get_claim_neighbors(claim_id: str):
    """读取某条主张的关系邻居（来自 identity_relations 表）。"""
    store = _require_store()
    auth = _get_auth()
    try:
        neighbors = await store.get_neighbors(claim_id, auth)
        return {"claim_id": claim_id, "neighbors": neighbors}
    except Exception as exc:
        logger.error("get_neighbors 失败: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/retrieval-traces")
async def list_retrieval_traces():
    """列出所有检索追踪记录（诊断用）。"""
    store = _require_store()
    try:
        traces = await store._list_retrieval_traces()
        return {"traces": traces}
    except Exception as exc:
        logger.error("list_retrieval_traces 失败: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/audit-log")
async def list_audit_log(limit: int = 100):
    """读取审计日志（管理员操作，返回最新 N 条）。"""
    store = _require_store()
    _get_auth()  # 仅验证 kevin 权限（桌面端已是 OWNER）
    try:
        if store._db is None:
            raise HTTPException(status_code=503, detail="Store not connected")
        cursor = await store._db.execute(
            "SELECT * FROM identity_audit_log ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return {"entries": [dict(r) for r in rows]}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("list_audit_log 失败: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# 写路由
# ---------------------------------------------------------------------------

@router.post("/rebuild")
async def trigger_rebuild(body: RebuildRequest):
    """从 Markdown 触发完整身份主张重建。需要 confirm=true。"""
    if not body.confirm:
        raise HTTPException(status_code=400, detail="需要 confirm=true 才能执行重建")
    store = _require_store()
    auth = _get_auth()
    try:
        from config.settings import DATA_DIR
        from src.identity.parser import IdentityParser

        parser = IdentityParser(
            store=store,
            identity_dir=Path(DATA_DIR) / "identity",
        )
        report = await parser.rebuild(auth)
        # RebuildReport 可能是数据类或命名元组
        if dataclasses.is_dataclass(report) and not isinstance(report, type):
            result = dataclasses.asdict(report)
        else:
            result = vars(report) if hasattr(report, "__dict__") else str(report)
        return {"status": "ok", "report": result}
    except Exception as exc:
        logger.error("rebuild 失败: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/killswitch")
async def toggle_killswitch(body: KillswitchRequest):
    """切换身份子系统全局主开关（killswitch）。"""
    if _flags is None:
        raise HTTPException(status_code=503, detail="Identity flags not initialized")
    try:
        # IdentityFlags 是 frozen=False 的 dataclass，可直接赋值
        _flags.identity_system_killswitch = body.enabled
        logger.info("identity_system_killswitch 设置为 %s", body.enabled)
        return {"killswitch": body.enabled, "flags": _flags.current()}
    except Exception as exc:
        logger.error("killswitch 切换失败: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# 隐私路由
# ---------------------------------------------------------------------------

@router.post("/claims/{claim_id}/export")
async def export_claim(claim_id: str):
    """导出指定主张及其所有修订记录。"""
    store = _require_store()
    auth = _get_auth()
    try:
        data = await store.export_claim(claim_id, auth)
        return data
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("export_claim 失败: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/claims/{claim_id}/redact")
async def redact_claim(claim_id: str, body: RedactRequest):
    """抹除主张文本（隐私保护）。若主张有未处理的源文件跨度，返回 409。"""
    store = _require_store()
    auth = _get_auth()
    try:
        result = await store.redact_claim(
            claim_id,
            auth,
            body.reason,
            source_already_redacted=body.source_already_redacted,
        )
        if not result.success and result.requires_source_redaction:
            raise HTTPException(
                status_code=409,
                detail="主张存在 markdown_span 证据，请先抹除源文件后再操作，"
                       "或传入 source_already_redacted=true",
            )
        return {"claim_id": claim_id, "success": result.success}
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("redact_claim 失败: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/claims/{claim_id}/erase")
async def erase_claim(claim_id: str, body: EraseRequest):
    """完全擦除主张（Addendum P1.1 tombstone 语义）。"""
    store = _require_store()
    auth = _get_auth()
    try:
        await store.erase_claim(claim_id, auth, body.reason)
        return {"claim_id": claim_id, "erased": True}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("erase_claim 失败: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# 健康 / 统计 / Outbox 路由
# ---------------------------------------------------------------------------

@router.get("/health")
async def health():
    """身份子系统基础健康检查。"""
    store_ok = _store is not None
    flags_status = _flags.current() if _flags is not None else None
    return {
        "status": "ok" if store_ok else "degraded",
        "store_initialized": store_ok,
        "flags": flags_status,
    }


@router.get("/stats")
async def stats():
    """返回主张总数及修订总数（简单统计）。"""
    store = _require_store()
    auth = _get_auth()
    try:
        claims = await store.list_claims(auth)
        # 统计各状态数量
        status_counts: dict[str, int] = {}
        for c in claims:
            s = c.status if isinstance(c.status, str) else c.status.value
            status_counts[s] = status_counts.get(s, 0) + 1

        # 修订总数（直接查 DB）
        revision_count = 0
        if store._db is not None:
            cursor = await store._db.execute(
                "SELECT COUNT(*) FROM identity_revisions"
            )
            row = await cursor.fetchone()
            revision_count = row[0] if row else 0

        return {
            "claim_count": len(claims),
            "status_counts": status_counts,
            "revision_count": revision_count,
        }
    except Exception as exc:
        logger.error("stats 失败: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/outbox-status")
async def outbox_status():
    """返回 identity_index_outbox 队列中待处理条目数量。"""
    store = _require_store()
    try:
        pending = await store._get_pending_outbox()
        return {"pending_count": len(pending), "entries": pending}
    except Exception as exc:
        logger.error("outbox_status 失败: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
