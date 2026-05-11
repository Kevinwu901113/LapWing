# Lapwing v1 Blueprint: Resident Agent Kernel Implementation

> **版本** v1.1 final — Signed off, ready for Claude Code execution
> **日期** 2026-05-11
> **基于** `lapwing_redesign_intent_v0.2.md` (signed off 2026-05-11)
> **执行方** Claude Code
> **状态** ✓ Kevin · ✓ GPT · ✓ Claude — three-way signed off 2026-05-11
> **实施计划** 由 Claude Code 自行 plan(blueprint 提供 Slice specs + 依赖图约束,不预设排程 / 工期 / PR 顺序)

---

## 0. 这份文档的定位

这是 **实施蓝图**,不是 intent。它把 v0.2 的宪法落到:

- 完整 schema 与 dataclass 定义
- API 签名与调用契约
- 文件路径与目录结构
- Acceptance test 用例(完整可执行,不只是描述)
- Slice 间硬依赖图(设计约束,不预设排程 / 工期 / PR 顺序)
- v0.1 → v0.2 数据迁移约束
- 所有 open question 的最终答案

**它不是 plan。** 具体如何切 PR、何时启动哪个 Slice、谁先谁后、几人并行 —— 由 Claude Code 阅读完 blueprint 后**自行规划**。Blueprint 只规定"必须做什么 + 必须满足什么约束",不规定"何时何序怎么做"。

任何 blueprint 实施过程中发现的 v0.2 边界冲突,**升级 v0.2 → v0.3,不在 blueprint 里 silent drift**。

任何 blueprint 内部细节争议,在 blueprint 内升版本(v1.1, v1.2),不污染 intent。

---

## 1. 实施范围

v1 = 跑通这条 **生命闭环**(v0.2 §14):

```text
Kevin 消息
  → cognition (Brain / TaskRuntime)
    → delegate_to_agent(kind, task, constraints)
      → agent worker
        → kernel.execute(Action) on Resource          [Kernel action pipeline]
          → Adapter 调用 (browser / credential / ...)
            ├─ 顺利 → Observation(status=ok)
            └─ 边界 → Interrupt(continuation_ref)
                       → Kevin resolve via Desktop /interrupts API
                         → kernel resume continuation
                           → Observation(status=ok | interrupted)
        → agent worker 综合
      → delegate result 回 cognition
    → 回应 Kevin
  → 全程写 EventLog
```

每个 v1 模块必须服务此闭环;不服务的功能 → §14 defer 集。

**v1 不做(v0.2 §13.3):**
AccountRegistry / Email Gateway / SMS Gateway / ResidentEpisodicMemory / Wiki 蒸馏管线 / 完整 CapabilityPolicy / 6 级 ActionRisk / Identity Evolution / Capability Evolution / ShellPolicy / FilesystemPolicy / ResidentWorkspace / Desktop v2 Linux-Windows 完整化。

---

## 2. 仓库目录布局

### 2.1 新增目录

```text
src/lapwing_kernel/
├── __init__.py
├── kernel.py                  ≤150 行,composition root
├── identity.py                ResidentIdentity dataclass
├── primitives/
│   ├── __init__.py
│   ├── action.py              Action dataclass
│   ├── observation.py         Observation dataclass + ObservationStatus
│   ├── interrupt.py           Interrupt dataclass + InterruptStatus + InterruptKind
│   ├── event.py               Event dataclass
│   └── resource.py            Resource Protocol + ResourceRef
├── pipeline/
│   ├── __init__.py
│   ├── executor.py            ActionExecutor (pipeline 实现)
│   └── registry.py            ResourceRegistry
├── policy.py                  policy.decide() 函数
├── redaction.py               SecretRedactor
├── model_slots.py             ModelSlotResolver + tier-list
├── stores/
│   ├── __init__.py
│   ├── interrupt_store.py     InterruptStore (SQLite)
│   └── event_log.py           EventLog (SQLite)
└── adapters/
    ├── __init__.py
    ├── browser.py             BrowserAdapter
    └── credential.py          CredentialAdapter

src/agents/                    (现有目录,扩展)
├── catalog.py                 (现有)
├── runtime.py                 (现有)
├── delegate.py                (新建) unified delegate_to_agent
└── workers/
    └── resident_operator.py   (新建) resident operator agent kind

docs/architecture/
├── lapwing_redesign_intent_v0.2.md   (signed off)
└── lapwing_v1_blueprint.md           (本文档)

docs/archive/
├── lapwing_redesign_intent_v0.1.md
├── identity_substrate_ticket_b.md         (archived)
├── capability_evolution_8phase.md         (archived)
└── task_learning_design.md                (archived)
```

### 2.2 降级 / 重组

```text
src/core/browser_manager.py
  → 不删,但作为 BrowserAdapter(profile="fetch") 的 legacy backend
  → 不再被 LLM tool 直接调用,不再 inject 给 sub-agent (统一走 Kernel pipeline)

src/core/browser_guard.py
  → 简化为两个 policy decision 函数
  → 不抽 CapabilityPolicy 基类
  → 被 kernel.policy 调用

src/core/credential_vault.py
  → 保留,继续作为 secret 底层 fact store
  → 不再被 tool 直接调用,统一通过 CredentialAdapter

src/tools/                     (现有目录,大规模削减)
  → 主面 ToolSpec 缩减到 §12 清单
  → browser_*, credential_* 等 raw tool 全部移除
  → reminder/promise/focus/correction 合并到 read_state / update_state
```

### 2.3 删除 / 拔 wiring

```text
src/capabilities/                              # Level 2+3: 删 active wiring/config,代码暂留
src/identity/substrate_ticket_b/   (若存在)    # Level 1+2: archive blueprint, 删 config
src/learning/task_learning/        (若存在)    # Level 1: archive
src/memory/wiki/pipeline/auto_write_*.py       # 拔 active wiring,write_enabled 强制 false
src/integrations/mirofish/         (若存在)    # Level 4: 删
```

### 2.4 完整 file change manifest

见 §15 各 Slice 内部。

---

## 3. Primitive Schemas

### 3.1 Action

```python
# src/lapwing_kernel/primitives/action.py

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
import uuid

@dataclass(frozen=True)
class Action:
    """
    对 Resource 的一次调用意图。

    Action 是数据,不是函数。所有 Action 必须通过 Kernel action pipeline 执行
    (ActionExecutor.execute),任何越过 pipeline 直接调 adapter 内部方法的代码
    = 协议违反。

    resource_profile 是 routing identity 的一部分,与 args(业务参数)分离。
    ResourceRegistry 用 (resource, resource_profile) 作为 lookup key。
    """
    id: str
    resource: str                       # browser / credential / ...
    resource_profile: str | None = None # fetch / personal / operator / None
    verb: str = ""                      # navigate / read / login / ...
    args: dict[str, Any] = field(default_factory=dict)
    actor: str = "lapwing"              # lapwing / owner / system / agent
    task_ref: str | None = None
    parent_action_id: str | None = None # 用于 continuation 链

    @staticmethod
    def new(resource: str, verb: str, *,
            resource_profile: str | None = None,
            args: dict[str, Any] | None = None,
            actor: str = "lapwing", task_ref: str | None = None,
            parent_action_id: str | None = None) -> "Action":
        return Action(
            id=str(uuid.uuid4()),
            resource=resource,
            resource_profile=resource_profile,
            verb=verb,
            args=args or {},
            actor=actor,
            task_ref=task_ref,
            parent_action_id=parent_action_id,
        )
```

### 3.2 Observation

```python
# src/lapwing_kernel/primitives/observation.py

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
from datetime import datetime

# 通用 status —— 每 resource 在此基础上扩展自己的 status 字符串
COMMON_STATUS = frozenset({
    "ok",
    "blocked",
    "blocked_by_policy",
    "interrupted",
    "failed",
    "timeout",
    "network_error",
    "empty_content",
})

@dataclass(frozen=True)
class Observation:
    """
    Action 执行的统一结果 envelope。

    所有 Resource 共享同一 envelope。Browser/Credential/Shell 不造独立 Result。

    content 是 LLM-facing 字段,视为对模型公开。
    artifacts 不自动 LLM-facing,每个 artifact 类型需显式 renderer 才能进 LLM。
    """
    id: str
    action_id: str
    resource: str
    status: str                      # 见 COMMON_STATUS + 各 resource 扩展
    summary: str | None = None       # 短人类可读
    content: str | None = None       # LLM-facing 文本,已 redact
    confidence: float | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    interrupt_id: str | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)

    @staticmethod
    def ok(action_id: str, resource: str, *, summary: str | None = None,
           content: str | None = None, artifacts: list[dict] | None = None,
           confidence: float | None = None,
           provenance: dict | None = None) -> "Observation":
        import uuid
        return Observation(
            id=str(uuid.uuid4()),
            action_id=action_id,
            resource=resource,
            status="ok",
            summary=summary,
            content=content,
            confidence=confidence,
            provenance=provenance or {},
            artifacts=artifacts or [],
        )

    @staticmethod
    def interrupted(action_id: str, resource: str, *, interrupt_id: str,
                    summary: str) -> "Observation":
        import uuid
        return Observation(
            id=str(uuid.uuid4()),
            action_id=action_id,
            resource=resource,
            status="interrupted",
            summary=summary,
            interrupt_id=interrupt_id,
        )

    @staticmethod
    def failure(action_id: str, resource: str, *, status: str,
                error: str, summary: str | None = None) -> "Observation":
        import uuid
        return Observation(
            id=str(uuid.uuid4()),
            action_id=action_id,
            resource=resource,
            status=status,
            error=error,
            summary=summary,
        )


# Browser 在 status 上扩展
BROWSER_EXTRA_STATUS = frozenset({
    "waf_challenge",
    "captcha_required",
    "auth_required",
    "user_attention_required",
})

# Credential 扩展
CREDENTIAL_EXTRA_STATUS = frozenset({
    "missing",         # service 不存在凭据
    "requires_owner",  # 需要 Kevin 介入
})

# 注:status 不用 Enum,用 str + 校验函数,允许未来 resource 扩展不改 schema
def validate_status(resource: str, status: str) -> bool:
    if status in COMMON_STATUS:
        return True
    if resource == "browser" and status in BROWSER_EXTRA_STATUS:
        return True
    if resource == "credential" and status in CREDENTIAL_EXTRA_STATUS:
        return True
    return False
```

### 3.3 Interrupt

