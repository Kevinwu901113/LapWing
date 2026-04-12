"""应用装配容器：统一管理依赖注入与生命周期。"""

from __future__ import annotations

import logging
from pathlib import Path

from config.settings import (
    BROWSER_ENABLED,
    DATA_DIR,
    DB_PATH,
    EXPERIENCE_SKILLS_DIR,
    EXPERIENCE_SKILLS_ENABLED,
    INCIDENT_ENABLED,
    SKILL_TRACES_DIR,
    SKILLS_BUNDLED_DIR,
    SKILLS_ENABLED,
    SKILLS_EXTRA_DIRS,
    SKILLS_MANAGED_DIR,
    SKILLS_WORKSPACE_DIR,
)
from src.api.event_bus import DesktopEventBus
from src.api.server import LocalApiServer
from src.app.task_view import TaskViewStore
from src.core.brain import LapwingBrain
from src.core.channel_manager import ChannelManager
from src.core.consciousness import ConsciousnessEngine
from src.core.heartbeat import HeartbeatEngine
from src.core.reminder_scheduler import ReminderScheduler
from src.core.latency_monitor import LatencyMonitor
from src.heartbeat.actions.autonomous_browsing import AutonomousBrowsingAction
from src.heartbeat.actions.compaction_check import CompactionCheckAction
from src.heartbeat.actions.consolidation import MemoryConsolidationAction
from src.heartbeat.actions.interest_proactive import InterestProactiveAction
from src.heartbeat.actions.proactive import ProactiveMessageAction
from src.heartbeat.actions.prompt_evolution import PromptEvolutionAction
from src.heartbeat.actions.self_reflection import SelfReflectionAction

