"""应用装配容器：统一管理依赖注入与生命周期。"""

from __future__ import annotations

import logging
from pathlib import Path

from config.settings import (
    DATA_DIR,
    DB_PATH,
    EXPERIENCE_SKILLS_DIR,
    EXPERIENCE_SKILLS_ENABLED,
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
        self.reminder_scheduler: ReminderScheduler | None = None
        self.telegram_app = None

        self._prepared = False
        self._started = False

    async def prepare(self) -> None:
        if self._prepared:
            return

        await self.brain.init_db()
        await self._configure_brain_dependencies()
        self._prepared = True
        logger.info("应用容器依赖装配完成")

    async def start(self, *, send_fn=None) -> None:
        if self._started:
            return

        await self.prepare()

        if send_fn is not None:
            self.heartbeat = self._build_heartbeat(send_fn)
            self.heartbeat.start()
            self.reminder_scheduler = ReminderScheduler(
                memory=self.brain.memory,
                send_fn=send_fn,
                event_bus=self.event_bus,
            )
            await self.reminder_scheduler.start()
            self.brain.reminder_scheduler = self.reminder_scheduler

        await self.channel_manager.start_all()

        await self.api_server.start()

        # 将心跳引擎注入 API 状态，供 /api/heartbeat/status 使用
        if self.api_server._app is not None:
            self.api_server._app.state.heartbeat = self.heartbeat

        self._started = True
        logger.info("应用容器启动完成")

    async def shutdown(self) -> None:
        if self.reminder_scheduler is not None:
            await self.reminder_scheduler.shutdown()
            self.reminder_scheduler = None

        if self.heartbeat is not None:
            await self.heartbeat.shutdown()
            self.heartbeat = None

        await self.channel_manager.stop_all()

        await self.api_server.shutdown()

        if self.brain.interest_tracker:
            await self.brain.interest_tracker.shutdown()

        await self.brain.fact_extractor.shutdown()
        await self.brain.memory.close()
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

        self.brain.constitution_guard = ConstitutionGuard(self.brain.router)
        self.brain.tactical_rules = TacticalRules(self.brain.router)
        self.brain.evolution_engine = EvolutionEngine(
            self.brain.router, self.brain.constitution_guard
        )

        # 经验技能系统（Lapwing 自身积累的工作经验）
        if EXPERIENCE_SKILLS_ENABLED:
            from src.core.experience_skills import ExperienceSkillManager
            esm = ExperienceSkillManager(
                skills_dir=EXPERIENCE_SKILLS_DIR,
                traces_dir=SKILL_TRACES_DIR,
                router=self.brain.router,
            )
            esm.ensure_directories()
            esm.load_index()
            self.brain.experience_skill_manager = esm
            logger.info("经验技能系统已就绪")

        # Session 管理系统
        from config.settings import SESSION_ENABLED
        if SESSION_ENABLED:
            from src.core.session_manager import SessionManager
            sm = SessionManager(memory=self.brain.memory, db=self.brain.memory._db)
            await sm.init()
            self.brain.session_manager = sm
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

        # 任务流编排
        from src.core.task_flow import TaskFlowManager
        self.brain.task_flow_manager = TaskFlowManager()
        recovered = self.brain.task_flow_manager.load_pending_flows()
        if recovered:
            logger.info("恢复了 %d 个未完成任务流", len(recovered))

        # 回复质量检查（可选）
        from config.settings import QUALITY_CHECK_ENABLED
        if QUALITY_CHECK_ENABLED:
            from src.core.quality_checker import ReplyQualityChecker
            self.brain.quality_checker = ReplyQualityChecker(router=self.brain.router)
            logger.info("回复质量检查已就绪")

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

        return heartbeat
