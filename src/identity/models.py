from __future__ import annotations

# 身份基底数据模型 — 枚举、数据类、ID 计算函数
# Identity substrate data models — enums, dataclasses, ID computation functions

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


# ---------------------------------------------------------------------------
# 类型别名
# ---------------------------------------------------------------------------

ActorType = Literal["kevin", "lapwing", "system"]


# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------

class ClaimType(str, Enum):
    """身份主张的类型，共 7 种"""
    BELIEF = "belief"
    PREFERENCE = "preference"
    TRAIT = "trait"
    VALUE = "value"
    MEMORY_ANCHOR = "memory_anchor"
    RELATIONSHIP = "relationship"
    SKILL_CLAIM = "skill_claim"


class ClaimOwner(str, Enum):
    """主张的归属方"""
    LAPWING = "lapwing"
    KEVIN = "kevin"
    SYSTEM = "system"
    SHARED = "shared"


class ClaimStatus(str, Enum):
    """主张的生命周期状态"""
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    CONTESTED = "contested"
    MERGED = "merged"
    REDACTED = "redacted"
    ERASED = "erased"


class Sensitivity(str, Enum):
    """主张的敏感度级别"""
    PUBLIC = "public"
    PRIVATE = "private"
    RESTRICTED = "restricted"


class RevisionAction(str, Enum):
    """修订日志中的操作类型"""
    CREATED = "created"
    UPDATED = "updated"
    DEPRECATED = "deprecated"
    CONTESTED = "contested"
    MERGED = "merged"
    REDACTED = "redacted"
    ERASED = "erased"
    RECLASSIFIED = "reclassified"


class GateOutcome(str, Enum):
    """门控检查的结果"""
    PASSED = "passed"
    BLOCKED = "blocked"
    DEFERRED = "deferred"
    REQUIRES_REVIEW = "requires_review"


class GatePassReason(str, Enum):
    """门控通过的原因（含 Addendum P0.5 两个 bypass 原因）"""
    NORMAL = "normal"
    COMPONENT_DISABLED = "component_disabled"   # Addendum P0.5
    KILLSWITCH_ON = "killswitch_on"             # Addendum P0.5


class GateLevel(str, Enum):
    """门控级别"""
    NONE = "none"
    LOG = "log"
    WARN = "warn"
    BLOCK = "block"


class ConflictType(str, Enum):
    """两条主张之间的冲突类型"""
    CONTRADICTS = "contradicts"
    SOFTENS = "softens"
    STRENGTHENS = "strengthens"
    DUPLICATES = "duplicates"


class AuditAction(str, Enum):
    """审计日志条目的操作类型"""
    CLAIM_CREATED = "claim_created"
    CLAIM_UPDATED = "claim_updated"
    CLAIM_DEPRECATED = "claim_deprecated"
    CLAIM_REDACTED = "claim_redacted"
    CLAIM_ERASED = "claim_erased"
    MANUAL_OVERRIDE = "manual_override"
    GATE_DECISION = "gate_decision"
    REBUILD_STARTED = "rebuild_started"
    REBUILD_COMPLETED = "rebuild_completed"


class ContextProfile(str, Enum):
    """对话上下文画像，用于检索权重调整"""
    REFLECTIVE = "reflective"
    SOCIAL = "social"
    TASK = "task"
    EMOTIONAL = "emotional"
    CREATIVE = "creative"


# ---------------------------------------------------------------------------
# ID 计算函数
# ---------------------------------------------------------------------------

