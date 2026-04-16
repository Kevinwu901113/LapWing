"""后台进程注册表。

管理通过 process_spawn 启动的后台进程：
- 输出缓冲（滚动窗口）
- 状态轮询
- 进程终止
- 模式监控（输出匹配 pattern 时通知）

与 ConsciousnessEngine 集成：每次 tick 检查进程状态。
"""

import asyncio
import logging
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable

logger = logging.getLogger("lapwing.core.process_registry")

MAX_OUTPUT_CHARS = 200_000       # 200KB 滚动输出缓冲
MAX_PROCESSES = 16               # 最大同时追踪进程数
FINISHED_TTL_SECONDS = 1800      # 已完成进程保留 30 分钟


@dataclass
class ProcessSession:
    """一个被追踪的后台进程。"""
    id: str                                      # "proc_xxxx"
    command: str                                  # 原始命令
    chat_id: str = ""                            # 关联对话
    pid: int | None = None                       # OS PID
    process: subprocess.Popen | None = None      # Popen 句柄
    started_at: float = 0.0
    exited: bool = False
    exit_code: int | None = None
    output_buffer: str = ""
    watch_patterns: list[str] = field(default_factory=list)
    notify_on_complete: bool = False

    def append_output(self, text: str) -> list[str]:
        """追加输出到缓冲，检查 watch patterns。返回匹配到的 patterns。"""
        self.output_buffer += text
        # 滚动窗口
        if len(self.output_buffer) > MAX_OUTPUT_CHARS:
            self.output_buffer = self.output_buffer[-MAX_OUTPUT_CHARS:]
        # 检查 watch patterns
        matched = []
        for pattern in self.watch_patterns:
            if pattern.lower() in text.lower():
                matched.append(pattern)
        return matched

    @property
    def status(self) -> str:
        if self.exited:
            return "exited"
        if self.process and self.process.poll() is not None:
            self.exited = True
            self.exit_code = self.process.returncode
            return "exited"
        return "running"

    @property
    def output_tail(self) -> str:
        """最后 2000 字符输出。"""
        return self.output_buffer[-2000:] if self.output_buffer else ""


class ProcessRegistry:
    """后台进程注册表。"""

    def __init__(self):
        self._sessions: dict[str, ProcessSession] = {}
        self._lock = threading.Lock()
        self._notify_callback: Callable[[str, str, str], Awaitable[None]] | None = None

    def set_notify_callback(self, callback):
        """设置通知回调：callback(chat_id, process_id, message)"""
        self._notify_callback = callback

    def spawn(
        self,
        command: str,
        chat_id: str = "",
        cwd: str | None = None,
        watch_patterns: list[str] | None = None,
        notify_on_complete: bool = False,
    ) -> ProcessSession:
        """启动后台进程并注册。"""
        with self._lock:
            # 清理过期进程
            self._cleanup_expired()
            if len([s for s in self._sessions.values() if s.status == "running"]) >= MAX_PROCESSES:
                raise RuntimeError(f"后台进程数已达上限 ({MAX_PROCESSES})")

        session_id = f"proc_{uuid.uuid4().hex[:12]}"
        try:
            proc = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=cwd,
            )
        except Exception as e:
            raise RuntimeError(f"进程启动失败: {e}")

        session = ProcessSession(
            id=session_id,
            command=command,
            chat_id=chat_id,
            pid=proc.pid,
            process=proc,
            started_at=time.time(),
            watch_patterns=watch_patterns or [],
            notify_on_complete=notify_on_complete,
        )

        with self._lock:
            self._sessions[session_id] = session

        # 启动输出读取线程
        thread = threading.Thread(
            target=self._read_output,
            args=(session,),
            daemon=True,
            name=f"proc-reader-{session_id}",
        )
        thread.start()

        logger.info("后台进程启动: id=%s pid=%s cmd=%s", session_id, proc.pid, command[:80])
        return session

    def poll(self, session_id: str) -> dict:
        """查询进程状态。"""
        session = self._sessions.get(session_id)
        if not session:
            return {"error": f"进程 {session_id} 不存在"}
        return {
            "id": session.id,
            "command": session.command,
            "status": session.status,
            "pid": session.pid,
            "exit_code": session.exit_code,
            "runtime_seconds": round(time.time() - session.started_at, 1),
            "output_tail": session.output_tail,
        }

    def kill(self, session_id: str) -> dict:
        """终止进程。"""
        session = self._sessions.get(session_id)
        if not session:
            return {"error": f"进程 {session_id} 不存在"}
        if session.process and session.status == "running":
            try:
                session.process.kill()
                session.process.wait(timeout=5)
                session.exited = True
                session.exit_code = session.process.returncode
                logger.info("进程已终止: %s", session_id)
            except Exception as e:
                return {"error": f"终止失败: {e}"}
        return {"id": session_id, "status": "killed", "exit_code": session.exit_code}

    def logs(self, session_id: str, tail: int = 50) -> str:
        """获取进程输出日志。"""
        session = self._sessions.get(session_id)
        if not session:
            return f"进程 {session_id} 不存在"
        lines = session.output_buffer.splitlines()
        return "\n".join(lines[-tail:]) if lines else "(无输出)"

    def list_active(self) -> list[dict]:
        """列出所有活跃进程。"""
        return [
            {
                "id": s.id,
                "command": s.command[:60],
                "status": s.status,
                "pid": s.pid,
                "runtime": f"{time.time() - s.started_at:.0f}s",
            }
            for s in self._sessions.values()
            if s.status == "running"
        ]

    def check_all(self) -> list[dict]:
        """检查所有进程状态，返回需要通知的事件。

        供 ConsciousnessEngine tick 调用。
        """
        events = []
        for session in list(self._sessions.values()):
            # 刷新状态
            _ = session.status
            if session.exited and session.notify_on_complete:
                events.append({
                    "type": "process_completed",
                    "id": session.id,
                    "command": session.command[:60],
                    "exit_code": session.exit_code,
                    "chat_id": session.chat_id,
                })
                session.notify_on_complete = False  # 只通知一次
        return events

    def _read_output(self, session: ProcessSession):
        """在后台线程中读取进程输出。"""
        try:
            for line in session.process.stdout:
                matched = session.append_output(line)
                if matched and self._notify_callback:
                    msg = (
                        f"后台进程 `{session.command[:40]}` 输出匹配: "
                        f"{', '.join(matched)}\n```\n{line.strip()}\n```"
                    )
                    try:
                        loop = asyncio.get_event_loop()
                        asyncio.run_coroutine_threadsafe(
                            self._notify_callback(session.chat_id, session.id, msg),
                            loop,
                        )
                    except Exception:
                        pass
        except Exception:
            pass
        finally:
            session.exited = True
            if session.process:
                try:
                    session.exit_code = session.process.wait(timeout=5)
                except Exception:
                    pass

    def _cleanup_expired(self):
        """清理已完成且超过 TTL 的进程。"""
        now = time.time()
        expired = [
            sid for sid, s in self._sessions.items()
            if s.exited and (now - s.started_at) > FINISHED_TTL_SECONDS
        ]
        for sid in expired:
            del self._sessions[sid]


# 全局单例
process_registry = ProcessRegistry()