```python
# src/lapwing_kernel/primitives/interrupt.py

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
from datetime import datetime, timedelta
import uuid

# Status 状态机
INTERRUPT_STATUS = frozenset({
    "pending", "resolved", "denied", "expired", "cancelled",
})

# Kind 命名空间(string,不用 enum,允许扩展)
# 已知 kinds:
#   browser.captcha          — Cloudflare / hCaptcha / reCAPTCHA
#   browser.login_required   — 需要登录态
#   browser.auth_2fa         — 2FA 验证
#   browser.waf              — WAF 拦截需 takeover
#   shell.sudo               — sudo 操作 (v1 不实施,预留 kind 命名)
#   email.send_confirm       — 外发邮件确认 (v1 不实施)
#   payment.confirm          — 付款确认 (v1 不实施)

@dataclass(frozen=True)
class Interrupt:
    """
    执行中需要外部介入的状态。

    Continuation-first: 由 in-progress action 产生的 Interrupt 必须携带
    continuation_ref,或显式 non_resumable=true。无 continuation_ref 的 Interrupt
    只是通知,不构成可 resume 执行。
    """
    id: str
    kind: str                            # browser.captcha / browser.login_required / ...
    status: str                          # pending / resolved / denied / expired / cancelled
    actor_required: str                  # owner / system / 其他
    resource: str
    resource_ref: str | None             # 如 browser tab_id / session_id
    continuation_ref: str | None         # 没有此字段 → 不可 resume,只是通知
    non_resumable: bool = False
    non_resumable_reason: str | None = None
    summary: str = ""
    payload_redacted: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: datetime | None = None
    updated_at: datetime = field(default_factory=datetime.utcnow)
    resolved_payload: dict[str, Any] | None = None  # owner resolve 时携带

    @staticmethod
    def new(kind: str, actor_required: str, resource: str, *,
            resource_ref: str | None = None,
            continuation_ref: str | None = None,
            non_resumable: bool = False,
            non_resumable_reason: str | None = None,
            summary: str = "",
            payload_redacted: dict | None = None,
            expires_in: timedelta | None = None) -> "Interrupt":
        # 硬规则: 必须要么有 continuation_ref,要么 non_resumable=True
        if continuation_ref is None and not non_resumable:
            raise ValueError(
                "Interrupt must have continuation_ref OR non_resumable=True; "
                "see v0.2 §8.1"
            )
        now = datetime.utcnow()
        return Interrupt(
            id=str(uuid.uuid4()),
            kind=kind,
            status="pending",
            actor_required=actor_required,
            resource=resource,
            resource_ref=resource_ref,
            continuation_ref=continuation_ref,
            non_resumable=non_resumable,
            non_resumable_reason=non_resumable_reason,
            summary=summary,
            payload_redacted=payload_redacted or {},
            created_at=now,
            expires_at=(now + expires_in) if expires_in else None,
            updated_at=now,
        )


# 默认 expires_at 策略 (open question O-2 答案):
# - browser.captcha / browser.login_required / browser.auth_2fa  → 24 小时
# - browser.waf                                                  → 24 小时
# - 其他未指定 → 无过期 (None),靠 owner cancel
DEFAULT_INTERRUPT_EXPIRY = {
    "browser.captcha": timedelta(hours=24),
    "browser.login_required": timedelta(hours=24),
    "browser.auth_2fa": timedelta(hours=24),
    "browser.waf": timedelta(hours=24),
}
```

### 3.4 Event

```python
# src/lapwing_kernel/primitives/event.py

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
from datetime import datetime
import uuid

# Type 命名空间 (string, 不用 enum)
# 已知 types:
#   browser.navigate / browser.challenge / browser.click / browser.text_input
#   credential.created / credential.used / credential.requires_owner
#   interrupt.created / interrupt.resolved / interrupt.denied / interrupt.expired
#   policy.blocked / policy.allowed / policy.escalated
#   model.fallback / model.timeout
#   agent.delegated / agent.completed / agent.failed
#   memory.read

@dataclass(frozen=True)
class Event:
    """
    Append-only operational history.

    EventLog 不是 LLM memory:
    - 不默认注入 prompt
    - 通过显式查询接口供 sub-agent 检索
    - v1 不做自动蒸馏 / Wiki 写入 / episodic 抽取
    """
    id: str
    time: datetime
    actor: str
    type: str                       # string namespace
    resource: str | None
    summary: str
    outcome: str | None             # ok / blocked / interrupted / failed / ...
    refs: dict[str, str] = field(default_factory=dict)         # action_id / interrupt_id / task_ref
    data_redacted: dict[str, Any] = field(default_factory=dict)  # 已 redact, 不含 secret

    @staticmethod
    def new(actor: str, type: str, summary: str, *,
            resource: str | None = None,
            outcome: str | None = None,
            refs: dict[str, str] | None = None,
            data_redacted: dict[str, Any] | None = None) -> "Event":
        return Event(
            id=str(uuid.uuid4()),
            time=datetime.utcnow(),
            actor=actor,
            type=type,
            resource=resource,
            summary=summary,
            outcome=outcome,
            refs=refs or {},
            data_redacted=data_redacted or {},
        )
```

### 3.5 Resource Protocol

```python
# src/lapwing_kernel/primitives/resource.py

from __future__ import annotations
from typing import Protocol, runtime_checkable
from .action import Action
from .observation import Observation


@runtime_checkable
class Resource(Protocol):
    """
    Resource is anything that produces side-effects or talks to the outside world.

    NOT a Resource:
      - TrajectoryStore (fact source)
      - Wiki (fact source)
      - EventLog (fact source)
      - CredentialVault (底层 secret store, 不直接被调用)

    Adapter pattern:
      class BrowserAdapter:
          name: ClassVar[str] = "browser"
          ...
          async def execute(self, action: Action) -> Observation: ...
    """
    name: str   # "browser" / "credential" / ...

    async def execute(self, action: Action) -> Observation: ...

    def supports(self, verb: str) -> bool: ...


# Resource Protocol 不强制 Profile,profile 是 adapter 实例的构造参数
# BrowserAdapter(profile="fetch") 和 BrowserAdapter(profile="personal") 是同一 name
# ResourceRegistry 用 (name, profile) 作为 key
```

### 3.6 ResidentIdentity

```python
# src/lapwing_kernel/identity.py

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ResidentIdentity:
    """
    Lapwing 在服务器上的身份事实。

    不是 "她的对象表示";只是 kernel 启动时读取的不可变身份事实。
    """
    agent_name: str               # "Lapwing"
    owner_name: str               # "Kevin"
    home_server_name: str         # e.g. "pve-home-01"
    linux_user: str               # OS-level user (运行时检查,不要假设固定)
    home_dir: Path
    personal_browser_profile: Path
    email_address: str | None = None   # v1 None;有了再填
    phone_number_ref: str | None = None  # v1 None
```

---

## 4. Kernel Composition Root

### 4.1 kernel.py 责任与禁区

`kernel.py` ≤150 行,只能做:

- composition root(组装 dependencies)
- ResourceRegistry 注册
- store 初始化(InterruptStore / EventLog)
- policy 注入
- redactor 注入
- ActionExecutor pipeline 装配

**禁止出现:**

- browser 业务逻辑
- credential 业务逻辑
- resume 业务逻辑
- redaction 实现
- agent dispatch 逻辑
- model fallback 实现

```python
# src/lapwing_kernel/kernel.py

from __future__ import annotations
from typing import Any
from pathlib import Path

from .identity import ResidentIdentity
from .pipeline.executor import ActionExecutor
from .pipeline.registry import ResourceRegistry
from .policy import PolicyDecider
from .redaction import SecretRedactor
from .stores.interrupt_store import InterruptStore
from .stores.event_log import EventLog
from .model_slots import ModelSlotResolver


class Kernel:
    """
    Composition root only. No business logic.

    Kernel is NOT Lapwing. Kernel is the OS-like substrate Lapwing runs on.
    If this class exceeds ~150 lines or contains business logic, the design has failed.

    See v0.2 §4.
    """

    def __init__(
        self,
        identity: ResidentIdentity,
        resource_registry: ResourceRegistry,
        interrupt_store: InterruptStore,
        event_log: EventLog,
        policy: PolicyDecider,
        redactor: SecretRedactor,
        model_slots: ModelSlotResolver,
    ):
        self.identity = identity
        self.resources = resource_registry
        self.interrupts = interrupt_store
        self.events = event_log
        self.policy = policy
        self.redactor = redactor
        self.model_slots = model_slots

        # ActionExecutor is the pipeline; kernel does NOT execute actions itself
        self.executor = ActionExecutor(
            resource_registry=resource_registry,
            interrupt_store=interrupt_store,
            event_log=event_log,
            policy=policy,
            redactor=redactor,
        )

    async def execute(self, action):  # 仅 thin facade, 委托给 executor
        return await self.executor.execute(action)

    async def resume(self, interrupt_id: str, owner_payload: dict) -> dict:
        """
        Thin facade. Returns small status dict, NOT an Observation.
        See ActionExecutor.resume for ordering rules.
        """
        return await self.executor.resume(interrupt_id, owner_payload)


def build_kernel(config: dict[str, Any]) -> Kernel:
    """
    Composition root factory.

    Reads config, constructs all dependencies, returns wired Kernel.
    All actual logic lives in the constructed objects, not here.
    """
    identity = _load_identity(config["identity"])
    redactor = SecretRedactor(config["redaction"])
    interrupt_store = InterruptStore(Path(config["paths"]["sqlite"]))
    event_log = EventLog(Path(config["paths"]["sqlite"]))
    model_slots = ModelSlotResolver.from_config(config["model_slots"])
    policy = PolicyDecider(config["policy"])

    registry = ResourceRegistry()
    _register_default_resources(registry, config, model_slots, redactor)

    return Kernel(
        identity=identity,
        resource_registry=registry,
        interrupt_store=interrupt_store,
        event_log=event_log,
        policy=policy,
        redactor=redactor,
        model_slots=model_slots,
    )


def _load_identity(cfg: dict) -> ResidentIdentity: ...
def _register_default_resources(registry, config, model_slots, redactor): ...
```

**实施验收:** `wc -l src/lapwing_kernel/kernel.py` 必须 ≤150。

### 4.2 ResourceRegistry

```python
# src/lapwing_kernel/pipeline/registry.py

from __future__ import annotations
from typing import Any
from ..primitives.resource import Resource


class ResourceRegistry:
    """
    (name, profile) -> Resource instance.

    BrowserAdapter(profile="fetch") and BrowserAdapter(profile="personal") share
    the same name="browser" but are different keys.
    """

    def __init__(self):
        self._resources: dict[tuple[str, str | None], Resource] = {}

    def register(self, resource: Resource, *, profile: str | None = None) -> None:
        key = (resource.name, profile)
        if key in self._resources:
            raise ValueError(f"Resource {key} already registered")
        self._resources[key] = resource

    def get(self, name: str, profile: str | None = None) -> Resource:
        key = (name, profile)
        if key not in self._resources:
            raise KeyError(f"Resource {key} not registered")
        return self._resources[key]

    def list_names(self) -> list[str]:
        return sorted({name for (name, _) in self._resources.keys()})
```