def compute_raw_block_id(normalized_file: str, stable_block_key: str) -> str:
    """
    根据文件名和块键计算原始块 ID（SHA-256 前 16 个十六进制字符）。
    Compute raw block ID: SHA-256 of "{normalized_file}::{stable_block_key}", first 16 hex chars.
    """
    payload = f"{normalized_file}::{stable_block_key}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def compute_claim_id(raw_block_id: str, claim_local_key: str) -> str:
    """
    根据原始块 ID 和局部键计算主张 ID（SHA-256 前 16 个十六进制字符）。
    Compute claim ID: SHA-256 of "{raw_block_id}::{claim_local_key}", first 16 hex chars.
    """
    payload = f"{raw_block_id}::{claim_local_key}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def compute_claim_id_from_key(
    source_file: str,
    stable_block_key: str,
    claim_local_key: str = "claim_0",
) -> str:
    """
    便捷包装：直接从文件名 + 块键 + 局部键计算主张 ID。
    Convenience wrapper: compute claim ID directly from file + block key + local key.
    """
    raw_block_id = compute_raw_block_id(source_file, stable_block_key)
    return compute_claim_id(raw_block_id, claim_local_key)


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class IdentityClaim:
    """
    一条身份主张。溯源信息（字节偏移、解析时 SHA）独立存储于 ClaimSourceMapping。
    A single identity claim. Provenance (byte offsets, SHA at parse) lives in ClaimSourceMapping.
    """
    claim_id: str
    raw_block_id: str
    claim_local_key: str
    source_file: str
    stable_block_key: str
    claim_type: ClaimType
    owner: ClaimOwner
    predicate: str
    object_val: str
    confidence: float              # 0.0–1.0
    sensitivity: Sensitivity
    status: ClaimStatus
    tags: list[str]
    evidence_ids: list[str]
    created_at: str                # ISO datetime string
    updated_at: str                # ISO datetime string


@dataclass
class ClaimRevision:
    """主张的修订记录"""
    revision_id: str
    claim_id: str
    action: RevisionAction
    old_snapshot: dict | None
    new_snapshot: dict
    actor: ActorType
    reason: str
    created_at: str                # ISO datetime string


@dataclass
class GateEvent:
    """门控检查事件记录"""
    event_id: str
    claim_id: str
    outcome: GateOutcome
    pass_reason: GatePassReason | None
    gate_level: GateLevel
    context_profile: ContextProfile | None
    signals: dict
    created_at: str                # ISO datetime string


@dataclass
class ConflictEvent:
    """两条主张之间的冲突事件"""
    event_id: str
    claim_id_a: str
    claim_id_b: str
    conflict_type: ConflictType
    resolution: str | None
    resolved: bool
    created_at: str                # ISO datetime string


@dataclass
class RetrievalTrace:
    """检索过程追踪"""
    trace_id: str
    query: str
    context_profile: ContextProfile | None
    candidate_ids: list[str]
    selected_ids: list[str]
    redacted_ids: list[str]
    latency_ms: float
    created_at: str                # ISO datetime string


@dataclass
class InjectionTrace:
    """注入过程追踪（将主张注入 prompt）"""
    trace_id: str
    retrieval_trace_id: str
    claim_ids: list[str]
    token_count: int
    budget_total: int
    created_at: str                # ISO datetime string


@dataclass
class AuditLogEntry:
    """审计日志条目"""
    entry_id: str
    action: AuditAction
    claim_id: str | None
    actor: ActorType
    details: dict
    created_at: str                # ISO datetime string


@dataclass
class OverrideToken:
    """手动覆盖令牌，允许绕过门控"""
    token_id: str
    claim_id: str
    issuer: ActorType
    reason: str
    action_payload_hash: str | None
    expires_at: str | None
    created_at: str                # ISO datetime string


@dataclass
class ClaimEvidence:
    """主张的支撑证据条目"""
    evidence_id: str
    claim_id: str
    evidence_type: str
    content: str
    source: str | None
    created_at: str                # ISO datetime string


@dataclass
class ClaimSourceMapping:
    """
    主张的溯源映射（Addendum P0.3）。
    存储字节偏移和解析时的文件 SHA，独立于 IdentityClaim 主表。
    Source provenance for a claim (Addendum P0.3).
    Stores byte offsets and file SHA at parse time, separate from the main claims table.
    """
    claim_id: str
    source_file: str
    byte_offset_start: int
    byte_offset_end: int
    sha256_at_parse: str


@dataclass
class InjectionDecision:
    """单条主张的注入决策"""
    claim_id: str
    included: bool
    reason: str


@dataclass
class ContextSignals:
    """当前对话上下文信号，用于检索和注入决策"""
    mood: str | None
    topic: str | None
    conversation_depth: int
    time_of_day: str | None
    custom: dict = field(default_factory=dict)