logger = logging.getLogger("lapwing.app.container")


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
        self.latency_monitor = LatencyMonitor()
        self.event_bus = event_bus or DesktopEventBus()
        if hasattr(self.event_bus, "set_latency_monitor"):
            self.event_bus.set_latency_monitor(self.latency_monitor)
        self.event_bus.add_listener(self.task_view_store.ingest_event)
        self.brain.event_bus = self.event_bus
        runtime = getattr(self.brain, "task_runtime", None)
        if runtime is not None and hasattr(runtime, "set_latency_monitor"):
            runtime.set_latency_monitor(self.latency_monitor)

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
            latency_monitor=self.latency_monitor,
            channel_manager=self.channel_manager,
        )
        self.heartbeat: HeartbeatEngine | None = None
        self.consciousness: ConsciousnessEngine | None = None
        self.reminder_scheduler: ReminderScheduler | None = None
        self.telegram_app = None

        # 浏览器子系统（可选）
        self._browser_manager = None
        self._credential_vault = None
        self._browser_guard = None

        self.incident_manager = None

        self._prepared = False
        self._started = False

    async def prepare(self) -> None:
        if self._prepared:
            return

        from src.core.vitals import init as init_vitals
        init_vitals(self._data_dir)

        await self.brain.init_db()

        # 浏览器子系统初始化（在依赖装配前启动，因为工具注册需要 browser_manager）
        if BROWSER_ENABLED:
            await self._init_browser()

        await self._configure_brain_dependencies()
        self._prepared = True
        logger.info("应用容器依赖装配完成")
        from src.logging.event_logger import events
        events.log("system", "startup", message="Lapwing 启动完成")

    async def start(self, *, send_fn=None) -> None:
        if self._started:
            return

        await self.prepare()

        if send_fn is not None:
            from config.settings import CONSCIOUSNESS_ENABLED, HEARTBEAT_ENABLED

            self.reminder_scheduler = ReminderScheduler(
                memory=self.brain.memory,
                send_fn=send_fn,
                event_bus=self.event_bus,
            )
            self.brain.reminder_scheduler = self.reminder_scheduler

            if CONSCIOUSNESS_ENABLED:
                self.consciousness = ConsciousnessEngine(
                    brain=self.brain,
                    send_fn=send_fn,
                    reminder_scheduler=self.reminder_scheduler,
                    incident_manager=self.incident_manager,
                )
                self.brain.consciousness_engine = self.consciousness
                await self.consciousness.start()
            elif HEARTBEAT_ENABLED:
                self.heartbeat = self._build_heartbeat(send_fn)
                self.heartbeat.start()
                await self.reminder_scheduler.start()

        await self.channel_manager.start_all()

        await self.api_server.start()

        # 将心跳引擎注入 API 状态，供 /api/heartbeat/status 使用
        if self.api_server._app is not None:
            self.api_server._app.state.heartbeat = self.heartbeat
            self.api_server._app.state.consciousness = self.consciousness

        self._started = True
        logger.info("应用容器启动完成")

    async def shutdown(self) -> None:
        # Consciousness engine shutdown (must come first — it owns reminder_scheduler)
        if self.consciousness is not None:
            await self.consciousness.stop()
            self.consciousness = None
            self.reminder_scheduler = None  # already shut down by consciousness.stop()

        if self.reminder_scheduler is not None:
            await self.reminder_scheduler.shutdown()
            self.reminder_scheduler = None

        if self.heartbeat is not None:
            await self.heartbeat.shutdown()
            self.heartbeat = None

        await self.channel_manager.stop_all()

        # 浏览器子系统关闭
        if self._browser_manager is not None:
            try:
                await self._browser_manager.stop()
                logger.info("浏览器子系统已关闭")
            except Exception:
                logger.warning("浏览器关闭异常", exc_info=True)

        await self.api_server.shutdown()

        if self.brain.interest_tracker:
            await self.brain.interest_tracker.shutdown()

        await self.brain.fact_extractor.shutdown()
        await self.brain.memory.close()
        from src.logging.event_logger import events, get_event_logger
        events.log("system", "shutdown", message="Lapwing 正在关闭")
        get_event_logger().close()
        self._started = False
        logger.info("应用容器资源清理完成")

    async def _configure_brain_dependencies(self) -> None:
        from src.core.knowledge_manager import KnowledgeManager
        from src.core.skills import SkillManager
        from src.core.self_reflection import SelfReflection
        from src.memory.interest_tracker import InterestTracker
        from src.memory.vector_store import VectorStore

        self.brain.knowledge_manager = KnowledgeManager()
        self.brain.vector_store = VectorStore(self._data_dir / "chroma")
        self.brain.skill_manager = SkillManager(
            enabled=SKILLS_ENABLED,
            workspace_dir=Path(SKILLS_WORKSPACE_DIR),
            managed_dir=Path(SKILLS_MANAGED_DIR),
            bundled_dir=Path(SKILLS_BUNDLED_DIR),
            extra_dirs=[Path(item) for item in SKILLS_EXTRA_DIRS],
        )
        self.brain.skill_manager.reload()

        self.brain.interest_tracker = InterestTracker(
            memory=self.brain.memory,
            router=self.brain.router,
        )
        self.brain.self_reflection = SelfReflection(
            memory=self.brain.memory,
            router=self.brain.router,
        )

        from src.core.constitution_guard import ConstitutionGuard
        from src.core.tactical_rules import TacticalRules
        from src.core.evolution_engine import EvolutionEngine

        # Incident 管理系统（可选）
        if INCIDENT_ENABLED:
            from src.core.incident_manager import IncidentManager
            self.incident_manager = IncidentManager(
                send_notification_fn=self._send_notification_to_owner,
            )
            self.brain.incident_manager = self.incident_manager
            self.brain.task_runtime.set_incident_manager(self.incident_manager)
            logger.info("Incident 管理系统已就绪")

        self.brain.constitution_guard = ConstitutionGuard(self.brain.router)
        self.brain.tactical_rules = TacticalRules(
            self.brain.router,
            incident_manager=self.incident_manager,
        )
        self.brain.evolution_engine = EvolutionEngine(
            self.brain.router, self.brain.constitution_guard
        )

        # 经验技能系统（Lapwing 自身积累的工作经验）
        if EXPERIENCE_SKILLS_ENABLED:
            from src.core.experience_skills import ExperienceSkillManager
            # 将当前注册的工具名传给 ESM，用于条件激活过滤（Pattern 2）
            available_tools = {
                tool.name for tool in self.brain.tool_registry.list_tools(include_internal=True)
            }
            esm = ExperienceSkillManager(
                skills_dir=EXPERIENCE_SKILLS_DIR,
                traces_dir=SKILL_TRACES_DIR,
                router=self.brain.router,
                available_tools=available_tools,
            )
            esm.ensure_directories()
            esm.load_index()
            self.brain.experience_skill_manager = esm
            logger.info("经验技能系统已就绪（可用工具 %d 个）", len(available_tools))

        # Session 管理系统
        from config.settings import SESSION_ENABLED
        if SESSION_ENABLED:
            from src.core.session_manager import SessionManager
            sm = SessionManager(memory=self.brain.memory, db=self.brain.memory._db)
            await sm.init()
            self.brain.session_manager = sm
            # 注入到 Compactor 以支持 Session Lineage
            self.brain.compactor._session_manager = sm
            logger.info("Session 系统已就绪")

        # 记忆索引（始终启用）
        from src.memory.memory_index import MemoryIndex
        self.brain.memory_index = MemoryIndex()
        self.brain.task_runtime.set_memory_index(self.brain.memory_index)
        logger.info("记忆索引已就绪（%d 条目）", len(self.brain.memory_index.all_entries()))

        # 自动记忆提取（Wave 1）
        from config.settings import AUTO_MEMORY_EXTRACT_ENABLED
        if AUTO_MEMORY_EXTRACT_ENABLED:
            from src.memory.auto_extractor import AutoMemoryExtractor
            self.brain.auto_memory_extractor = AutoMemoryExtractor(
                router=self.brain.router,
                memory_index=self.brain.memory_index,
            )
            logger.info("自动记忆提取已就绪")
            # 注入到 Compactor 以支持压缩前记忆冲刷
            self.brain.compactor._auto_memory_extractor = self.brain.auto_memory_extractor

        # 任务流编排
        from src.core.task_flow import TaskFlowManager
        self.brain.task_flow_manager = TaskFlowManager()
        recovered = self.brain.task_flow_manager.load_pending_flows()
        if recovered:
            logger.info("恢复了 %d 个未完成任务流", len(recovered))

        # 子 Agent 委托系统（可选）
        from config.settings import DELEGATION_ENABLED
        if DELEGATION_ENABLED:
            from src.core.delegation import DelegationManager
            self.brain.delegation_manager = DelegationManager(
                router=self.brain.router,
                tool_registry=self.brain.tool_registry,
                event_bus=self.event_bus,
            )
            logger.info("子 Agent 委托系统已就绪")

        # Agent Team 系统（可选，新架构）
        from config.settings import AGENT_TEAM_ENABLED
        if AGENT_TEAM_ENABLED:
            from src.core.agent_registry import AgentRegistry
            from src.core.agent_dispatcher import AgentDispatcher
            agent_registry = AgentRegistry()
            self.brain.agent_registry = agent_registry
            self.brain.agent_dispatcher = AgentDispatcher(
                registry=agent_registry,
                task_runtime=self.brain.task_runtime,
            )
            logger.info("Agent Team 系统已就绪")

        # 回复质量检查（可选）
        from config.settings import QUALITY_CHECK_ENABLED
        if QUALITY_CHECK_ENABLED:
            from src.core.quality_checker import ReplyQualityChecker
            self.brain.quality_checker = ReplyQualityChecker(
                router=self.brain.router,
                incident_manager=self.incident_manager,
            )
            logger.info("回复质量检查已就绪")

        # 中间进度汇报（可选）
        from config.settings import PROGRESS_REPORT_ENABLED
        if PROGRESS_REPORT_ENABLED:
            self.brain.task_runtime.set_progress_enabled(True)
            logger.info("中间进度汇报已就绪")

        # 未完成任务恢复（可选）
        from config.settings import TASK_RESUMPTION_ENABLED
        if TASK_RESUMPTION_ENABLED:
            from src.core.pending_task import PendingTaskStore
            pending_store = PendingTaskStore(self._data_dir / "pending_tasks.json")
            self.brain.pending_task_store = pending_store
            self.brain.task_runtime.set_pending_task_store(pending_store)
            logger.info("未完成任务恢复已就绪")

    async def _init_browser(self) -> None:
        """初始化浏览器子系统组件。"""
        from src.core.browser_manager import BrowserManager
        from src.guards.browser_guard import BrowserGuard

        self._browser_guard = BrowserGuard()
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
        logger.info("浏览器子系统已就绪")

    def _build_heartbeat(self, send_fn) -> HeartbeatEngine:
        from src.heartbeat.actions.session_reaper import SessionReaperAction
        heartbeat = HeartbeatEngine(brain=self.brain, send_fn=send_fn)
        heartbeat.registry.register(CompactionCheckAction())
        heartbeat.registry.register(ProactiveMessageAction())
        heartbeat.registry.register(AutonomousBrowsingAction())
        heartbeat.registry.register(InterestProactiveAction())
        heartbeat.registry.register(MemoryConsolidationAction())
        heartbeat.registry.register(SelfReflectionAction())
        heartbeat.registry.register(PromptEvolutionAction())
        heartbeat.registry.register(SessionReaperAction())
        # Wave 1 actions
        from config.settings import AUTO_MEMORY_EXTRACT_ENABLED
        if AUTO_MEMORY_EXTRACT_ENABLED:
            from src.heartbeat.actions.auto_memory import AutoMemoryAction
            heartbeat.registry.register(AutoMemoryAction())

        # 记忆维护 + 任务通知
        from src.heartbeat.actions.memory_maintenance import MemoryMaintenanceAction
        from src.heartbeat.actions.task_notification import TaskNotificationAction
        heartbeat.registry.register(MemoryMaintenanceAction())
        heartbeat.registry.register(TaskNotificationAction())

        # 系统健康监控
        from src.heartbeat.actions.system_health import SystemHealthAction
        heartbeat.registry.register(SystemHealthAction())

        # 未完成任务恢复
        from config.settings import TASK_RESUMPTION_ENABLED
        if TASK_RESUMPTION_ENABLED:
            from src.heartbeat.actions.task_resumption import TaskResumptionAction
            heartbeat.registry.register(TaskResumptionAction())

        return heartbeat

    async def _send_notification_to_owner(self, text: str) -> None:
        """通过消息通道通知 Kevin。由 IncidentManager 在 wont_fix 时调用。"""
        try:
            if self.channel_manager:
                await self.channel_manager.send_to_owner(text)
        except Exception:
            logger.debug("通知 owner 失败", exc_info=True)
