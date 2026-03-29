"""应用装配容器：统一管理依赖注入与生命周期。"""

from __future__ import annotations

import logging
from pathlib import Path

from config.settings import (
    DATA_DIR,
    DB_PATH,
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
from src.core.heartbeat import HeartbeatEngine
from src.core.latency_monitor import LatencyMonitor
from src.heartbeat.actions.autonomous_browsing import AutonomousBrowsingAction
from src.heartbeat.actions.compaction_check import CompactionCheckAction
from src.heartbeat.actions.consolidation import MemoryConsolidationAction
from src.heartbeat.actions.interest_proactive import InterestProactiveAction
from src.heartbeat.actions.proactive import ProactiveMessageAction, ReminderDispatchAction
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

        self.brain = brain or LapwingBrain(db_path=self._db_path)
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

        self.api_server = api_server or LocalApiServer(
            brain=self.brain,
            event_bus=self.event_bus,
            task_view_store=self.task_view_store,
            latency_monitor=self.latency_monitor,
        )
        self.heartbeat: HeartbeatEngine | None = None
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

    async def start(self, *, bot=None) -> None:
        if self._started:
            return

        await self.prepare()

        if bot is not None:
            self.heartbeat = self._build_heartbeat(bot)
            self.heartbeat.start()

        await self.api_server.start()
        self._started = True
        logger.info("应用容器启动完成")

    async def shutdown(self) -> None:
        if self.heartbeat is not None:
            await self.heartbeat.shutdown()
            self.heartbeat = None

        await self.api_server.shutdown()

        if self.brain.interest_tracker:
            await self.brain.interest_tracker.shutdown()

        await self.brain.fact_extractor.shutdown()
        await self.brain.memory.close()
        self._started = False
        logger.info("应用容器资源清理完成")

    async def _configure_brain_dependencies(self) -> None:
        from src.agents.base import AgentRegistry
        from src.agents.browser import BrowserAgent
        from src.agents.coder import CoderAgent
        from src.agents.file_agent import FileAgent
        from src.agents.researcher import ResearcherAgent
        from src.agents.todo_agent import TodoAgent
        from src.agents.weather_agent import WeatherAgent
        from src.core.dispatcher import AgentDispatcher
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

        registry = AgentRegistry()
        registry.register(
            ResearcherAgent(
                memory=self.brain.memory,
                knowledge_manager=self.brain.knowledge_manager,
            )
        )
        registry.register(CoderAgent(memory=self.brain.memory, runtime=self.brain.task_runtime))
        registry.register(
            BrowserAgent(
                memory=self.brain.memory,
                knowledge_manager=self.brain.knowledge_manager,
            )
        )
        registry.register(FileAgent(memory=self.brain.memory, runtime=self.brain.task_runtime))
        registry.register(WeatherAgent())
        registry.register(TodoAgent(memory=self.brain.memory))

        self.brain.dispatcher = AgentDispatcher(
            registry=registry,
            router=self.brain.router,
            memory=self.brain.memory,
        )

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

    def _build_heartbeat(self, bot) -> HeartbeatEngine:
        heartbeat = HeartbeatEngine(brain=self.brain, bot=bot)
        heartbeat.registry.register(CompactionCheckAction())
        heartbeat.registry.register(ProactiveMessageAction())
        heartbeat.registry.register(ReminderDispatchAction())
        heartbeat.registry.register(AutonomousBrowsingAction())
        heartbeat.registry.register(InterestProactiveAction())
        heartbeat.registry.register(MemoryConsolidationAction())
        heartbeat.registry.register(SelfReflectionAction())
        heartbeat.registry.register(PromptEvolutionAction())
        return heartbeat