### 4.3 ActionExecutor (pipeline)

```python
# src/lapwing_kernel/pipeline/executor.py

from __future__ import annotations
from ..primitives.action import Action
from ..primitives.observation import Observation
from ..primitives.event import Event
from ..policy import PolicyDecider, PolicyDecision
from ..redaction import SecretRedactor
from ..stores.interrupt_store import InterruptStore
from ..stores.event_log import EventLog
from .registry import ResourceRegistry


class ActionExecutor:
    """
    The Kernel action pipeline.

    All resource actions must enter through this pipeline. No caller may bypass
    and call adapter internals directly. kernel.py only wires this; business
    logic lives here.
    """

    def __init__(
        self,
        resource_registry: ResourceRegistry,
        interrupt_store: InterruptStore,
        event_log: EventLog,
        policy: PolicyDecider,
        redactor: SecretRedactor,
    ):
        self._registry = resource_registry
        self._interrupts = interrupt_store
        self._events = event_log
        self._policy = policy
        self._redactor = redactor

    async def execute(self, action: Action) -> Observation:
        # 1. Policy decision
        decision = self._policy.decide(action)
        if decision == PolicyDecision.BLOCK:
            self._events.append(Event.new(
                actor=action.actor, type="policy.blocked",
                resource=action.resource,
                summary=f"{action.resource}.{action.verb} blocked by policy",
                refs={"action_id": action.id},
            ))
            return Observation.failure(
                action.id, action.resource,
                status="blocked_by_policy",
                error="policy.block",
                summary=f"{action.resource}.{action.verb} blocked",
            )

        if decision == PolicyDecision.INTERRUPT:
            # Policy 主动要求外部介入(如 sudo)
            interrupt = self._interrupts.create_from_policy(action)
            return Observation.interrupted(
                action.id, action.resource,
                interrupt_id=interrupt.id,
                summary=f"policy interrupt for {action.resource}.{action.verb}",
            )

        # 2. Resolve resource (profile 来自 Action 顶层字段, 不混在 args 里)
        resource = self._registry.get(
            action.resource, profile=action.resource_profile
        )

        if not resource.supports(action.verb):
            return Observation.failure(
                action.id, action.resource,
                status="failed",
                error=f"unsupported_verb:{action.verb}",
            )

        # 3. Log action start
        self._events.append(Event.new(
            actor=action.actor, type=f"{action.resource}.{action.verb}",
            resource=action.resource,
            summary=f"executing {action.resource}.{action.verb}",
            refs={"action_id": action.id},
        ))

        # 4. Execute
        try:
            observation = await resource.execute(action)
        except Exception as exc:
            self._events.append(Event.new(
                actor=action.actor, type=f"{action.resource}.failed",
                resource=action.resource,
                summary=str(exc)[:200],
                outcome="failed",
                refs={"action_id": action.id},
            ))
            return Observation.failure(
                action.id, action.resource,
                status="failed",
                error=type(exc).__name__,
                summary=str(exc)[:200],
            )

        # 5. Apply redaction to LLM-facing fields (defense-in-depth)
        observation = self._redactor.redact_observation(observation)

        # 6. Log outcome
        self._events.append(Event.new(
            actor=action.actor, type=f"{action.resource}.{observation.status}",
            resource=action.resource,
            summary=observation.summary or "",
            outcome=observation.status,
            refs={
                "action_id": action.id,
                "observation_id": observation.id,
                **({"interrupt_id": observation.interrupt_id} if observation.interrupt_id else {}),
            },
        ))

        return observation

    async def resume(self, interrupt_id: str, owner_payload: dict) -> dict:
        """
        Owner has resolved an interrupt. Release the suspended continuation
        and return immediately. The final Observation is produced by the
        original agent worker in its own coroutine, NOT awaited here.

        Critical ordering (v1.1 fix per GPT final pass):
          1. Validate interrupt is pending and resumable
          2. Check continuation_ref is still alive in ContinuationRegistry
             (process restart between Interrupt creation and resume = continuation lost)
          3. ONLY if alive → persist resolved + release future
             If lost → mark cancelled with reason, write EventLog, return error
             NEVER mark resolved without an awaiter to wake.

        Returns a small status dict, NOT an Observation. Desktop /approve
        endpoint must not block awaiting the final Observation.
        """
        from .continuation_registry import ContinuationRegistry

        interrupt = self._interrupts.get(interrupt_id)
        if interrupt is None:
            raise KeyError(f"Interrupt {interrupt_id} not found")
        if interrupt.status != "pending":
            raise ValueError(
                f"Interrupt {interrupt_id} is {interrupt.status}, not pending"
            )
        if interrupt.non_resumable or interrupt.continuation_ref is None:
            return {
                "status": "error",
                "interrupt_id": interrupt_id,
                "reason": "non_resumable_interrupt",
            }

        registry = ContinuationRegistry.instance()

        # GPT FINAL-PASS BLOCKING FIX:
        # Check continuation existence BEFORE marking resolved.
        # If process restarted, the future no longer exists; we must not
        # mark interrupt as resolved while no awaiter can wake.
        if not registry.has(interrupt.continuation_ref):
            self._interrupts.cancel(
                interrupt_id,
                reason="continuation_lost_after_restart",
            )
            self._events.append(Event.new(
                actor="system", type="interrupt.continuation_lost",
                resource=interrupt.resource,
                summary=f"continuation {interrupt.continuation_ref} lost; "
                        f"likely kernel restart between interrupt creation and resolve",
                outcome="cancelled",
                refs={"interrupt_id": interrupt.id},
            ))
            return {
                "status": "error",
                "interrupt_id": interrupt_id,
                "reason": "continuation_lost_after_restart",
            }

        # Continuation is alive → safe to resolve + wake
        self._interrupts.resolve(interrupt_id, owner_payload)
        self._events.append(Event.new(
            actor="owner", type="interrupt.resolved",
            resource=interrupt.resource,
            summary=f"owner resolved {interrupt.kind}",
            refs={"interrupt_id": interrupt.id},
        ))
        registry.resume(interrupt.continuation_ref, owner_payload)

        # Fire-and-forget. Original agent worker continues in its own coroutine.
        # Final Observation flows through the normal pipeline once the worker
        # completes; it is NOT awaited here.
        return {
            "status": "resumed",
            "interrupt_id": interrupt_id,
            "continuation_ref": interrupt.continuation_ref,
        }
```

### 4.4 PolicyDecider

```python
# src/lapwing_kernel/policy.py

from __future__ import annotations
from enum import Enum
from typing import Any
from .primitives.action import Action


class PolicyDecision(Enum):
    ALLOW = "allow"
    INTERRUPT = "interrupt"     # 要求外部介入
    BLOCK = "block"             # 直接拒绝


class PolicyDecider:
    """
    Single function-shaped decision point. No class hierarchy until ≥3 concrete
    rule families share behavior (v0.2 §4 / §11.1).

    Rule sources:
      - browser.fetch: URL allowlist / blocklist
      - browser.personal: more permissive (signed-in profile)
      - browser sensitive verbs (login, download): INTERRUPT
      - shell (v1 不实施 ShellAdapter, 此分支预留)
      - credential.use: INTERRUPT if first-time use, ALLOW if previously approved
                        (state tracked in CredentialUseState, NOT config — see §7.4)
      - high-risk verbs (sudo / payment / external_send): INTERRUPT
    """

    def __init__(self, config: dict[str, Any], use_state: "CredentialUseState"):
        self._cfg = config
        self._url_allowlist = set(config.get("browser_fetch", {}).get("url_allowlist", []))
        self._url_blocklist = set(config.get("browser_fetch", {}).get("url_blocklist", []))
        self._use_state = use_state    # GPT non-blocking B: state, not config

    def decide(self, action: Action) -> PolicyDecision:
        # Browser
        if action.resource == "browser":
            return self._decide_browser(action)
        if action.resource == "credential":
            return self._decide_credential(action)
        # Default
        return PolicyDecision.ALLOW

    def _decide_browser(self, action: Action) -> PolicyDecision:
        # GPT non-blocking E: profile from Action top-level field, not args
        profile = action.resource_profile or "fetch"
        verb = action.verb
        url = action.args.get("url", "")

        if profile == "fetch":
            # fetch 模式: URL 白名单 + 黑名单
            if self._url_blocklist and any(b in url for b in self._url_blocklist):
                return PolicyDecision.BLOCK
            # 默认 allow (web 是开放的)
            return PolicyDecision.ALLOW

        if profile == "personal":
            # personal 模式: login/download/form_submit 需要 owner 同意
            if verb in {"login", "download", "form_submit"}:
                return PolicyDecision.INTERRUPT
            return PolicyDecision.ALLOW

        return PolicyDecision.ALLOW

    def _decide_credential(self, action: Action) -> PolicyDecision:
        if action.verb == "use":
            # 首次使用 → INTERRUPT 取得 owner 同意; 后续使用 ALLOW
            # 状态来源: CredentialUseState (sqlite), NOT config
            service = action.args.get("service")
            if service and self._use_state.has_been_used(service):
                return PolicyDecision.ALLOW
            return PolicyDecision.INTERRUPT
        return PolicyDecision.ALLOW
```

---

## 5. P0-Redaction Patch

**独立于 kernel slice。可立即开始,与 Slice A 并行。**

### 5.1 当前必现路径

```text
page DOM input.value
  → InteractiveElement.value
  → InteractiveElement.to_label()
  → PageState.to_llm_text()
  → LLM-visible browser result
```

正常 `browser_login` 路径走完会跳页,但**登录失败 / 2FA / 异常 / 表单回传**场景下,password 已填入未提交,value 进入 LLM 文本。

### 5.2 受影响代码点(本地 grep 确认清单)

```text
src/core/page_state.py            InteractiveElement.to_label() 输出 value 字段
src/core/dom_processor.py         JS 抽取阶段是否过滤 sensitive type
src/tools/browser_tools.py        browser_login 工具,处理失败 / 2FA 路径
src/core/event_bus.py             事件 payload 是否承载 PageState
src/core/mutation_log.py          mutation payload sanitizer
src/desktop/api/browser_routes.py screenshot metadata / annotations
```

**实施期修正(由实施计划记录):上面 §5.2 路径在当前仓库不存在;`InteractiveElement` 类与 JS 抽取逻辑实际位于 `src/core/browser_manager.py`(行 ~94 / 行 ~232)。本节后续 §5.4 改动落到该文件,不改变 schema 或 contract。**

### 5.3 SecretRedactor 实现

