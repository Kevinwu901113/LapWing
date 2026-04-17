"""应用装配容器：统一管理依赖注入与生命周期。"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from config.settings import (
    BROWSER_ENABLED,
    DATA_DIR,
    DB_PATH,
    PHASE0_MODE,
)
from src.api.event_bus import DesktopEventBus
from src.api.server import LocalApiServer
from src.app.task_view import TaskViewStore
from src.core.brain import LapwingBrain
from src.core.channel_manager import ChannelManager
from src.core.consciousness import ConsciousnessEngine
from src.core.dispatcher import Dispatcher
from src.core.durable_scheduler import DurableScheduler
from src.logging.state_mutation_log import MutationType, StateMutationLog

logger = logging.getLogger("lapwing.app.container")


def _resolve_git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parent.parent.parent,
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


class AppContainer:
    """应用容器：构建并持有核心对象，管理 start/shutdown。"""

    def __init__(
        self,
        *,
        db_path: Path = DB_PATH,
        data_dir: Path = DATA_DIR,
        brain: LapwingBrain | None = None,
        event_bus: DesktopEventBus | None = None,
        task_view_store: TaskViewStore | None = None,
        api_server: LocalApiServer | None = None,
    ) -> None:
        self._db_path = db_path
        self._data_dir = data_dir

        from src.core.model_config import ModelConfigManager
        _model_config = ModelConfigManager()
        self.brain = brain or LapwingBrain(db_path=self._db_path, model_config=_model_config)
        self.task_view_store = task_view_store or TaskViewStore()
        self.event_bus = event_bus or DesktopEventBus()
        self.event_bus.add_listener(self.task_view_store.ingest_event)
        self.brain.event_bus = self.event_bus

        from src.adapters.base import ChannelType
        from src.adapters.desktop_adapter import DesktopChannelAdapter
        self.channel_manager = ChannelManager()
        self._desktop_adapter = DesktopChannelAdapter()
        self.channel_manager.register(ChannelType.DESKTOP, self._desktop_adapter)
        self.brain.channel_manager = self.channel_manager

        self.api_server = api_server or LocalApiServer(
            brain=self.brain,
            event_bus=self.event_bus,
            task_view_store=self.task_view_store,
            channel_manager=self.channel_manager,
        )
        self.consciousness: ConsciousnessEngine | None = None
        self.durable_scheduler: DurableScheduler | None = None
        # 浏览器子系统（可选）
        self._browser_manager = None
        self._credential_vault = None
        self._browser_guard = None

        # Dispatcher — 内存 pub/sub 总线，给桌面端 SSE 和子系统实时广播用
        # (v2.0 Step 1: EventLogger/events_v2.db 持久化职责已移交给 StateMutationLog)
        self.dispatcher: Dispatcher | None = None

        # v2.0 Step 1: StateMutationLog — durable append-only log of state mutations
        self.mutation_log: StateMutationLog | None = None

        self._prepared = False
        self._started = False

    async def prepare(self) -> None:
        if self._prepared:
            return

        from src.core.vitals import init as init_vitals
        init_vitals(self._data_dir)

        await self.brain.init_db()

        # Dispatcher — 纯内存 pub/sub 总线（SSE 广播、子系统信号）
        self.dispatcher = Dispatcher()
        # 注入到主对话路径（无条件，AGENT_TEAM_ENABLED 与否都要）
        self.brain._dispatcher_ref = self.dispatcher
        # 注入到 API server（server.start() 时才实际使用）
        self.api_server._dispatcher = self.dispatcher
        logger.info("Dispatcher pub/sub 已初始化")

        # v2.0 Step 1: StateMutationLog — separate SQLite log for LLM/tool/iteration
        # mutations, independent from the legacy events_v2.db (which is scheduled
        # for archival in Step 1g). See Blueprint v2.0 §2.1.
        mutation_db = self._data_dir / "mutation_log.db"
        mutation_logs_dir = self._data_dir / "logs"
        self.mutation_log = StateMutationLog(mutation_db, logs_dir=mutation_logs_dir)
        await self.mutation_log.init()
        self.brain._mutation_log_ref = self.mutation_log
        self.brain.router.set_mutation_log(self.mutation_log)
        logger.info("StateMutationLog 已初始化：%s", mutation_db)

        # 浏览器子系统初始化（在依赖装配前启动，因为工具注册需要 browser_manager）
        if BROWSER_ENABLED and not PHASE0_MODE:
            await self._init_browser()

        await self._configure_brain_dependencies()
        self._prepared = True
        logger.info("应用容器依赖装配完成")

    async def start(self, *, send_fn=None) -> None:
        if self._started:
            return

        await self.prepare()

        if send_fn is not None and not PHASE0_MODE:
            from config.settings import CONSCIOUSNESS_ENABLED
            import asyncio as _asyncio

            if CONSCIOUSNESS_ENABLED:
                self.consciousness = ConsciousnessEngine(
                    brain=self.brain,
                    send_fn=send_fn,
                    dispatcher=self.dispatcher,
                )
                self.brain.consciousness_engine = self.consciousness
                await self.consciousness.start()

                # Phase 4: 连接 DurableScheduler → consciousness urgency queue
                if self.durable_scheduler is not None:
                    async def _on_reminder_fired(reminder):
                        self.consciousness.push_urgency({
                            "type": "reminder",
                            "content": reminder.content,
                            "reminder_id": reminder.reminder_id,
                        })
                    self.durable_scheduler.urgency_callback = _on_reminder_fired
                    self.durable_scheduler.send_fn = send_fn
                    self.durable_scheduler.brain = self.brain
                    self._durable_scheduler_task = _asyncio.create_task(
                        self.durable_scheduler.run_loop(),
                        name="durable-scheduler",
                    )
                    # 更新 PromptBuilder 的 reminder_source
                    if self.brain.prompt_builder is not None:
                        self.brain.prompt_builder.reminder_source = self.durable_scheduler
                    logger.info("DurableScheduler 循环已启动，已连接意识循环 urgency queue")
            else:
                # DurableScheduler 在非意识模式下也启动
                if self.durable_scheduler is not None:
                    self.durable_scheduler.send_fn = send_fn
                    self.durable_scheduler.brain = self.brain
                    self._durable_scheduler_task = _asyncio.create_task(
                        self.durable_scheduler.run_loop(),
                        name="durable-scheduler",
                    )
        elif PHASE0_MODE:
            logger.info("Phase 0 模式：跳过意识循环")

        await self.channel_manager.start_all()

        await self.api_server.start()

        # 将意识引擎注入 API 状态
        if self.api_server._app is not None:
            self.api_server._app.state.consciousness = self.consciousness

        if self.mutation_log is not None:
            try:
                await self.mutation_log.record(
                    MutationType.SYSTEM_STARTED,
                    {
                        "pid": os.getpid(),
                        "git_commit": _resolve_git_commit(),
                        "phase0_mode": PHASE0_MODE,
                        "reason": "normal_start",
                    },
                )
            except Exception:
                logger.warning("SYSTEM_STARTED mutation record failed", exc_info=True)

        self._started = True
        logger.info("应用容器启动完成")

    async def shutdown(self) -> None:
        import asyncio as _asyncio

        # DurableScheduler shutdown (Phase 4)
        if self.durable_scheduler is not None:
            await self.durable_scheduler.stop()
            if hasattr(self, "_durable_scheduler_task") and self._durable_scheduler_task is not None:
                self._durable_scheduler_task.cancel()
                try:
                    await self._durable_scheduler_task
                except _asyncio.CancelledError:
                    pass
            self.durable_scheduler = None

        # Consciousness engine shutdown
        if self.consciousness is not None:
            await self.consciousness.stop()
            self.consciousness = None

        # API 先停，不再接受新请求
        await self.api_server.shutdown()

        # Channel 后停，处理完在途消息
        await self.channel_manager.stop_all()

        # VLM 客户端关闭
        if hasattr(self, "_vlm_client") and self._vlm_client is not None:
            try:
                await self._vlm_client.close()
            except Exception:
                pass

        # 浏览器子系统关闭
        if self._browser_manager is not None:
            try:
                await self._browser_manager.stop()
                logger.info("浏览器子系统已关闭")
            except Exception:
                logger.warning("浏览器关闭异常", exc_info=True)

        # EmbeddingWorker 后台任务取消
        if hasattr(self, "_embedding_task") and self._embedding_task is not None:
            self._embedding_task.cancel()
            try:
                await self._embedding_task
            except _asyncio.CancelledError:
                pass

        # v2.0 Step 1: 写入 SYSTEM_STOPPED 并关闭 mutation_log
        if self.mutation_log is not None:
            try:
                await self.mutation_log.record(
                    MutationType.SYSTEM_STOPPED,
                    {"pid": os.getpid(), "reason": "normal_shutdown"},
                )
            except Exception:
                logger.warning("SYSTEM_STOPPED mutation record failed", exc_info=True)
            try:
                await self.mutation_log.close()
            except Exception:
                logger.warning("mutation_log close failed", exc_info=True)
            self.mutation_log = None

        await self.brain.memory.close()
        self._started = False
        logger.info("应用容器资源清理完成")

    async def _configure_brain_dependencies(self) -> None:
        if PHASE0_MODE:
            logger.info("Phase 0 模式 (%s)：跳过大部分依赖装配", PHASE0_MODE)
            return

        from src.memory.vector_store import VectorStore

        self.brain.vector_store = VectorStore(self._data_dir / "chroma")

        # PromptBuilder（Phase 2：4 层）
        from src.core.prompt_builder import PromptBuilder
        from config.settings import IDENTITY_DIR
        self.brain.prompt_builder = PromptBuilder(
            soul_path=IDENTITY_DIR / "soul.md",
            constitution_path=IDENTITY_DIR / "constitution.md",
            voice_path="lapwing_voice",
            reminder_source=self.brain.memory,
        )

        # SoulManager + soul 工具
        from src.core.soul_manager import SoulManager
        self._soul_manager = SoulManager(
            soul_path=IDENTITY_DIR / "soul.md",
            snapshot_dir=IDENTITY_DIR / "soul_snapshots",
        )
        from src.tools.soul_tools import register_soul_tools
        register_soul_tools(self.brain.tool_registry, self._soul_manager)
        # 暴露给 API server 使用
        self.brain._soul_manager_ref = self._soul_manager

        # Phase 3: 记忆树 + 向量库 + 工具
        from src.memory.note_store import NoteStore
        from src.memory.vector_store import MemoryVectorStore
        note_store = NoteStore()  # 默认 data/memory/notes/
        self.brain._note_store = note_store
        memory_vector_store = MemoryVectorStore(persist_dir=str(self._data_dir / "chroma_memory"))
        self.brain._memory_vector_store = memory_vector_store

        # EmbeddingWorker（后台任务）
        import asyncio
        from src.memory.embedding_worker import EmbeddingWorker
        embedding_worker = EmbeddingWorker(note_store, memory_vector_store)
        self._embedding_task = asyncio.create_task(embedding_worker.run_loop(interval=60))

        # 注册 Phase 3 记忆工具
        from src.tools.memory_tools_v2 import register_memory_tools_v2
        register_memory_tools_v2(self.brain.tool_registry)
        logger.info("Phase 3 记忆系统已装配（NoteStore + MemoryVectorStore + 9 工具）")

        # ── Agent Team 系统（Phase 6） ──────────────────────────────────
        from config.settings import AGENT_TEAM_ENABLED
        if AGENT_TEAM_ENABLED:
            from src.agents.registry import AgentRegistry
            from src.agents.team_lead import TeamLead
            from src.agents.researcher import Researcher
            from src.agents.coder import Coder
            from src.tools.agent_tools import register_agent_tools
            from src.tools.workspace_tools import (
                ws_file_read_executor,
                ws_file_write_executor,
                ws_file_list_executor,
            )

            agent_registry = AgentRegistry()

            # services 供 Agent 的 tool loop 传递给 ToolExecutionContext
            agent_services = {
                "agent_registry": agent_registry,
                "dispatcher": self.dispatcher,
            }

            # 注册具体 Agent
            agent_registry.register(
                "team_lead",
                TeamLead.create(
                    self.brain.router,
                    self.brain.tool_registry,
                    self.dispatcher,
                    services=agent_services,
                ),
            )
            agent_registry.register(
                "researcher",
                Researcher.create(
                    self.brain.router,
                    self.brain.tool_registry,
                    self.dispatcher,
                    services=agent_services,
                ),
            )
            agent_registry.register(
                "coder",
                Coder.create(
                    self.brain.router,
                    self.brain.tool_registry,
                    self.dispatcher,
                    services=agent_services,
                ),
            )

            # 注册 Agent 工具（delegate + delegate_to_agent）
            register_agent_tools(self.brain.tool_registry)

            # 注册 workspace 工具（供 Coder 使用，visibility=internal 不暴露给主聊天）
            from src.tools.types import ToolSpec as _TS
            self.brain.tool_registry.register(_TS(
                name="ws_file_read",
                description="读取工作区文件",
                json_schema={"type": "object", "properties": {
                    "path": {"type": "string", "description": "相对路径"},
                }, "required": ["path"]},
                executor=ws_file_read_executor,
                capability="agent",
                visibility="internal",
            ))
            self.brain.tool_registry.register(_TS(
                name="ws_file_write",
                description="写入工作区文件",
                json_schema={"type": "object", "properties": {
                    "path": {"type": "string", "description": "相对路径"},
                    "content": {"type": "string", "description": "文件内容"},
                }, "required": ["path", "content"]},
                executor=ws_file_write_executor,
                capability="agent",
                visibility="internal",
            ))
            self.brain.tool_registry.register(_TS(
                name="ws_file_list",
                description="列出工作区文件",
                json_schema={"type": "object", "properties": {
                    "path": {"type": "string", "description": "相对路径", "default": "."},
                }, "required": []},
                executor=ws_file_list_executor,
                capability="agent",
                visibility="internal",
            ))

            # 注入 services（dispatcher 已在 prepare() 中无条件设置）
            self.brain._agent_registry = agent_registry

            # 创建工作区目录
            Path("data/agent_workspace").mkdir(parents=True, exist_ok=True)
            Path("data/agent_workspace/patches").mkdir(parents=True, exist_ok=True)

            logger.info("Agent Team 系统已就绪（%d agents）", len(agent_registry.list_names()))

        # Phase 4: DurableScheduler（初始化但不启动循环——循环在 start() 中启动）
        self.durable_scheduler = DurableScheduler(
            db_path=self._db_path,
            dispatcher=self.dispatcher,
        )
        self.brain._durable_scheduler_ref = self.durable_scheduler

        # Phase 4: 注册个人工具
        from src.tools.personal_tools import register_personal_tools
        personal_services = {
            "channel_manager": self.channel_manager,
            "scheduler": self.durable_scheduler,
            "browser_manager": self._browser_manager,
            "vlm": getattr(self, "_vlm_client", None),
            "owner_qq_id": getattr(__import__("config.settings", fromlist=["QQ_KEVIN_ID"]), "QQ_KEVIN_ID", ""),
        }
        register_personal_tools(self.brain.tool_registry, personal_services)
        logger.info("Phase 4 个人工具已注册")

        # Research 子系统：search + fetch + refine 封装成 research(question)
        from config.settings import BOCHA_API_KEY, TAVILY_API_KEY, TAVILY_COUNTRY
        from src.research.backends.bocha import BochaBackend
        from src.research.backends.tavily import TavilyBackend
        from src.research.engine import ResearchEngine
        from src.research.fetcher import SmartFetcher
        from src.research.refiner import Refiner
        from src.research.scope_router import ScopeRouter
        from src.tools.research_tool import register_research_tool

        self.brain._research_engine = ResearchEngine(
            scope_router=ScopeRouter(),
            tavily_backend=TavilyBackend(api_key=TAVILY_API_KEY, country=TAVILY_COUNTRY),
            bocha_backend=BochaBackend(api_key=BOCHA_API_KEY),
            fetcher=SmartFetcher(browser_manager=self._browser_manager),
            refiner=Refiner(llm_router=self.brain.router),
        )
        register_research_tool(self.brain.tool_registry)
        logger.info("Research 子系统已装配（research 工具 + ResearchEngine）")

        # Phase 4: 注册 DurableScheduler 提醒工具
        from src.core.durable_scheduler import DURABLE_SCHEDULER_EXECUTORS
        from src.tools.types import ToolSpec
        self.brain.tool_registry.register(ToolSpec(
            name="set_reminder",
            description=(
                "设置提醒。指定时间和内容。"
                "例如：time='2026-04-17 09:00', content='查看邮件'"
            ),
            json_schema={
                "type": "object",
                "properties": {
                    "time": {
                        "type": "string",
                        "description": "提醒时间，格式 YYYY-MM-DD HH:MM（台北时间）",
                    },
                    "content": {
                        "type": "string",
                        "description": "提醒内容",
                    },
                    "repeat": {
                        "type": "string",
                        "enum": ["daily", "weekly", "interval"],
                        "description": "重复方式（可选）",
                    },
                    "interval_minutes": {
                        "type": "integer",
                        "description": "仅 interval 类型：间隔分钟数",
                    },
                    "execution_mode": {
                        "type": "string",
                        "enum": ["notify", "agent"],
                        "description": "notify=发文字提醒（默认）; agent=执行任务并发送结果",
                    },
                },
                "required": ["time", "content"],
            },
            executor=DURABLE_SCHEDULER_EXECUTORS["set_reminder"],
            capability="schedule",
            risk_level="medium",
        ))
        self.brain.tool_registry.register(ToolSpec(
            name="view_reminders",
            description="查看所有未触发的提醒。",
            json_schema={"type": "object", "properties": {}},
            executor=DURABLE_SCHEDULER_EXECUTORS["view_reminders"],
            capability="schedule",
            risk_level="low",
        ))
        self.brain.tool_registry.register(ToolSpec(
            name="cancel_reminder",
            description="取消一条提醒。",
            json_schema={
                "type": "object",
                "properties": {
                    "reminder_id": {
                        "type": "string",
                        "description": "提醒 ID（从 view_reminders 获取）",
                    },
                },
                "required": ["reminder_id"],
            },
            executor=DURABLE_SCHEDULER_EXECUTORS["cancel_reminder"],
            capability="schedule",
            risk_level="medium",
        ))
        logger.info("Phase 4 DurableScheduler + 提醒工具已装配")

    async def _init_browser(self) -> None:
        """初始化浏览器子系统组件。"""
        from src.core.browser_manager import BrowserManager

        self._browser_guard = None  # BrowserGuard 已移除（Phase 1 减法）
        self._browser_manager = BrowserManager()
        await self._browser_manager.start()

        # CredentialVault 需要 CREDENTIAL_VAULT_KEY 环境变量，缺失时跳过
        try:
            from src.core.credential_vault import CredentialVault
            from config.settings import CREDENTIAL_VAULT_PATH
            self._credential_vault = CredentialVault(vault_path=CREDENTIAL_VAULT_PATH)
        except ValueError:
            logger.warning("CREDENTIAL_VAULT_KEY 未设置，browser_login 不可用")
            self._credential_vault = None

        # 注册浏览器工具到 brain 的 tool_registry
        from src.tools.browser_tools import register_browser_tools
        register_browser_tools(
            registry=self.brain.tool_registry,
            browser_manager=self._browser_manager,
            credential_vault=self._credential_vault,
            browser_guard=self._browser_guard,
            event_bus=self.event_bus,
        )
        self.brain.browser_manager = self._browser_manager
        self.brain.task_runtime.set_browser_guard(self._browser_guard)
        self._browser_manager.set_router(self.brain.router)
        self._browser_manager.set_event_bus(self.event_bus)
        self._browser_manager.set_browser_guard(self._browser_guard)

        # MiniMax VLM 客户端（浏览器视觉理解的替代方案）
        from config.settings import MINIMAX_VLM_ENABLED, MINIMAX_VLM_API_KEY, MINIMAX_VLM_HOST
        if MINIMAX_VLM_ENABLED and MINIMAX_VLM_API_KEY:
            from src.core.minimax_vlm import MiniMaxVLM
            self._vlm_client = MiniMaxVLM(api_key=MINIMAX_VLM_API_KEY, api_host=MINIMAX_VLM_HOST)
            self._browser_manager.set_vlm_client(self._vlm_client)
            self.brain._vlm_client_ref = self._vlm_client
            logger.info("MiniMax VLM 客户端已注入浏览器子系统")

        logger.info("浏览器子系统已就绪")
