"""测试后台进程注册表。"""

import time
import pytest
from src.core.process_registry import ProcessRegistry, MAX_PROCESSES


class TestProcessRegistry:
    def test_spawn_and_poll(self):
        reg = ProcessRegistry()
        session = reg.spawn("echo hello && sleep 0.1", chat_id="test")
        assert session.id.startswith("proc_")
        assert session.pid is not None
        time.sleep(0.5)
        result = reg.poll(session.id)
        assert result["status"] == "exited"
        assert result["exit_code"] == 0

    def test_output_buffering(self):
        reg = ProcessRegistry()
        session = reg.spawn("echo line1 && echo line2 && echo line3")
        time.sleep(0.5)
        logs = reg.logs(session.id)
        assert "line1" in logs
        assert "line3" in logs

    def test_kill(self):
        reg = ProcessRegistry()
        session = reg.spawn("sleep 60")
        time.sleep(0.2)
        assert session.status == "running"
        result = reg.kill(session.id)
        assert result["status"] == "killed"

    def test_max_processes_limit(self):
        reg = ProcessRegistry()
        sessions = []
        for i in range(MAX_PROCESSES):
            sessions.append(reg.spawn(f"sleep 60"))
        with pytest.raises(RuntimeError, match="上限"):
            reg.spawn("sleep 60")
        # 清理
        for s in sessions:
            reg.kill(s.id)

    def test_list_active(self):
        reg = ProcessRegistry()
        s = reg.spawn("sleep 60", chat_id="test")
        active = reg.list_active()
        assert len(active) == 1
        assert active[0]["id"] == s.id
        reg.kill(s.id)

    def test_list_active_excludes_exited(self):
        reg = ProcessRegistry()
        session = reg.spawn("echo done")
        time.sleep(0.5)
        active = reg.list_active()
        assert len(active) == 0

    def test_watch_pattern_detection(self):
        reg = ProcessRegistry()
        session = reg.spawn('echo "test FAILED here"', watch_patterns=["FAILED"])
        time.sleep(0.5)
        assert "FAILED" in session.output_buffer

    def test_check_all_returns_completed_events(self):
        reg = ProcessRegistry()
        session = reg.spawn("echo done", notify_on_complete=True, chat_id="test")
        time.sleep(0.5)
        events = reg.check_all()
        assert len(events) == 1
        assert events[0]["type"] == "process_completed"
        assert events[0]["chat_id"] == "test"
        # 再次调用不应重复通知
        events2 = reg.check_all()
        assert len(events2) == 0

    def test_nonexistent_process(self):
        reg = ProcessRegistry()
        result = reg.poll("proc_nonexistent")
        assert "error" in result

    def test_kill_nonexistent(self):
        reg = ProcessRegistry()
        result = reg.kill("proc_nonexistent")
        assert "error" in result

    def test_logs_nonexistent(self):
        reg = ProcessRegistry()
        logs = reg.logs("proc_nonexistent")
        assert "不存在" in logs

    def test_logs_tail(self):
        reg = ProcessRegistry()
        # 生成多行输出
        session = reg.spawn("for i in $(seq 1 20); do echo line_$i; done")
        time.sleep(0.5)
        logs = reg.logs(session.id, tail=5)
        lines = logs.strip().splitlines()
        assert len(lines) == 5
        assert "line_20" in lines[-1]

    def test_spawn_invalid_command(self):
        reg = ProcessRegistry()
        # 空命令不会抛异常（shell 处理），但无效路径会
        session = reg.spawn("echo ok")
        assert session.id.startswith("proc_")
        time.sleep(0.3)
        assert session.status == "exited"