```python
# src/lapwing_kernel/redaction.py

from __future__ import annotations
import re
from typing import Any
from .primitives.observation import Observation


# Sensitive selectors (DOM 层)
SENSITIVE_INPUT_TYPES = frozenset({"password"})
SENSITIVE_AUTOCOMPLETE = frozenset({
    "one-time-code", "current-password", "new-password",
})
SENSITIVE_NAME_PATTERNS = re.compile(
    r"(?i)(otp|passcode|password|token|secret|recovery[\W_]?code|api[\W_]?key|"
    r"private[\W_]?key|access[\W_]?token|refresh[\W_]?token)"
)

# Secret-shaped string (兜底,识别 LLM 已能见到但未走 sensitive selector 的 secret)
SECRET_SHAPED_PATTERNS = [
    re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b"),       # base64-ish 长 token
    re.compile(r"\b[a-f0-9]{32,}\b"),                  # hex-ish 长 token
    re.compile(r"\bey[A-Za-z0-9_-]{30,}\.[A-Za-z0-9_-]{30,}\.[A-Za-z0-9_-]{20,}\b"),  # JWT
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),            # OpenAI-style
    re.compile(r"\bxox[abp]-[A-Za-z0-9-]{20,}\b"),     # Slack
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),           # GitHub PAT
]

REDACTED = "[REDACTED]"


class SecretRedactor:
    """
    Two-layer defense:
      Layer 1: JS extraction stage (in DOMProcessor) skips sensitive input values
      Layer 2: Python redact_* methods scrub anything that still leaks
    """

    def __init__(self, config: dict[str, Any]):
        self._extra_patterns = [re.compile(p) for p in config.get("extra_patterns", [])]

    # Layer 2: 兜底
    def redact_text(self, text: str | None) -> str | None:
        if text is None:
            return None
        for pat in SECRET_SHAPED_PATTERNS:
            text = pat.sub(REDACTED, text)
        for pat in self._extra_patterns:
            text = pat.sub(REDACTED, text)
        return text

    def redact_dict(self, d: dict[str, Any] | None) -> dict[str, Any]:
        if not d:
            return {}
        out: dict[str, Any] = {}
        for k, v in d.items():
            if SENSITIVE_NAME_PATTERNS.search(k):
                out[k] = REDACTED
                continue
            if isinstance(v, str):
                out[k] = self.redact_text(v)
            elif isinstance(v, dict):
                out[k] = self.redact_dict(v)
            elif isinstance(v, list):
                out[k] = [self.redact_text(x) if isinstance(x, str) else x for x in v]
            else:
                out[k] = v
        return out

    def redact_observation(self, obs: Observation) -> Observation:
        # Defense-in-depth: even if adapter forgot to redact, content/artifacts get scrubbed here
        from dataclasses import replace
        return replace(
            obs,
            summary=self.redact_text(obs.summary),
            content=self.redact_text(obs.content),
            artifacts=[self.redact_dict(a) for a in obs.artifacts],
            provenance=self.redact_dict(obs.provenance),
        )

    # Layer 1 helper: 用于 DOMProcessor 抽取时判断字段是否敏感
    @staticmethod
    def is_sensitive_input(input_type: str | None, name: str | None,
                           autocomplete: str | None,
                           placeholder: str | None,
                           aria_label: str | None) -> bool:
        if input_type and input_type.lower() in SENSITIVE_INPUT_TYPES:
            return True
        if autocomplete and autocomplete.lower() in SENSITIVE_AUTOCOMPLETE:
            return True
        for s in (name, placeholder, aria_label):
            if s and SENSITIVE_NAME_PATTERNS.search(s):
                return True
        return False
```

### 5.4 受影响代码改动清单

**实施期注:** 下面伪代码引用 `src/core/page_state.py` / `src/core/dom_processor.py`,实仓中等价代码在 `src/core/browser_manager.py`(`InteractiveElement` 类与 `_EXTRACT_ELEMENTS_JS` 字符串)。落地时按实仓位置实施;契约不变。

**`src/core/page_state.py`**:

```python
# InteractiveElement.to_label() 改动
class InteractiveElement:
    def to_label(self, redactor: SecretRedactor | None = None) -> str:
        """
        Generate label for LLM consumption.

        If the element is a sensitive input, value is omitted; replaced with
        an explicit marker. Even non-sensitive values pass through redactor
        for defense-in-depth.
        """
        is_sensitive = SecretRedactor.is_sensitive_input(
            input_type=self.input_type,
            name=self.name,
            autocomplete=self.autocomplete,
            placeholder=self.placeholder,
            aria_label=self.aria_label,
        )

        value_repr = ""
        if self.value:
            if is_sensitive:
                value_repr = "[值=[REDACTED]]"
            else:
                redacted = redactor.redact_text(self.value) if redactor else self.value
                value_repr = f"[值={redacted}]"

        # ... rest of label formatting
```

**`src/core/dom_processor.py`** Layer 1:

```python
# JS extraction stage 改动:
# 当前 JS 直接读取 input.value 并塞进 InteractiveElement.value
# 改动: JS 端检测 sensitive type, 直接传 null;Python 端不依赖此(Layer 2 兜底)

# 在 _extract_interactive_elements_js() 的 JS 字符串中添加:
SENSITIVE_TYPES = ['password']
SENSITIVE_AUTOCOMPLETE = ['one-time-code', 'current-password', 'new-password']
function extractValue(el) {
    if (el.tagName === 'INPUT') {
        if (SENSITIVE_TYPES.indexOf(el.type) !== -1) return null;
        if (SENSITIVE_AUTOCOMPLETE.indexOf(el.autocomplete) !== -1) return null;
        // name/placeholder pattern 检测 (镜像 Python 的 SENSITIVE_NAME_PATTERNS)
        var pattern = /(otp|passcode|password|token|secret|recovery|api[\W_]?key)/i;
        for (var attr of ['name', 'id', 'placeholder', 'aria-label']) {
            if (pattern.test(el.getAttribute(attr) || '')) return null;
        }
    }
    return el.value;
}
```

### 5.5 Test 用例

`tests/lapwing_kernel/test_redaction.py`:

```python
import pytest
from lapwing_kernel.redaction import SecretRedactor

@pytest.fixture
def redactor():
    return SecretRedactor(config={})

# Sensitive input detection
def test_password_input_is_sensitive(redactor):
    assert SecretRedactor.is_sensitive_input("password", "pwd", None, None, None)

def test_otp_autocomplete_is_sensitive(redactor):
    assert SecretRedactor.is_sensitive_input("text", "code", "one-time-code", None, None)

def test_name_pattern_is_sensitive(redactor):
    assert SecretRedactor.is_sensitive_input("text", "api_key", None, None, None)
    assert SecretRedactor.is_sensitive_input("text", None, None, "Enter OTP code", None)
    assert SecretRedactor.is_sensitive_input("text", None, None, None, "Recovery code")

def test_normal_input_is_not_sensitive(redactor):
    assert not SecretRedactor.is_sensitive_input("text", "email", "email", None, None)
    assert not SecretRedactor.is_sensitive_input("text", "search", None, "search", None)

# Secret-shaped text scrubbing
def test_jwt_redacted(redactor):
    text = "Header: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    assert "[REDACTED]" in redactor.redact_text(text)

def test_openai_key_redacted(redactor):
    text = "Set OPENAI_API_KEY to sk-abc1234567890abcdefghijklmnopqrstuvwxyz"
    assert "[REDACTED]" in redactor.redact_text(text)
    assert "sk-abc" not in redactor.redact_text(text)

def test_github_pat_redacted(redactor):
    text = "Token is ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789"
    assert "[REDACTED]" in redactor.redact_text(text)

# Dict sanitization
def test_dict_password_field_redacted(redactor):
    d = {"username": "kevin", "password": "hunter2", "service": "github"}
    out = redactor.redact_dict(d)
    assert out["password"] == "[REDACTED]"
    assert out["username"] == "kevin"

def test_nested_dict_redacted(redactor):
    d = {"creds": {"api_key": "abc123def456", "url": "https://x.com"}}
    out = redactor.redact_dict(d)
    assert out["creds"]["api_key"] == "[REDACTED]"

# Observation level
def test_observation_redacted(redactor):
    from lapwing_kernel.primitives.observation import Observation
    obs = Observation(
        id="o1", action_id="a1", resource="browser",
        status="ok",
        content="JWT in page: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJrZXZpbiJ9.signature_part_here_long_enough",
        artifacts=[{"page_state_ref": "ps1", "password": "leaked123"}],
    )
    redacted = redactor.redact_observation(obs)
    assert "REDACTED" in redacted.content
    assert redacted.artifacts[0]["password"] == "[REDACTED]"

# Integration: full leak path
def test_page_state_to_llm_no_password():
    from core.page_state import InteractiveElement
    elem = InteractiveElement(
        tag="input", input_type="password", name="login_password",
        value="ShouldNeverAppear123", placeholder="Password",
    )
    label = elem.to_label(redactor=SecretRedactor(config={}))
    assert "ShouldNeverAppear123" not in label
    assert "REDACTED" in label

def test_page_state_with_otp_no_value():
    from core.page_state import InteractiveElement
    elem = InteractiveElement(
        tag="input", input_type="text", name="otp",
        autocomplete="one-time-code",
        value="123456",
    )
    label = elem.to_label(redactor=SecretRedactor(config={}))
    assert "123456" not in label
```

### 5.6 验收

```text
[ ] tests/lapwing_kernel/test_redaction.py 全绿
[ ] 抓 git grep 确认无任何 LLM-visible 路径直接读取 InteractiveElement.value
    而不经过 to_label() 或 redactor
[ ] 手测: 在 chrome 装一个测试登录页, 跑 browser_login → 失败 → 检查
    返回的 PageState.to_llm_text() 无密码出现
```

---

## 6. BrowserAdapter

### 6.1 Profile config

`config.toml`:

```toml
[lapwing_kernel.resources.browser.fetch]
enabled = true
headless = true
persistent = false
user_data_dir = "data/browser/profiles/fetch"
viewport_width = 1280
viewport_height = 800

[lapwing_kernel.resources.browser.personal]
enabled = true
headless = false                    # 真实 headful, 见 §6.6
persistent = true
user_data_dir = "/home/lapwing/.config/lapwing-browser"
allow_takeover = true
viewport_width = 1440
viewport_height = 900

[lapwing_kernel.resources.browser.operator]
enabled = false                     # v1 不启用; 历史 operator profile 预留
```

### 6.2 BrowserAdapter API

