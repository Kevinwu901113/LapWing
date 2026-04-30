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
from src.config import get_settings
from src.core.brain import LapwingBrain
from src.core.browser_guard import BrowserGuard
from src.core.channel_manager import ChannelManager
from src.core.dispatcher import Dispatcher
from src.core.durable_scheduler import DurableScheduler
from src.core.attention import AttentionManager
from src.core.event_queue import EventQueue
from src.core.commitments import CommitmentStore
from src.core.inner_tick_scheduler import InnerTickScheduler
from src.core.main_loop import MainLoop
from src.core.maintenance_timer import MaintenanceTimer
from src.core.proactive_message_gate import ProactiveMessageGate
from src.core.trajectory_store import TrajectoryStore
from src.logging.state_mutation_log import MutationType, StateMutationLog

logger = logging.getLogger("lapwing.app.container")


def _wire_trajectory_to_dispatcher(trajectory_store, dispatcher) -> None:
    """Register a listener that forwards every trajectory append to Dispatcher
    as a `trajectory_appended` event matching the shape served by the
    /api/v2/life/timeline endpoint."""
    if trajectory_store is None or dispatcher is None:
        return

    async def _forward(entry) -> None:
        text = ""
        if isinstance(entry.content, dict):
            text = (
                entry.content.get("text")
                or entry.content.get("message")
                or entry.content.get("summary")
                or ""
            )
        payload = {
            "kind": entry.entry_type,
            "timestamp": entry.timestamp,
            "id": f"traj_{entry.id}",
            "content": text,
            "metadata": {
                "source_chat_id": entry.source_chat_id,
                "actor": entry.actor,
                "related_iteration_id": entry.related_iteration_id,
            },
        }
        try:
            await dispatcher.submit("trajectory_appended", payload, actor=entry.actor or "system")
        except Exception:
            logger.warning("trajectory_appended dispatcher.submit failed", exc_info=True)

    trajectory_store.add_on_append_listener(_forward)


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
    except Exception as e:
        logging.getLogger("lapwing.app.container").debug("Git 版本检测失败: %s", e)
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
        from src.core.intent_router import IntentRouter
        self.intent_router = IntentRouter(llm_router=self.brain.router)
        self.brain.intent_router = self.intent_router
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

        # event_queue must already exist before LocalApiServer is built so
        # the desktop /ws/chat route can enqueue MessageEvent on it.
        self.event_queue: EventQueue = EventQueue()

        # mutation_log is wired into the API server lazily — see prepare()
        # where the StateMutationLog instance is constructed. The server
        # creates the FastAPI app at start() time, by which point
        # prepare() will have populated self.mutation_log.
        self.api_server = api_server or LocalApiServer(
            brain=self.brain,
            event_bus=self.event_bus,
            task_view_store=self.task_view_store,
            channel_manager=self.channel_manager,
            event_queue=self.event_queue,
        )
        self.maintenance_timer: MaintenanceTimer | None = None
        self.durable_scheduler: DurableScheduler | None = None
        # 浏览器子系统（可选）
        self._browser_manager = None
        self._credential_vault = None
        self._browser_guard: BrowserGuard | None = None

        # ProactiveMessageGate — rate limit / quiet-hours / urgent bypass
        # for proactive send_message calls. Built unconditionally so any
        # background path (inner ticks, reminders, compose_proactive) can
        # consult it. Direct chat replies use bare model text and do not
        # reach send_message, so this gate never throttles user replies.
        _s = get_settings()
        self.proactive_message_gate = ProactiveMessageGate.from_settings(
            _s.proactive_messages,
        )
        self.brain._proactive_message_gate_ref = self.proactive_message_gate

        # Dispatcher — 内存 pub/sub 总线，给桌面端 SSE 和子系统实时广播用。
        # 持久化由 StateMutationLog 负责；dispatcher 只是 live stream。
        self.dispatcher: Dispatcher | None = None

        # v2.0 Step 1: StateMutationLog — durable append-only log of state mutations
        self.mutation_log: StateMutationLog | None = None

        # v2.0 Step 2: AttentionManager — in-memory focus state, event-sourced
        self.attention_manager: AttentionManager | None = None

        # v2.0 Step 2: TrajectoryStore — cross-channel behaviour timeline,
        # dual-written alongside the legacy conversations table during the
        # sub-phase-A window; becomes read-side truth in sub-phase B.
        self.trajectory_store: TrajectoryStore | None = None
        self.focus_manager = None

        # v2.0 Step 5: CommitmentStore — durable record of Lapwing's
        # outstanding promises. Wired into brain services so the
        # commit/fulfill/abandon_promise tools can write to it, and into
        # StateViewBuilder so inner ticks see open + overdue commitments.
        self.commitment_store: CommitmentStore | None = None

        # v2.0 Step 4: MainLoop — single runtime driver. event_queue was
        # constructed above (so LocalApiServer could pick it up). The
        # loop itself starts in start() once brain wiring is complete.
        self.main_loop: MainLoop | None = None
        self._main_loop_task = None

        # v2.0 Step 4 M3: InnerTickScheduler replaces ConsciousnessEngine's
        # built-in timer. Started in start() alongside MainLoop.
        self.inner_tick_scheduler: InnerTickScheduler | None = None

        # ProxyRouter — per-domain proxy/direct routing with adaptive learning
        from src.core.proxy_router import ProxyRouter
        self.proxy_router: ProxyRouter | None = None

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

        # StateMutationLog — 独立 SQLite 文件记录 LLM/tool/iteration/system
        # 生命周期的状态变更。与 lapwing.db 的业务表分离。见 Blueprint v2.0 §2.1。
        mutation_db = self._data_dir / "mutation_log.db"
        mutation_logs_dir = self._data_dir / "logs"
        self.mutation_log = StateMutationLog(mutation_db, logs_dir=mutation_logs_dir)
        await self.mutation_log.init()
        self.brain._mutation_log_ref = self.mutation_log
        self.brain.router.set_mutation_log(self.mutation_log)
        # Step 4 M5: SSE subscribes to mutation_log via the API server.
        self.api_server._mutation_log = self.mutation_log
        logger.info("StateMutationLog 已初始化：%s", mutation_db)

        self.trajectory_store = TrajectoryStore(
            self._data_dir / "lapwing.db", self.mutation_log,
        )
        await self.trajectory_store.init()
        self.brain.trajectory_store = self.trajectory_store
        _wire_trajectory_to_dispatcher(self.brain.trajectory_store, self.dispatcher)
        logger.info("TrajectoryStore 已初始化（dual-write + read-path wired）")

        # v2.0 Step 5: CommitmentStore — same lapwing.db, separate aiosqlite
        # connection. brain._commitment_store_ref 让 brain._complete_chat 把
        # 它放到 services dict，三个 commit/fulfill/abandon_promise 工具就能拿到。
        self.commitment_store = CommitmentStore(
            self._data_dir / "lapwing.db", self.mutation_log,
        )
        await self.commitment_store.init()
        self.brain._commitment_store_ref = self.commitment_store
        logger.info("CommitmentStore 已初始化")

        # v2.0 Step 2: AttentionManager — focus singleton, recovers state from
        # mutation_log's most recent ATTENTION_CHANGED at boot.
        self.attention_manager = AttentionManager(self.mutation_log)
        await self.attention_manager.initialize()
        self.brain.attention_manager = self.attention_manager
        logger.info("AttentionManager 已初始化")

        # ProxyRouter — 按域名自适应选择代理或直连
        from src.core.proxy_router import ProxyRouter
        from config.settings import PROXY_SERVER, PROXY_DEFAULT_STRATEGY
        self.proxy_router = ProxyRouter(
            server=PROXY_SERVER,
            default_strategy=PROXY_DEFAULT_STRATEGY,
            data_dir=self._data_dir / "proxy",
        )
        logger.info(
            "ProxyRouter 已初始化 (server=%s, default=%s)",
            PROXY_SERVER or "disabled",
            PROXY_DEFAULT_STRATEGY,
        )

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

        # v2.0 Step 4 M3: build InnerTickScheduler first so DurableScheduler
        # can wire its urgency callback into it before kicking off.
        import asyncio as _asyncio
        self.inner_tick_scheduler = InnerTickScheduler(self.event_queue)
        self.brain.inner_tick_scheduler = self.inner_tick_scheduler

        if send_fn is not None and not PHASE0_MODE:
            # Step 4 M7: ConsciousnessEngine retired. Inner ticks live on
            # InnerTickScheduler; periodic maintenance lives on
            # MaintenanceTimer (this block).
            self.maintenance_timer = MaintenanceTimer(self.brain)
            await self.maintenance_timer.start()

            # DurableScheduler always starts when send_fn is wired.
            # Reminder fires push into InnerTickScheduler's urgency queue
            # so the next tick picks them up.
            if self.durable_scheduler is not None:
                _scheduler = self.inner_tick_scheduler

                async def _on_reminder_fired(reminder):
                    if _scheduler is not None:
                        _scheduler.push_urgency({
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
                self.brain.state_view_builder._reminders = self.durable_scheduler
                logger.info("DurableScheduler 循环已启动 → 内心 tick 调度器")
        elif PHASE0_MODE:
            logger.info("Phase 0 模式：跳过意识循环")

        # CircuitBreaker -> urgency: tool repeated failures -> heartbeat
        if hasattr(self, '_circuit_breaker') and self.inner_tick_scheduler is not None:
            _its_cb = self.inner_tick_scheduler
            _cb = self._circuit_breaker
            def _on_cb_open(key: str, count: int) -> None:
                _its_cb.push_urgency({
                    "type": "circuit_breaker",
                    'content': f'工具 {key} 已连续失败 {count} 次，断路器已开启。考虑创建 Skill 来更可靠地处理这类任务。',
                })
            _cb._on_open = _on_cb_open

        # CorrectionManager → urgency 信号：纠正规则反复违反时推送到 heartbeat
        if hasattr(self, '_correction_manager') and self.inner_tick_scheduler is not None:
            _its_cm = self.inner_tick_scheduler
            _cm = self._correction_manager
            def _on_correction_threshold(rule_key: str, count: int, details: str) -> None:
                _its_cm.push_urgency({
                    "type": "correction_threshold",
                    "rule_key": rule_key,
                    "count": count,
                    "details": details,
                    'content': f'纠正规则「{rule_key[:80]}」已被违反 {count} 次。这是反复出现的问题，考虑创建 Skill 来系统性地避免它。',
                })
            _cm._on_threshold = _on_correction_threshold

        # v2.0 Step 4: start MainLoop after the consciousness/scheduler
        # block above (so brain wiring is complete) but before adapters
        # connect (so the first MessageEvent / InnerTickEvent has a
        # consumer). InnerTickScheduler was constructed at the top of
        # start() so DurableScheduler could wire its urgency callback.
        self.main_loop = MainLoop(
            self.event_queue, self.brain, self.inner_tick_scheduler,
        )
        self._main_loop_task = _asyncio.create_task(
            self.main_loop.run(), name="lapwing-main-loop",
        )
        if not PHASE0_MODE:
            await self.inner_tick_scheduler.start()
        logger.info("MainLoop + InnerTickScheduler 已启动")

        await self.channel_manager.start_all()

        await self.api_server.start()

        # Step 4 M7: api_server.app.state.consciousness was used by SSE
        # status endpoints to project the legacy ConsciousnessEngine
        # state. With the engine retired, no API consumes the field;
        # set it to None so any lingering reader sees a clean signal.
        if self.api_server._app is not None:
            self.api_server._app.state.consciousness = None

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

        # Step 4 M7: MaintenanceTimer replaces ConsciousnessEngine for
        # periodic background work; stop it during shutdown.
        if self.maintenance_timer is not None:
            await self.maintenance_timer.stop()
            self.maintenance_timer = None

        # API 先停，不再接受新请求
        await self.api_server.shutdown()

        # Channel 后停，处理完在途消息
        await self.channel_manager.stop_all()

        # v2.0 Step 4: stop scheduler before MainLoop so it stops
        # producing events into the queue we're about to drain.
        if self.inner_tick_scheduler is not None:
            await self.inner_tick_scheduler.stop()
            self.inner_tick_scheduler = None

        # Stop MainLoop after channels so any in-flight adapter callbacks
        # can drain. cancel() unblocks queue.get.
        if self.main_loop is not None:
            await self.main_loop.stop()
        if self._main_loop_task is not None:
            self._main_loop_task.cancel()
            try:
                await self._main_loop_task
            except _asyncio.CancelledError:
                pass
            self._main_loop_task = None
        self.main_loop = None

        # VLM 客户端关闭
        if hasattr(self, "_vlm_client") and self._vlm_client is not None:
            try:
                await self._vlm_client.close()
            except Exception as e:
                logger.debug("VLM 客户端关闭失败: %s", e)

        # ProxyRouter 规则持久化
        if self.proxy_router is not None:
            try:
                await self.proxy_router.persist()
                logger.info("ProxyRouter 规则已持久化")
            except Exception as exc:
                logger.warning("ProxyRouter 持久化失败: %s", exc)

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

        # AmbientKnowledgeStore 关闭
        if hasattr(self, "ambient_store") and self.ambient_store is not None:
            try:
                await self.ambient_store.close()
            except Exception:
                logger.warning("ambient_store close failed", exc_info=True)
            self.ambient_store = None

        # 身份基底存储关闭
        if hasattr(self, '_identity_store') and self._identity_store is not None:
            await self._identity_store.close()
            logger.info("身份基底存储已关闭 / Identity store closed")

        # v2.0 Step 2f: close TrajectoryStore connection before mutation_log
        # closes (so any in-flight TRAJECTORY_APPENDED records land first).
        if self.trajectory_store is not None:
            try:
                await self.trajectory_store.close()
            except Exception:
                logger.warning("trajectory_store close failed", exc_info=True)
            self.trajectory_store = None

        if self.focus_manager is not None:
            try:
                await self.focus_manager.close_db()
            except Exception:
                logger.warning("focus_manager close failed", exc_info=True)
            self.focus_manager = None

        # v2.0 Step 5: same ordering rule for CommitmentStore — flush before
        # mutation_log so COMMITMENT_* events land in the audit trail.
        if self.commitment_store is not None:
            try:
                await self.commitment_store.close()
            except Exception:
                logger.warning("commitment_store close failed", exc_info=True)
            self.commitment_store = None

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

        self._started = False
        logger.info("应用容器资源清理完成")

    async def _configure_brain_dependencies(self) -> None:
        if PHASE0_MODE:
            logger.info("Phase 0 模式 (%s)：跳过大部分依赖装配", PHASE0_MODE)
            return

        from src.memory.vector_store import VectorStore

        self.brain.vector_store = VectorStore(self._data_dir / "chroma")

        from src.core.state_view_builder import StateViewBuilder
        from src.core.vitals import get_previous_state
        from config.settings import IDENTITY_DIR
        self.brain.state_view_builder = StateViewBuilder(
            soul_path=IDENTITY_DIR / "soul.md",
            constitution_path=IDENTITY_DIR / "constitution.md",
            voice_prompt_name="lapwing_voice",
            attention_manager=self.brain.attention_manager,
            trajectory_store=self.brain.trajectory_store,
            focus_manager=None,
            commitment_store=self.commitment_store,
            task_store=None,
            reminder_source=None,
            previous_state_reader=get_previous_state,
        )

        # AmbientKnowledgeStore —— 环境知识缓存
        from src.ambient.ambient_knowledge import AmbientKnowledgeStore
        self.ambient_store = AmbientKnowledgeStore(
            db_path=self._data_dir / "ambient.db",
        )
        await self.ambient_store.init()
        self.brain.state_view_builder._ambient = self.ambient_store
        self.brain._ambient_store = self.ambient_store

        # PreparationEngine —— 准备引擎
        from src.ambient.preparation_engine import InterestProfile, PreparationEngine
        interest_profile = InterestProfile(IDENTITY_DIR / "kevin_interests.md")
        self._preparation_engine = PreparationEngine(
            interest_profile=interest_profile,
            ambient_store=self.ambient_store,
        )
        self.brain._preparation_engine = self._preparation_engine
        self.brain._interest_profile = interest_profile

        # CorrectionManager —— 行为纠正记录 + 断路器反馈
        # on_threshold / on_circuit_break 回调延迟绑定 inner_tick_scheduler（start() 时才创建），
        # 用 lambda 捕获 self 实现延迟解析，避免循环依赖。
        from config.settings import DATA_DIR
        from src.feedback.correction_manager import CorrectionManager
        from src.feedback.correction_store import CorrectionStore
        _correction_manager = CorrectionManager(
            store=CorrectionStore(DATA_DIR / "corrections.db"),
            threshold=3,
            on_threshold=lambda rule_key, count, details: (
                self.inner_tick_scheduler.push_urgency({
                    "type": "correction_threshold",
                    "content": f"纠正阈值：规则「{rule_key}」已被纠正{count}次。详情：{details}",
                }) if self.inner_tick_scheduler is not None else None
            ),
            on_circuit_break=lambda tool_name, repeat_count: (
                self.inner_tick_scheduler.push_urgency({
                    "type": "circuit_break",
                    "content": f"工具断路：{tool_name} 重复{repeat_count}次无进展",
                }) if self.inner_tick_scheduler is not None else None
            ),
        )
        self.brain._correction_manager = _correction_manager
        self._correction_manager = _correction_manager
        self.brain.state_view_builder._correction_manager = _correction_manager
        # 将断路器回调注入到 task_runtime（CorrectionManager 做防抖）
        self.brain.task_runtime.on_circuit_breaker_open = _correction_manager.on_circuit_break
        logger.info("CorrectionManager 已装配（阈值=3，断路器冷却=600s）")

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

        # voice.md / constitution.md 走通用的 IdentityFileManager：
        # 无冷却（编辑权全归 Kevin），但要版本化。voice.md 写入后
        # 清掉 prompt_loader 缓存，让下一次 state view 重新读取。
        from src.core.identity_file_manager import IdentityFileManager
        from src.core.prompt_loader import clear_cache as _clear_prompt_cache
        from config.settings import PROMPTS_DIR

        self._voice_manager = IdentityFileManager(
            file_path=PROMPTS_DIR / "lapwing_voice.md",
            snapshot_dir=IDENTITY_DIR / "voice_snapshots",
            kind="voice",
            on_after_write=_clear_prompt_cache,
        )
        self._constitution_manager = IdentityFileManager(
            file_path=IDENTITY_DIR / "constitution.md",
            snapshot_dir=IDENTITY_DIR / "constitution_snapshots",
            kind="constitution",
        )
        self.brain._voice_manager_ref = self._voice_manager
        self.brain._constitution_manager_ref = self._constitution_manager

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

        # Step 7: Episodic/Semantic stores + WorkingSet + extractor/distiller
        #
        # Episodic / Semantic 共享 MemoryVectorStore 底层 ChromaDB collection，
        # 用 metadata.note_type 区分。WorkingSet 把两层合并喂给 StateView。
        # 提取管线：conversation_end 触发 episodic；maintenance daily 触发 semantic。
        from config.settings import (
            EPISODIC_EXTRACT_ENABLED,
            EPISODIC_EXTRACT_MIN_TURNS,
            EPISODIC_EXTRACT_WINDOW_SIZE,
            MEMORY_DIR,
            MEMORY_WIKI_CONTEXT_BUDGET_RATIO,
            MEMORY_WIKI_CONTEXT_ENABLED,
            MEMORY_WIKI_DIR,
            MEMORY_WIKI_ENABLED,
            MEMORY_WORKING_SET_TOP_K,
            SEMANTIC_DISTILL_DEDUP_THRESHOLD,
            SEMANTIC_DISTILL_ENABLED,
            SEMANTIC_DISTILL_EPISODES_WINDOW,
        )
        from src.memory.episodic_extractor import EpisodicExtractor
        from src.memory.episodic_store import EpisodicStore
        from src.memory.incident_store import IncidentStore
        from src.memory.semantic_distiller import SemanticDistiller
        from src.memory.semantic_store import SemanticStore
        from src.memory.working_set import WorkingSet
        episodic_store = EpisodicStore(
            memory_dir=MEMORY_DIR / "episodic",
            vector_store=memory_vector_store,
        )
        semantic_store = SemanticStore(
            memory_dir=MEMORY_DIR / "semantic",
            vector_store=memory_vector_store,
            dedup_threshold=SEMANTIC_DISTILL_DEDUP_THRESHOLD,
        )
        incident_store = IncidentStore(
            memory_dir=MEMORY_DIR / "incidents",
            vector_store=memory_vector_store,
        )
        self.brain._episodic_store = episodic_store
        self.brain._semantic_store = semantic_store
        self.brain._incident_store = incident_store
        self.brain._working_set = WorkingSet(
            episodic_store=episodic_store,
            semantic_store=semantic_store,
            wiki_dir=MEMORY_WIKI_DIR,
            wiki_enabled=(MEMORY_WIKI_ENABLED and MEMORY_WIKI_CONTEXT_ENABLED),
            wiki_budget_ratio=MEMORY_WIKI_CONTEXT_BUDGET_RATIO,
        )
        self.brain.state_view_builder._working_set = self.brain._working_set
        self.brain.state_view_builder._memory_top_k = MEMORY_WORKING_SET_TOP_K

        if EPISODIC_EXTRACT_ENABLED and self.trajectory_store is not None:
            self.brain._episodic_extractor = EpisodicExtractor(
                router=self.brain.router,
                trajectory_store=self.trajectory_store,
                episodic_store=episodic_store,
                incident_store=incident_store,
                window_size=EPISODIC_EXTRACT_WINDOW_SIZE,
                min_turns=EPISODIC_EXTRACT_MIN_TURNS,
            )
        else:
            self.brain._episodic_extractor = None

        if SEMANTIC_DISTILL_ENABLED:
            self.brain._semantic_distiller = SemanticDistiller(
                router=self.brain.router,
                episodic_store=episodic_store,
                semantic_store=semantic_store,
                episodes_window=SEMANTIC_DISTILL_EPISODES_WINDOW,
            )
        else:
            self.brain._semantic_distiller = None

        from config.settings import FOCUS_ENABLED
        from src.core.focus_archiver import EpisodicArchiver
        from src.core.focus_manager import FocusManager
        focus_archiver = EpisodicArchiver(
            episodic_store=episodic_store,
            llm_router=self.brain.router,
        )
        self.focus_manager = FocusManager(
            db_path=self._data_dir / "lapwing.db",
            trajectory_store=self.trajectory_store,
            attention_manager=self.attention_manager,
            llm_router=self.brain.router,
            vector_store=memory_vector_store,
            archiver=focus_archiver,
            episodic_extractor=getattr(self.brain, "_episodic_extractor", None),
            mutation_log=self.mutation_log,
            enabled=FOCUS_ENABLED,
        )
        await self.focus_manager.init_db()
        await self.focus_manager.startup_load()
        self.brain.focus_manager = self.focus_manager
        self.brain.state_view_builder._focus_manager = self.focus_manager
        logger.info("FocusManager 已初始化（enabled=%s）", FOCUS_ENABLED)

        logger.info(
            "Step 7 记忆树已装配（Episodic + Semantic + WorkingSet + "
            "extractor=%s + distiller=%s）",
            self.brain._episodic_extractor is not None,
            self.brain._semantic_distiller is not None,
        )

        # ── 身份基底 ─────────────────────────────
        from config.settings import (
            IDENTITY_PARSER_ENABLED, IDENTITY_STORE_ENABLED,
            IDENTITY_RETRIEVER_ENABLED, IDENTITY_INJECTOR_ENABLED,
            IDENTITY_GATE_ENABLED, IDENTITY_SYSTEM_KILLSWITCH,
            DATA_DIR,
        )
        from src.identity.flags import IdentityFlags
        self._identity_flags = IdentityFlags(
            parser_enabled=IDENTITY_PARSER_ENABLED,
            store_enabled=IDENTITY_STORE_ENABLED,
            retriever_enabled=IDENTITY_RETRIEVER_ENABLED,
            injector_enabled=IDENTITY_INJECTOR_ENABLED,
            gate_enabled=IDENTITY_GATE_ENABLED,
            identity_system_killswitch=IDENTITY_SYSTEM_KILLSWITCH,
        )
        self._identity_store = None
        self._identity_retriever = None
        self._identity_vector_index = None
        if self._identity_flags.is_active("store"):
            from src.identity.store import IdentityStore
            self._identity_store = IdentityStore(db_path=DATA_DIR / "identity.db")
            await self._identity_store.init()
            logger.info("身份基底存储已初始化 / Identity store initialized")
            if self._identity_flags.is_active("retriever"):
                from src.identity.retriever import IdentityRetriever
                from src.identity.vector_index import IdentityVectorIndex, drain_outbox
                try:
                    self._identity_vector_index = IdentityVectorIndex(
                        persist_dir=DATA_DIR / "chroma_identity",
                    )
                    drained = await drain_outbox(
                        self._identity_store, self._identity_vector_index,
                    )
                    logger.info(
                        "身份向量索引已初始化 / Identity vector index ready (%s)",
                        drained,
                    )
                except Exception:
                    # Embedding optional — retriever falls back to confidence sort.
                    self._identity_vector_index = None
                    logger.warning(
                        "身份向量索引初始化失败，退回 confidence-only 排序",
                        exc_info=True,
                    )
                self._identity_retriever = IdentityRetriever(
                    store=self._identity_store,
                    flags=self._identity_flags,
                    vector_index=self._identity_vector_index,
                )
        self.brain._identity_store = self._identity_store
        self.brain._identity_flags = self._identity_flags
        self.brain._identity_retriever = self._identity_retriever
        self.brain._identity_vector_index = self._identity_vector_index

        # ── Agent Team 系统 (Phase 6 + Blueprint §6) ─────────────────
        # v2 wiring: Catalog (SQLite) + Factory + Policy + Registry facade.
        # Builtin researcher/coder specs are upserted into the catalog at
        # init() time; their runtime instances are produced fresh by the
        # Factory on every delegation, mirroring dynamic-agent semantics.
        from config.settings import AGENT_TEAM_ENABLED
        if AGENT_TEAM_ENABLED:
            from src.agents.catalog import AgentCatalog
            from src.agents.factory import AgentFactory
            from src.agents.policy import AgentPolicy
            from src.agents.registry import AgentRegistry
            from src.tools.agent_tools import register_agent_tools
            from src.tools.workspace_tools import (
                ws_file_read_executor,
                ws_file_write_executor,
                ws_file_list_executor,
            )

            agent_catalog = AgentCatalog(self._data_dir / "lapwing.db")
            await agent_catalog.init()
            agent_factory = AgentFactory(
                llm_router=self.brain.router,
                tool_registry=self.brain.tool_registry,
                mutation_log=self.mutation_log,
            )
            agent_policy = AgentPolicy(
                catalog=agent_catalog,
                llm_router=self.brain.router,
            )
            agent_registry = AgentRegistry(
                catalog=agent_catalog,
                factory=agent_factory,
                policy=agent_policy,
            )
            await agent_registry.init()

            # Expose to Brain for service injection (build_services pulls these).
            self.brain._agent_catalog = agent_catalog
            self.brain._agent_policy = agent_policy

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

            register_agent_tools(self.brain.tool_registry, agent_registry)

            self.brain._agent_registry = agent_registry
            # StateView agent summary (Blueprint §9): wire registry into builder.
            self.brain.state_view_builder._agent_registry = agent_registry

            # 创建工作区目录
            Path("data/agent_workspace").mkdir(parents=True, exist_ok=True)
            Path("data/agent_workspace/patches").mkdir(parents=True, exist_ok=True)

            # Periodic session cleanup (Blueprint §6 / §13).
            try:
                from src.config import get_settings
                interval = get_settings().agent_team.dynamic.session_cleanup_interval_seconds
                # Schedule via APScheduler if available; otherwise rely on
                # ad-hoc cleanup at delegation time. Only fire if we have a
                # scheduler registered already.
                ap_sched = getattr(self, "scheduler", None) or getattr(self.brain, "scheduler", None)
                if ap_sched is not None and hasattr(ap_sched, "add_job"):
                    async def _cleanup_sessions():
                        try:
                            n = await agent_registry.cleanup_expired_sessions()
                            if n:
                                logger.info("Cleaned %d expired session agents", n)
                        except Exception:
                            logger.exception("session cleanup failed")
                    ap_sched.add_job(
                        _cleanup_sessions, "interval",
                        seconds=interval, id="agent_session_cleanup",
                        replace_existing=True,
                    )
            except Exception:
                logger.debug("APScheduler hookup for session cleanup skipped",
                             exc_info=True)

            n_agents = len(await agent_registry.list_agents())
            logger.info("Agent Team v2 系统已就绪（%d agents in catalog）", n_agents)

        # Phase 4: DurableScheduler（初始化但不启动循环——循环在 start() 中启动）
        self.durable_scheduler = DurableScheduler(
            db_path=self._db_path,
            dispatcher=self.dispatcher,
            trajectory_store=getattr(self, "trajectory_store", None),
            mutation_log=getattr(self, "mutation_log", None),
            event_queue=self.event_queue,
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
            fetcher=SmartFetcher(browser_manager=self._browser_manager, proxy_router=self.proxy_router),
            refiner=Refiner(llm_router=self.brain.router),
        )
        register_research_tool(self.brain.tool_registry)
        logger.info("Research 子系统已装配（research 工具 + ResearchEngine）")

        # 环境知识工具
        from src.tools.ambient_tools import register_ambient_tools
        register_ambient_tools(self.brain.tool_registry)
        logger.info("环境知识工具已注册（prepare/check_ambient_knowledge + manage_interest_profile）")

        # CircuitBreaker — 工具失败断路器，注入到 brain services 让 task_runtime 使用
        from src.utils.circuit_breaker import CircuitBreaker
        self._circuit_breaker = CircuitBreaker()
        self.brain._circuit_breaker_ref = self._circuit_breaker
        logger.info("CircuitBreaker 已装配")

        # Skill Growth Model
        from config.settings import SKILL_SYSTEM_ENABLED
        if SKILL_SYSTEM_ENABLED:
            from src.skills.skill_store import SkillStore
            from src.skills.skill_executor import SkillExecutor
            from src.tools.skill_tools import register_skill_tools, _register_skill_as_tool
            from config.settings import SKILL_SANDBOX_IMAGE

            skill_store = SkillStore()
            skill_executor = SkillExecutor(
                skill_store=skill_store,
                sandbox_image=SKILL_SANDBOX_IMAGE,
            )
            self.brain._skill_store = skill_store
            self.brain._skill_executor = skill_executor
            self.brain.state_view_builder._skill_store = skill_store

            # Register stable skills as first-class tools
            for stable_skill in skill_store.get_stable_skills():
                _register_skill_as_tool(
                    self.brain.tool_registry,
                    skill_store,
                    skill_executor,
                    stable_skill["meta"]["id"],
                )

            # Register the 6 management tools
            register_skill_tools(self.brain.tool_registry)

            logger.info(
                "Skill Growth Model 已装配（%d stable skills registered as tools）",
                len(skill_store.get_stable_skills()),
            )

        # Phase 2B: Capability read tools (feature-gated behind capabilities.enabled)
        from config.settings import CAPABILITIES_ENABLED
        if CAPABILITIES_ENABLED:
            from config.settings import CAPABILITIES_DATA_DIR, CAPABILITIES_INDEX_DB_PATH
            from src.capabilities.index import CapabilityIndex
            from src.capabilities.store import CapabilityStore
            from src.tools.capability_tools import register_capability_tools

            capability_index = CapabilityIndex(CAPABILITIES_INDEX_DB_PATH)
            capability_index.init()
            capability_store = CapabilityStore(
                data_dir=CAPABILITIES_DATA_DIR,
                mutation_log=self.mutation_log,
                index=capability_index,
            )
            self.brain._capability_store = capability_store
            self.brain._capability_index = capability_index

            register_capability_tools(
                self.brain.tool_registry,
                capability_store,
                capability_index,
            )
            logger.info("Phase 2B capability read tools registered (list/search/view)")

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
                        "description": "提醒时间，格式 YYYY-MM-DD HH:MM（默认本地时间）",
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

        # BrowserGuard is mandatory whenever browser automation runs:
        # TaskRuntime / browser_tools / BrowserManager all refuse to act
        # on browser_* tools when the guard is absent. Build it from the
        # BrowserConfig section (url_blacklist / url_whitelist / sensitive
        # words / block_internal_network).
        _s = get_settings()
        self._browser_guard = BrowserGuard.from_settings(_s.browser)
        self._browser_manager = BrowserManager()
        self._browser_manager.set_proxy_router(self.proxy_router)
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