```python
# src/lapwing_kernel/adapters/browser.py

from __future__ import annotations
from typing import Any, ClassVar
from ..primitives.action import Action
from ..primitives.observation import Observation, validate_status
from ..redaction import SecretRedactor
from ..stores.interrupt_store import InterruptStore
from ..primitives.interrupt import Interrupt, DEFAULT_INTERRUPT_EXPIRY


class BrowserAdapter:
    """
    Single adapter, multiple profiles.

    Profiles:
      - fetch:    headless, ephemeral, public web only
      - personal: headful (Xvfb-backed on PVE, see §6.6), persistent, signed-in identity
      - operator: legacy, v1 disabled

    Legacy backend:
      profile="fetch" wraps existing BrowserManager (src/core/browser_manager.py).
      profile="personal" is a fresh Playwright persistent context, independent of
      BrowserManager. No state sharing.
    """
    name: ClassVar[str] = "browser"
    SUPPORTED_VERBS: ClassVar[frozenset[str]] = frozenset({
        "navigate", "click", "type", "select", "scroll", "screenshot",
        "get_text", "back", "wait", "login", "form_submit", "download",
    })

    def __init__(self, *, profile: str, config: dict[str, Any],
                 redactor: SecretRedactor,
                 interrupt_store: InterruptStore,
                 model_slots: "ModelSlotResolver",
                 legacy_browser_manager: Any | None = None):  # 仅 fetch 用
        self.profile = profile
        self._cfg = config
        self._redactor = redactor
        self._interrupts = interrupt_store
        self._model_slots = model_slots
        self._legacy = legacy_browser_manager
        # personal profile 内部维护自己的 Playwright context
        self._personal_context = None

    def supports(self, verb: str) -> bool:
        return verb in self.SUPPORTED_VERBS

    async def execute(self, action: Action) -> Observation:
        if self.profile == "fetch":
            return await self._execute_fetch(action)
        if self.profile == "personal":
            return await self._execute_personal(action)
        raise ValueError(f"Unknown profile {self.profile}")

    async def _execute_fetch(self, action: Action) -> Observation:
        # 包装现有 BrowserManager
        if self._legacy is None:
            raise RuntimeError("fetch profile requires legacy_browser_manager")

        verb = action.verb
        if verb == "navigate":
            url = action.args["url"]
            try:
                result = await self._legacy.navigate(url)
            except Exception as exc:
                return Observation.failure(
                    action.id, "browser", status="failed",
                    error=type(exc).__name__, summary=str(exc)[:200],
                )
            # 检测 WAF / CAPTCHA: 复用 SmartFetcher._is_challenge_page()
            if self._legacy.is_challenge_page(result):
                # GPT non-blocking D: fetch profile WAF 不创建 Interrupt.
                # 理由:
                #   - fetch profile 是 ephemeral,无持久身份
                #   - 即使 owner takeover 也无意义,刷新仍触发 WAF
                #   - 创建 Interrupt 会让 /interrupts/pending 列表充满
                #     用户无法处理的 fetch WAF 通知,污染 owner attention queue
                # 改为:仅产出 Observation(status=waf_challenge, interrupt_id=None),
                # 由上层 agent 决定是否要 retry with personal profile.
                # EventLog 记录 browser.challenge,但不进 InterruptStore.
                return Observation(
                    id=self._new_id(),
                    action_id=action.id,
                    resource="browser",
                    status="waf_challenge",
                    interrupt_id=None,             # 显式 None: 这是 failed source 不是 owner action
                    summary=f"WAF challenge on {url}; fetch profile cannot bypass. "
                            f"Consider retry via personal profile if persistent identity helps.",
                    provenance={"url": url, "profile": "fetch"},
                )

            # 翻译 PageState → Observation (§6.3)
            return self._translate_page_state(action, result)

        # 其他 verb 类似...
        raise NotImplementedError(f"verb {verb}")

    async def _execute_personal(self, action: Action) -> Observation:
        # 独立 Playwright context, 不复用 BrowserManager
        context = await self._ensure_personal_context()
        verb = action.verb

        if verb == "navigate":
            url = action.args["url"]
            page = context.pages[0] if context.pages else await context.new_page()
            try:
                await page.goto(url, timeout=30000)
            except Exception as exc:
                return Observation.failure(
                    action.id, "browser", status="timeout",
                    error=type(exc).__name__, summary=str(exc)[:200],
                )

            # 检测 challenge
            challenge_kind = await self._detect_challenge(page)
            if challenge_kind:
                # personal profile 可以 takeover (有 user_data_dir 持久化 + headful)
                continuation_ref = self._suspend_continuation(action.task_ref)
                interrupt = Interrupt.new(
                    kind=f"browser.{challenge_kind}",
                    actor_required="owner",
                    resource="browser",
                    resource_ref=str(id(page)),
                    continuation_ref=continuation_ref,
                    summary=f"{challenge_kind} on {url}; awaiting owner takeover",
                    payload_redacted={"url": url, "profile": "personal"},
                    expires_in=DEFAULT_INTERRUPT_EXPIRY.get(f"browser.{challenge_kind}"),
                )
                self._interrupts.persist(interrupt)
                return Observation.interrupted(
                    action.id, "browser",
                    interrupt_id=interrupt.id,
                    summary=interrupt.summary,
                )

            # 提取 PageState (内部 artifact) → Observation
            page_state = await self._extract_page_state(page)
            return self._translate_page_state(action, page_state)

        if verb == "login":
            # login 需要 credential, 走 credential.use action 链 (见 §7.4)
            ...

        raise NotImplementedError(f"verb {verb} on personal profile")

    def _translate_page_state(self, action: Action, page_state) -> Observation:
        """
        §6.3 PageState → Observation 翻译 contract.

        - PageState is internal; never returned raw to LLM
        - Observation.content: redacted text summary
        - Observation.artifacts: page_state_ref / screenshot_ref / element_list_ref
          (NOT auto LLM-facing)
        """
        # Text summary: 取 PageState.text_summary, 经过 redactor
        text_summary = self._redactor.redact_text(page_state.text_summary)

        # Artifacts: 保存 PageState 到内部 cache, 暴露 ref
        page_state_id = self._cache_page_state(page_state)
        artifacts = [{
            "type": "page_state_ref",
            "ref": page_state_id,
            "url": page_state.url,
        }]
        if page_state.screenshot_path:
            artifacts.append({
                "type": "screenshot_ref",
                "ref": page_state.screenshot_path,
            })
        # interactive elements 也是 artifact, 不进 content
        artifacts.append({
            "type": "element_list_ref",
            "ref": page_state_id,
            "count": len(page_state.interactive_elements),
        })

        return Observation.ok(
            action.id, "browser",
            summary=f"navigated to {page_state.url}: {page_state.title}",
            content=text_summary,
            artifacts=artifacts,
            provenance={
                "url": page_state.url,
                "final_url": page_state.final_url,
                "title": page_state.title,
                "profile": self.profile,
            },
        )

    async def _detect_challenge(self, page) -> str | None:
        # 复用 SmartFetcher._is_challenge_page() 的检测逻辑
        # 返回 "captcha" / "waf" / "login_required" / "auth_2fa" / None
        ...

    def _suspend_continuation(self, task_ref: str | None) -> str:
        """Register a continuation for current task, return ref."""
        from ..pipeline.continuation_registry import ContinuationRegistry
        return ContinuationRegistry.instance().register(task_ref)

    async def _ensure_personal_context(self): ...
    async def _extract_page_state(self, page): ...
    def _cache_page_state(self, page_state) -> str: ...
    def _new_id(self) -> str: ...
```

### 6.3 PageState → Observation translation contract

**硬规则:**

- `PageState` 永远不裸暴露给 LLM。它是 BrowserAdapter 的 internal artifact。
- `Observation.content`:仅来自 `PageState.text_summary`(已 redact),LLM-facing
- `Observation.artifacts`:`page_state_ref` / `screenshot_ref` / `element_list_ref`,**不自动 LLM-facing**
- 任何 LLM 想看 element 列表 → 显式工具 `browse_element_list(page_state_ref)` (sub-agent only,不上主面)
- Sensitive input value 在 DOM 抽取阶段(JS)和 to_label 阶段(Python)双重 redact

### 6.4 Status 扩展

`Observation.status` 在 browser 上扩展(参见 §3.2 `BROWSER_EXTRA_STATUS`):

```text
ok                       — 成功加载,有 page_state
waf_challenge            — WAF 拦截(fetch profile 无法 takeover → non_resumable)
captcha_required         — CAPTCHA(personal profile 可 takeover)
auth_required            — 需登录但无 credential
user_attention_required  — 通用 owner 介入(2FA / 验证短信 / 等)
timeout                  — Playwright timeout
network_error            — DNS / 连接失败
empty_content            — 页面加载但无可消费内容
blocked_by_policy        — URL 在 blocklist
interrupted              — 通用挂起(参见 5.1 §3.2 COMMON_STATUS)
```

### 6.5 Redaction at adapter layer

`BrowserAdapter._translate_page_state()` 必须:

1. `text_summary` 进 redactor 之前由 `_extract_page_state()` 已先用 sensitive input filter 跳过 password value
2. `artifacts` 中任何字典字段进 `redactor.redact_dict()`
3. `provenance.url` 不 redact(URL 本身不视为 secret;但 query string 中的 token-shaped 内容会被 `redact_text` 抓到)

### 6.6 Personal profile 在 PVE 上的运行 (Open Question O-4 答案)

**决策:Personal profile 用 Xvfb 虚拟显示 + 可选 VNC 用于 takeover。**

PVE 服务器无 GUI,但 headful Playwright 需要 X display。方案:

```bash
# systemd service: lapwing-xvfb.service
ExecStart=/usr/bin/Xvfb :99 -screen 0 1440x900x24

# 环境变量
DISPLAY=:99

# Takeover 路径 (Kevin 端访问 Lapwing 浏览器):
# - x11vnc on display :99 暴露 VNC 5900 (仅 LAN 或 SSH tunnel)
# - 或 noVNC 通过 Tauri Desktop 嵌入

# v1 实施: 启用 Xvfb + x11vnc;noVNC web 端集成留到 v1 之后再做
```

`docs/operations/personal_browser_xvfb.md` 单独写运维说明,blueprint 不展开。

**注:** v1 期间 Kevin 通过 VNC client(或 SSH X11 forwarding)做 takeover,后续 v2 集成进 Tauri Desktop。

---

## 7. CredentialAdapter

### 7.1 设计原则 (v1.1 per GPT final pass)

**Adapter 不得直接 import / call 另一个 adapter。** Cross-resource coordination 必须通过 Kernel action pipeline 或显式 in-memory lease/handle 机制完成。

v1.0 初稿设计了 `_inject_into_browser` 作为唯一 adapter-to-adapter 直调路径,GPT 拒绝。v1.1 改为 **CredentialLease + CredentialLeaseStore** 机制:

```text
CredentialAdapter.use
  → 校验 policy / first-use / vault
  → CredentialLeaseStore.create(service, secret) → lease_id
  → Observation(artifacts=[credential_lease_ref])    # 不含明文,LLM 看不到
                                                      # artifact 不自动 LLM-facing

BrowserAdapter.login
  → 从前一个 Observation.artifacts 取 lease_id
  → CredentialLeaseStore.consume(lease_id) → secret  # 进程内,one-shot
  → 在 Playwright page 内填表
  → lease 即用即销毁
```

边界:

- `CredentialAdapter` 不知道 `BrowserAdapter` 存在
- `BrowserAdapter` 不知道 `CredentialVault` 存在
- secret 不进 `Action.args`
- secret 不进 `Observation.content`
- secret 不进 `EventLog.data_redacted`(redactor 兜底)
- secret 只存在于 `CredentialLeaseStore` (in-memory, short TTL),consume 后立即删除

### 7.2 CredentialLeaseStore

```python
# src/lapwing_kernel/adapters/credential_lease_store.py
# 这是 adapter 层的辅助 store, 不是顶层 fact source。
# In-memory only, 不持久化 (vault 已经加密落盘, lease 只是解密后短期持有)

from __future__ import annotations
import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any


# Lease TTL: 默认 30 秒,足够一次 login 表单填入,超时即作废
DEFAULT_LEASE_TTL = timedelta(seconds=30)


@dataclass(frozen=True)
class CredentialLease:
    """
    Ephemeral credential lease. NOT serializable to LLM/EventLog/Action args.

    The actual secret is held inside CredentialLeaseStore, never on the dataclass.
    `id` and `service` may be referenced externally; secret access requires
    `LeaseStore.consume(id)`.
    """
    id: str
    service: str
    purpose: str            # e.g. "browser_login"
    issued_at: datetime
    expires_at: datetime


class CredentialLeaseStore:
    """
    In-memory, one-shot. Consume() removes the entry.

    Singleton per process. ContinuationRegistry-style lifecycle:
      kernel restart = all leases lost (no recovery, by design).

    NOT visible to LLM. NOT serialized. NOT logged.

    HARD CONSTRAINT (per GPT sign-off note, v1.1 final):
      This store MUST remain in-memory only. It is forbidden to persist leases
      to any of:
        - sqlite / data/lapwing.db / any other DB
        - jsonl / log files / append-only stores
        - shelve / pickle / marshal
        - in-process disk caches
        - shared memory across processes
      Lease secrets are vault-decrypted plaintext; persisting them would defeat
      the vault's encryption and violate I-2 (no LLM-visible plaintext).

      Process restart = all leases lost. This is acceptable because lease lifetime
      is bounded by DEFAULT_LEASE_TTL (30s) — no scenario requires a sub-minute
      lease to survive a restart.

      Implementer must add a test (§15.2 I-2) that grep-asserts no `sqlite3`,
      `open(.., "w")`, `pickle.dump`, `shelve.open` calls exist anywhere in
      src/lapwing_kernel/adapters/credential_lease_store.py.
    """
    _instance: "CredentialLeaseStore | None" = None

    @classmethod
    def instance(cls) -> "CredentialLeaseStore":
        if cls._instance is None:
            cls._instance = CredentialLeaseStore()
        return cls._instance

    def __init__(self):
        self._secrets: dict[str, Any] = {}      # lease_id -> raw credential object
        self._leases: dict[str, CredentialLease] = {}
        self._lock = asyncio.Lock()

    async def create(self, *, service: str, secret: Any,
                     purpose: str = "browser_login",
                     ttl: timedelta = DEFAULT_LEASE_TTL) -> CredentialLease:
        async with self._lock:
            now = datetime.utcnow()
            lease = CredentialLease(
                id=str(uuid.uuid4()),
                service=service,
                purpose=purpose,
                issued_at=now,
                expires_at=now + ttl,
            )
            self._secrets[lease.id] = secret
            self._leases[lease.id] = lease
            # 调度自动过期清理
            asyncio.create_task(self._auto_expire(lease.id, ttl))
        return lease

    async def consume(self, lease_id: str) -> Any | None:
        """One-shot retrieve. Returns secret object and removes lease."""
        async with self._lock:
            lease = self._leases.get(lease_id)
            if lease is None:
                return None
            if datetime.utcnow() > lease.expires_at:
                self._purge(lease_id)
                return None
            secret = self._secrets.pop(lease_id)
            self._leases.pop(lease_id)
            return secret

    def peek_meta(self, lease_id: str) -> CredentialLease | None:
        """Read metadata only (no secret). Used by BrowserAdapter to verify
        lease exists / belongs to right service before consuming."""
        return self._leases.get(lease_id)

    async def _auto_expire(self, lease_id: str, ttl: timedelta) -> None:
        await asyncio.sleep(ttl.total_seconds() + 1)
        async with self._lock:
            self._purge(lease_id)

    def _purge(self, lease_id: str) -> None:
        self._secrets.pop(lease_id, None)
        self._leases.pop(lease_id, None)
```

### 7.3 CredentialAdapter

LLM-visible surface:
- `list_count` — count only, no service names (privacy)
- `exists(service)` — exists / missing (LLM provides service name)
- `use(service)` — produces lease artifact, no plaintext anywhere LLM-facing
- `create` — blocked_by_policy (owner-only via CLI)

### 7.4 CredentialUseState (replaces first-use config)

GPT non-blocking B: first-use 是状态不是 config。改为 sqlite 表:

```sql
-- src/lapwing_kernel/adapters/credential_use_state.sql
CREATE TABLE IF NOT EXISTS credential_use_approvals (
    service     TEXT PRIMARY KEY,
    approved_at TEXT NOT NULL,
    approved_by TEXT NOT NULL DEFAULT 'owner'
);
```

### 7.5 BrowserAdapter side: lease consumption

`BrowserAdapter.login` 不 import `CredentialAdapter`。它从前序 Observation 的 `artifacts` 里读 `credential_lease_ref`,通过 `CredentialLeaseStore` 直接取 secret。Plaintext 仅在 `_login_with_lease` 方法内存在,不进 Observation / Action / EventLog。

### 7.6 LLM-visible surface

LLM 通过 sub-agent 调用 `credential` 只能看到:

```text
credential.list_count     → content="count=N"  (no service names)
credential.exists         → content="exists" | "missing"
credential.use            → content="credential available"
                             artifacts=[{type: credential_lease_ref, ref, service, ...}]
                             (artifact NOT auto LLM-facing per v0.2 §7.2; only
                              BrowserAdapter or other in-process consumers read ref)
credential.create         → blocked_by_policy
```

任何含明文 password / token / OTP / cookie 的 Observation 字段视为 P0 bug。任何 EventLog 含明文同此。

### 7.7 完整登录流程

```text
agent decides to login to github via personal browser
  → Action(resource=browser, resource_profile=personal, verb=login,
           args={service: "github", url, username_selector, password_selector})
    → policy:
        if not credential_use_state.has_been_used("github"):
            return INTERRUPT     # 首次使用,owner 必须明确批准
        else:
            return ALLOW
    → 若 INTERRUPT: Kevin 通过 Desktop /interrupts/{id}/approve, policy 之后允许
    → 若 ALLOW:
        BrowserAdapter._execute_personal:
          1. 内部发起 sub-action(通过 ActionExecutor):
             sub_action = Action.new(
                 resource="credential", verb="use",
                 args={service: "github", purpose: "browser_login"},
                 parent_action_id=action.id,
             )
             cred_obs = await executor.execute(sub_action)
          2. 从 cred_obs.artifacts 读 lease_ref (CredentialAdapter 不知道 browser 存在)
          3. self._login_with_lease(page, lease_ref, target_form)
          4. LeaseStore.consume 取出 secret, 填表, secret 立即销毁
          5. 返回 Observation(status=ok, summary="logged in to github")
             无明文,credential 用法记 EventLog credential.used (refs only, no secret)
```

---

## 8. InterruptStore

### 8.1 SQLite schema

```sql
-- src/lapwing_kernel/stores/interrupt_store.sql

CREATE TABLE IF NOT EXISTS interrupts (
    id                      TEXT PRIMARY KEY,
    kind                    TEXT NOT NULL,
    status                  TEXT NOT NULL,           -- pending/resolved/denied/expired/cancelled
    actor_required          TEXT NOT NULL,
    resource                TEXT NOT NULL,
    resource_ref            TEXT,
    continuation_ref        TEXT,
    non_resumable           INTEGER NOT NULL DEFAULT 0,
    non_resumable_reason    TEXT,
    summary                 TEXT NOT NULL DEFAULT '',
    payload_redacted_json   TEXT NOT NULL DEFAULT '{}',
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    expires_at              TEXT,
    resolved_payload_json   TEXT
);

CREATE INDEX IF NOT EXISTS idx_interrupts_status   ON interrupts(status);
CREATE INDEX IF NOT EXISTS idx_interrupts_kind     ON interrupts(kind);
CREATE INDEX IF NOT EXISTS idx_interrupts_created  ON interrupts(created_at);
```

### 8.2 InterruptStore Python API

API: `persist`, `get`, `list_pending`, `resolve`, `deny`, `cancel(reason=...)`, `expire_overdue()`. Implementation matches §8.2 in the conversation blueprint.

### 8.3 ContinuationRegistry (in-memory async)

In-memory registry of suspended agent tasks awaiting interrupt resolution. continuation_ref -> asyncio.Future.

**CRITICAL CONTRACT (v1.1 per GPT final pass):** ActionExecutor.resume() MUST call has(ref) BEFORE persisting interrupt as 'resolved'. Lost continuations must be marked 'cancelled', not 'resolved'.

API: `register(task_ref) -> ref`, `has(ref) -> bool`, `get_status(ref) -> "active"|"missing"|"done"|"cancelled"`, `wait_for_resume(ref) -> dict`, `resume(ref, payload)`, `cancel(ref, reason)`, `cleanup(ref)`.

### 8.4 ContinuationRegistry cleanup lifecycle (v1.1 final per GPT sign-off)

**Hard invariant:** Every `continuation_ref` registered via `register()` must eventually be released via `cleanup(ref)`. The four terminal transitions for the related Interrupt all trigger cleanup. No `register()` without a paired `cleanup()`.

Canonical worker pattern: `try / except InterruptCancelled / finally registry.cleanup(ref)` in the agent worker that called `wait_for_resume(ref)`.

| Terminal transition           | Trigger                                | Cleanup owner          |
|-------------------------------|----------------------------------------|------------------------|
| resolved                      | `kernel.resume()` succeeds             | worker's `finally`     |
| denied                        | `/interrupts/{id}/deny` → `cancel()`   | worker's `finally`     |
| expired                       | `expire_overdue_loop` → `cancel()`     | worker's `finally`     |
| cancelled (task-side)         | upstream `cancel(ref, reason='...')`   | worker's `finally`     |
| continuation_lost_after_restart | `ActionExecutor.resume()` (lost path) | no worker exists → no cleanup needed (registry is empty anyway) |

**Tested invariant (§15.2 I-6 expanded in v1.1 final):**
- After each terminal transition: `registry.get_status(ref) == 'missing'`
- 100 sequential complete interrupt cycles: registry internal dict size returns to 0
- Restart scenario (lost continuation): no cleanup needed because no register() ever called in current process

### 8.5 InterruptStore 状态机

```text
pending  ──(owner approve)──→ resolved
         ──(owner deny)─────→ denied
         ──(timeout / expire_overdue)→ expired
         ──(task cancel / continuation_lost)──→ cancelled
```

`expired` 与 `denied` 触发 continuation 用 `Observation.status="interrupted"` 收尾,不静默丢失。后台 `expire_overdue_loop` 每 5 分钟跑一次。

### 8.6 Desktop API

实仓位置:`src/api/routes/interrupts.py`(注册于 `src/api/server.py`)。

Endpoints:
- `GET /interrupts/pending` → list pending interrupts (filtered by actor=owner)
- `GET /interrupts/{id}` → detail
- `POST /interrupts/{id}/approve` → resume; returns status dict, NOT Observation; does NOT await final Observation
- `POST /interrupts/{id}/deny` → deny + cancel continuation

SSE v2 推送 type=`interrupt.created` / `interrupt.resolved` / `interrupt.continuation_lost`。

---

## 9. EventLog

### 9.1 SQLite schema

```sql
-- src/lapwing_kernel/stores/event_log.sql

CREATE TABLE IF NOT EXISTS events (
    id              TEXT PRIMARY KEY,
    time            TEXT NOT NULL,
    actor           TEXT NOT NULL,
    type            TEXT NOT NULL,
    resource        TEXT,
    summary         TEXT NOT NULL,
    outcome         TEXT,
    refs_json       TEXT NOT NULL DEFAULT '{}',
    data_redacted_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_events_time     ON events(time);
CREATE INDEX IF NOT EXISTS idx_events_type     ON events(type);
CREATE INDEX IF NOT EXISTS idx_events_resource ON events(resource);
CREATE INDEX IF NOT EXISTS idx_events_actor    ON events(actor);
```

**Append-only。无 UPDATE / DELETE 路径。**

### 9.2 EventLog Python API

API: `append(event)`, `query(type_prefix, resource, actor, since, until, limit)`, `count()`. No UPDATE / DELETE.

### 9.3 NOT to do

```text
- 不默认注入 prompt
- 不自动蒸馏到 Wiki
- 不抽 episodic memory
- 不让 LLM 自然访问全表
```

LLM 想读 EventLog → 通过显式 `read_fact(scope="event", query={...})` 工具。

### 9.4 Retention policy (Open Question O-3 答案)

**v1 决策:无清理,append-only,体积监控仅告警。**

---

## 10. Model Slots

### 10.1 配置 schema

`data/config/model_routing.json` 改造为 tier-list 形态(per-slot tiers with candidates),具体 candidates 由 Kevin 在 sign-off 后填入实际可用 model_id 列表。

### 10.2 ModelSlotResolver

**Hard rules (v0.2 §10):**
- empty candidates → ConfigError at construct time
- tier order is fixed; no dynamic performance routing in v1
- probe results may be cached; order is config-determined
- fallback transitions emit EventLog model.fallback

### 10.3 启动校验

`main.py` 启动阶段:`ModelSlotResolver.from_config(config["model_slots"])` 失败则 `sys.exit(2)`。同时验证 `capability_requirements` 与 model registry 一致。

### 10.4 探活与缓存 (Open Question O-5 答案)

- 启动时**不预探活**。第一次 `resolve()` 时假定 primary 可用,失败由 `report_failure()` 标记
- 探活缓存 TTL **=60 分钟**;过期后下次 resolve 重新尝试 primary
- 缓存 in-memory(进程内),进程重启 = 重置
- 每次 fallback transitions 写 EventLog `model.fallback`

---

## 11. Tool Surface & Delegation

### 11.1 主面工具(目标 <10)

```python
MAIN_SURFACE_TOOLS = [
    "delegate_to_agent",
    "respond_to_interrupt",
    "read_state",
    "update_state",
    "read_fact",
    "send_message",
]
```

计数:6 个。<10 满足。

`reminder / promise / focus / correction` 默认合并到 `read_state` / `update_state`。

### 11.2 delegate_to_agent

```python
async def delegate_to_agent(
    kernel: Kernel,
    catalog: AgentCatalog,
    *,
    kind: str,                # researcher / coder / resident_operator / browser_operator
    task: str,
    constraints: dict | None = None,
) -> "Observation":
```

kind 来自 AgentCatalog。Adding a new agent kind = registering in catalog,NOT adding a new main-surface tool.

### 11.3 旧 delegate 工具迁移

```text
delegate_to_researcher  → 通过 delegate_to_agent(kind="researcher", ...)
delegate_to_coder        → 通过 delegate_to_agent(kind="coder", ...)
```

### 11.4 read_state / update_state / read_fact

```python
async def read_state(*, scope: str, query: dict | None = None) -> dict
async def update_state(*, scope: str, op: str, value: dict) -> dict
async def read_fact(*, scope: str, query: dict) -> dict
```

read_fact scopes: `trajectory`, `wiki`, `eventlog`。read_fact 是 fact source query,NOT a ResourceAdapter。

### 11.5 respond_to_interrupt

Lapwing's own reaction to an outstanding interrupt (notify_owner / acknowledge / cancel). Owner approve/deny does NOT go through this tool; that's via Desktop /interrupts API.

---

## 12. Memory Wiki Repositioning

### 12.1 写入路径拔除清单

```text
config.toml:
  [memory.wiki]
  enabled = true              # read 仍开
  context_enabled = true      # 注入 prompt 仍开 (read-mostly)
  gate_enabled = false        # 写入门关
  write_enabled = false       # 写入关
  lint_enabled = false        # lint 不在 v1 范围
  auto_writeback = false      # 显式关闭 ambient writeback
```

### 12.2 entity seeds 处理 (Open Question O-6 答案)

**决策:保留 `data/memory/wiki/entities/{kevin,lapwing}.md`,但内容重写。** 标 `compiler_version: manual-curated-v1`。内容由 Kevin 手工 curate。

### 12.3 read_fact(scope="wiki") 接口

LLM 通过主面 `read_fact(scope="wiki", query={...})` 读 wiki。不允许其他路径直接消费 wiki 内容。

---

## 13. Delete / Defer Execution

### 13.1 Level 1+2+3 执行清单

| 模块 | DELETE Level | Slice | 操作 |
|------|--------------|-------|------|
| Identity Substrate Ticket B 蓝图 | 1 | J | mv to `docs/archive/` |
| Identity Substrate Ticket B 代码 | 2+3 | J | 删除 config 段 + 拔 AppContainer wiring;代码暂留 |
| Capability Evolution 8-phase 蓝图 | 1 | J | mv to `docs/archive/` |
| Capability Evolution 代码 | 2+3 | J | 删除 `config.toml [capabilities]` + 删 `container.py` import |
| task_learning 设计 | 1 | J | mv to `docs/archive/` |
| MiroFish 残留 | 4 | J | grep 删干净 |
| TacticalRules / QualityChecker / ProactiveFilter / EvolutionEngine | 4 | J | 删除所有 import 路径 + config keys |
| `src/memory/wiki/pipeline/auto_writeback.py` wiring | 3 | C/J | 拔 wiring,代码保留 (read-mostly 角色) |

### 13.2 配置项删除清单

```text
config.toml 删:
  [capabilities]
  [identity.substrate_b]      # (若存在)
  [memory.wiki].write_enabled  # 改为 false 显式锁定
  [task_learning]              # (若存在)

data/config/model_routing.json 改:
  全表改成 §10.1 tier-list 形态
```

### 13.3 wiring 删除清单

```text
src/app/container.py 拔:
  CapabilityRegistry 注册
  ExperienceCurator 注册
  CapabilityEvolutionEngine 注册
  Maintenance A/B/C scheduler

src/main.py 拔:
  任何 capabilities 子模块 startup hook
  任何 identity.substrate_b startup hook
```

### 13.4 Level 4 代码删除(Open Question O-7 答案)

**v1 决策:仅删 MiroFish 整目录。`src/capabilities/` 整目录、`src/identity/substrate_ticket_b/` 整目录代码暂留但完全拔 wiring。**

### 13.5 Slice J 前的 grep audit (v1.1 per GPT final pass)

§13.1 - §13.4 按"已知模块"列删除清单,但仓库里可能有遗漏的小残留。**Slice J 开始前必须跑 grep audit,每条 hit 分类成 DELETE / DISCONNECT / KEEP-UNTIL-REPLACED / KEEP,任何 unclassified hit 阻塞 v1 sign-off。**

输出: `docs/audit/grep_audit_pre_slice_j_<date>.md` (实施计划锁定为 `docs/refactor_v2/slice_j_grep_audit.md`)

Audit grep checklist 见 conversation blueprint §13.5 全文(8 大类:agent/tool 旧入口、browser 旧链路、credential 旧链路、memory/ambient 旧链路、capability/identity 残留、event/proactive 补偿层、default-false config、misc)。

---

## 14. Slice Specifications

每个 Slice 是一个独立可交付单元。下面列出每个 Slice 的 **任务 / 文件变更清单 / 验收**,不规定执行顺序或工期。依赖图见 §14.x。

### Slice P0-Redaction
**任务:** 实现 SecretRedactor (§5.3)、改造 InteractiveElement.to_label() (§5.4)、改造 JS extraction、添加 redaction tests (§5.5)、验收 §5.6。

### Slice A: Kernel Primitives
**任务:** 五 primitive dataclass、ResidentIdentity、PolicyDecider、ResourceRegistry、ActionExecutor、ContinuationRegistry、kernel.py (≤150 行)、相应单测。

### Slice C: BrowserAdapter
依赖:A + P0 + D
**任务:** BrowserAdapter 单一类多 profile(fetch / personal)、PageState → Observation translation、challenge detection、Xvfb service。

### Slice D: InterruptStore + persistence
依赖:A
**任务:** schema + Python API + 状态机 transitions + expire_overdue_loop。

### Slice E: Resume Mechanics
依赖:C + D + F
**任务:** ActionExecutor.resume() check-continuation-first、BrowserAdapter continuation 接通、Desktop /interrupts API、SSE v2 推送、e2e 测试。

### Slice F: EventLog persistence
依赖:A
**任务:** schema + append-only API + 显式 query 接口。

### Slice G: CredentialAdapter
依赖:A + P0 + D + F
**任务:** CredentialLeaseStore (in-memory)、CredentialAdapter、CredentialUseState (sqlite)、BrowserAdapter _login_with_lease、完整登录流程 integration test。

### Slice H: Model Slots
独立,无依赖
**任务:** ModelSlotResolver、tier-list schema、启动校验、LLMRouter 接入。

### Slice I: Delegate Surface 与 AgentCatalog Review
依赖:A + AgentCatalog review
**任务:** delegate_to_agent 单一入口、主面 ToolSpec 缩减到 <10、resident_operator agent kind。

### Slice J: Delete Wiring
依赖:所有替代路径就位 + §13.5 grep audit
**任务:** 拔 §13.3 wiring、删 §13.2 配置项、archive blueprints、Level 4 删 MiroFish。

### 14.x Slice 依赖图(设计约束,非排程)

```text
P0-Redaction ─────────────────────────┐ 无依赖
                                       │
Slice A : Kernel primitives ──────────┤ 无依赖
          (dataclasses + 接口)         │
                                       │
Slice D : Interrupt store + 持久化 ───A┤
Slice F : EventLog 持久化 ────────────A┤
Slice H : Model slots ─────────────── 独立(无依赖)
                                       │
Slice C : BrowserAdapter ─────A + P0 + D
Slice G : CredentialAdapter ──A + P0 + D + F
                                       │
Slice E : Resume mechanics ───C + D + F
Slice I : Delegate surface ───A + AgentCatalog review
                                       │
Slice J : Delete wiring ──── 所有替代路径就位后 + §13.5 grep audit 完成
```

---

## 15. Acceptance Test Matrix

**v1 不列堆叠验收矩阵。只验收一条闭环 + 6 条不变量 + 1 条 restart 边界。**

### 15.1 v1 闭环 e2e (the only canonical test)

`tests/integration/test_v1_closed_loop.py` — Kevin sends message → Lapwing cognition delegates → BrowserAdapter detects CAPTCHA → Interrupt persisted → owner approves → continuation resumes → final Observation flows → response to Kevin. EventLog assertions in order (other events may interleave): browser.navigate → browser.captcha_required → interrupt.created → interrupt.resolved → browser.ok → agent.completed. CredentialLeaseStore empty after test.

**Looseness note:** Worker may legitimately refresh the page after CAPTCHA resolution; what is forbidden is restarting the entire agent task or recreating the browser context.

### 15.2 6 条不变量验收(v0.2 §3)

#### I-1 不伪造结果 (`tests/invariants/test_I1_no_false_results.py`)
- WAF page returns waf_challenge status
- WAF observation content excludes HTML
- Cache-hit observation is marked (`provenance['cache_hit']`)
- Agent final answer does not claim search when cache hit (factual gate)

#### I-2 不泄密 (`tests/invariants/test_I2_no_llm_secret_leak.py`)
- Password input value not in PageState.to_llm_text()
- OTP input value not in Observation.content
- Token-shaped query string redacted
- Credential lease artifact does not contain secret
- EventLog data_redacted scrubbed
- Artifact renderer must be explicit (no auto-render into LLM)
- **Static-grep test:** `credential_lease_store.py` source contains NO `sqlite3`, `open(`, `pickle`, `shelve`, `marshal`
- Credential lease lost after process restart (integration test)

#### I-3 不绕访问控制 (`tests/invariants/test_I3_no_access_control_bypass.py`)
- captcha_required does not trigger auto-solve
- waf_challenge does not trigger stealth retry
- Personal profile CAPTCHA creates Interrupt + EventLog
- Fetch profile WAF does not create Interrupt

#### I-4 工具薄 (`tests/invariants/test_I4_tool_surface_lt_10.py`)
- `len(MAIN_SURFACE_TOOLS) < 10`
- No raw browser tools on main surface
- No raw credential tools on main surface
- No per-kind delegate tools (researcher / coder / resident_browser)
- `delegate_to_agent` is present

#### I-5 状态有事实源 (`tests/invariants/test_I5_fact_sources_explicit.py`)
- EventLog NOT injected into prompt by default
- EventLog read requires explicit read_fact
- Wiki write_enabled is False in config
- auto_writeback disabled
- Event-type 'action' rejected by wiki candidate store

#### I-6 resume (`tests/invariants/test_I6_interrupt_resume_works.py`)
- Approve resumes continuation
- Deny cancels continuation
- **Lost continuation does NOT mark resolved** (kernel restart scenario → status='cancelled', reason='continuation_lost_after_restart')
- Approve endpoint does not await final Observation (5s sleep in worker, approve response <1s)
- **Cleanup after each transition** (resolved / denied / expired / cancelled): `registry.get_status(ref) == 'missing'`
- 100 sequential cycles → registry size returns to 0

### 15.3 v1 sign-off pass criteria

```text
[ ] 1.  闭环 e2e test 全绿 (§15.1)
[ ] 2.  6 条不变量 test 全绿 (§15.2,每条至少 GPT 列出的所有断言)
[ ] 3.  Restart/lost-continuation 边界 test 全绿 (§15.2 I-6)
[ ] 4.  全仓 pytest 全绿
[ ] 5.  kernel.py ≤ 150 行
[ ] 6.  主面 ToolSpec < 10
[ ] 7.  src/ runtime code 无 ResidentRuntime / PersonalBrowserService /
       FetchBrowserService / BrowserResult / PendingUserAttentionStore /
       ResidentAuditLog 命名 (grep 验证)
       (docs/ 允许在 archival / negative context 提及,见 §18 / §19)
[ ] 8.  default-false / blueprint-only 模块在启动日志中无提及
[ ] 9.  PersonalBrowser Xvfb 运行,Kevin 可通过 VNC takeover
[ ] 10. CredentialAdapter 不暴露任何明文 secret;CredentialLeaseStore 一次性 consume
[ ] 11. No adapter imports another adapter directly
[ ] 12. EventLog append-only,无 UPDATE/DELETE 路径
[ ] 13. model_slots: candidates=[] 配置触发启动 ConfigError
[ ] 14. v0.1 残留:`src/capabilities/` / `src/identity/substrate_*` 等代码可暂留,
       但 active wiring/config 已清空
[ ] 15. §13.5 grep audit checklist 完整执行,无 unclassified hit
```

---

## 16. Open Question Answers

- **O-1.** PersonalBrowser sub-agent 形态: **AgentCatalog 新增 `resident_operator` agent kind**
- **O-2.** Interrupt expires_at 默认: **browser.* kinds → 24 小时,其他无过期**
- **O-3.** EventLog retention: **v1 无清理,append-only,体积监控仅告警**
- **O-4.** PersonalBrowser headful: **Xvfb + x11vnc**
- **O-5.** 模型探活: **启动不预探活,失败 in-memory 标记,TTL 60min,进程重启重置**
- **O-6.** Wiki entity seeds: **保留 kevin.md / lapwing.md,内容由 Kevin 手工 curate,标 manual-curated-v1**
- **O-7.** DELETE Level 4: **v1 仅删 MiroFish 整目录;capabilities/ 与 substrate_ticket_b/ 代码暂留**

---

## 17. Implementation Constraints

### 17.1 PR review 标准

每个 PR 必须:
- 通过 §15.3 中适用的 sign-off 项(对应 slice)
- 不引入 v1 范围外的功能(v0.2 §13.3 推迟项不允许触碰)
- **`src/` runtime code 不引入 v0.1 命名**(grep 验证范围仅限 `src/`,不包含 `docs/`)
- kernel.py 总行数监控:不超过 150
- **No adapter directly imports or calls another adapter**

### 17.2 回滚策略

- 每个 Slice PR 独立可 revert(不破坏 main 可启动)
- Slice E 与 Slice C 之间的边界:Slice C 完成时不接 PendingInterrupt resume,只产生 `Observation(status="user_attention_required")`(暂时 non_resumable);Slice E 完成后切换到真实 resume。这样 C 可独立部署测试。
- Slice J 必须分两次:先 archive blueprints + 删 config(可逆),再删 wiring(可逆,代码仍在)。Level 4 删代码留到 v1 验收后单独 PR。

### 17.3 v0.1 数据迁移

`data/lapwing.db` 现有 schema:
- 不动 `mutations` / `trajectory` / 其他既有表
- 新增 `interrupts` 表(`InterruptStore` 创建)
- 新增 `events` 表(`EventLog` 创建)
- 新增 `credential_use_approvals` 表(`CredentialUseState` 创建)
- 不迁移历史数据(EventLog 从 v1 启动那一刻开始记录)

`data/memory/wiki/` 现有:
- 不删 entity seeds(§12.2)
- 内容由 Kevin 手工 curate
- 不跑 wiki_compiler 重写

### 17.4 交付方式

Blueprint 全文交 Claude Code。Claude Code 自行 plan 实施顺序、PR 切分、并行点、工期。

---

## 18. Sign-off

```text
Kevin   : signed off (v0.2 intent + v1.1 final blueprint)         date: 2026-05-11
GPT     : signed off (v1.1 final, 2 non-blocking notes folded in)  date: 2026-05-11
Claude  : v1.1 final author                                        date: 2026-05-11
```

**v1.1 final 已定版。Claude Code 可按 §17 PR 顺序开始实施。**
**任何 v0.2 / v1.1 边界冲突在实施过程中需升版本号(v0.3 / v1.2)并保留 changelog,禁止 silent drift。**

---

## 19. 不在 v1 范围(再次明确)

- AccountRegistry / Email Gateway / SMS Gateway / Communication Gateway
- ResidentEpisodicMemory / Wiki 蒸馏管线 / 自动 distillation
- 完整 CapabilityPolicy 抽象基类 / 6 级 ActionRisk / 4 级 PolicyDecision
- Identity Substrate Ticket B 实现
- Capability Evolution System 实现 (任何 phase)
- task_learning
- ShellAdapter / ShellPolicy / FilesystemAdapter / FilesystemPolicy 具体类
- ResidentWorkspace 文件系统抽象
- Desktop v2 Linux/Windows 完整化
- CAPTCHA 自动求解 / WAF 伪装 / 代理池绕风控
- 营销 / 群发 / 主动联系陌生人
- 高风险无监督操作

---

## 20. Changelog

- **v1.1 final patch (2026-05-11, Kevin directive)** — Scheduling / phasing 全部撤下,blueprint 不替 Claude Code 做 planning。
- **v1.1 final (2026-05-11, signed off)** — GPT sign-off received with 2 non-blocking notes folded directly into v1.1; no v1.2 required:
  1. `CredentialLeaseStore` in-memory-only **HARD CONSTRAINT** written into class docstring (§7.2)
  2. `ContinuationRegistry` cleanup lifecycle invariant — new §8.4 documents `try / except / finally cleanup(ref)` 模式
  3. §15.2 I-2 加 static-grep test + lease-lost-after-restart integration test
  4. §15.2 I-6 加 5 个 cleanup 测试

  **Three-way sign-off:** Kevin / GPT / Claude all 2026-05-11. Ready for Claude Code execution.

- **v1.1 (2026-05-11)** — Post GPT final-pass revision. 6 blocking + 5 non-blocking 修订。
- **v1.0 (2026-05-11)** — Initial blueprint based on `lapwing_redesign_intent_v0.2.md`. 含完整 schema、API、Slice plan、acceptance criteria、open question 答案。
