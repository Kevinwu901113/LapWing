"""FileAgent 回归测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.base import AgentTask
from src.agents.file_agent import FileAgent


def make_task(user_message: str = "读取文件") -> AgentTask:
    return AgentTask(
        chat_id="chat1",
        user_message=user_message,
        history=[],
        user_facts=[],
    )


def make_router(raw: str) -> MagicMock:
    router = MagicMock()
    router.complete = AsyncMock(return_value=raw)
    return router


@pytest.mark.asyncio
async def test_write_read_append_and_list_within_whitelist(tmp_path, monkeypatch):
    monkeypatch.setattr("src.agents.file_agent.ROOT_DIR", tmp_path)
    (tmp_path / "prompts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "notes.txt").write_text("hello", encoding="utf-8")

    agent = FileAgent(memory=MagicMock())
    with patch("src.agents.file_agent.load_prompt", return_value="prompt {user_message}"):
        write_result = await agent.execute(
            make_task("写入"),
            make_router('{"operation":"write","path":"prompts/demo.txt","content":"abc"}'),
        )
        read_result = await agent.execute(
            make_task("读取"),
            make_router('{"operation":"read","path":"prompts/demo.txt"}'),
        )
        append_result = await agent.execute(
            make_task("追加"),
            make_router('{"operation":"append","path":"prompts/demo.txt","content":"123"}'),
        )
        list_result = await agent.execute(
            make_task("列目录"),
            make_router('{"operation":"list","path":"data"}'),
        )

    assert "已写入" in write_result.content
    assert "abc" in read_result.content
    assert "已追加" in append_result.content
    assert (tmp_path / "prompts" / "demo.txt").read_text(encoding="utf-8") == "abc123"
    assert "notes.txt" in list_result.content


@pytest.mark.asyncio
async def test_rejects_non_whitelist_paths(tmp_path, monkeypatch):
    monkeypatch.setattr("src.agents.file_agent.ROOT_DIR", tmp_path)
    agent = FileAgent(memory=MagicMock())

    with patch("src.agents.file_agent.load_prompt", return_value="prompt {user_message}"):
        result = await agent.execute(
            make_task("写代码"),
            make_router('{"operation":"write","path":"src/new.py","content":"print(1)"}'),
        )

    assert "不在我的操作范围内" in result.content
    assert not (tmp_path / "src" / "new.py").exists()


@pytest.mark.asyncio
async def test_rejects_blocked_exact_path(tmp_path, monkeypatch):
    monkeypatch.setattr("src.agents.file_agent.ROOT_DIR", tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    agent = FileAgent(memory=MagicMock())

    with patch("src.agents.file_agent.load_prompt", return_value="prompt {user_message}"):
        result = await agent.execute(
            make_task("写 env"),
            make_router('{"operation":"write","path":"config/.env","content":"TOKEN=1"}'),
        )

    assert "不在我的操作范围内" in result.content
    assert not (tmp_path / "config" / ".env").exists()


@pytest.mark.asyncio
async def test_uses_runtime_tools_when_runtime_is_injected(tmp_path, monkeypatch):
    monkeypatch.setattr("src.agents.file_agent.ROOT_DIR", tmp_path)
    (tmp_path / "prompts").mkdir(parents=True, exist_ok=True)

    runtime = MagicMock()
    runtime.execute_tool = AsyncMock(
        return_value=MagicMock(
            payload={
                "success": True,
                "operation": "file_write",
                "path": str(tmp_path / "prompts" / "demo.txt"),
                "changed": True,
                "reason": "",
                "content": "",
                "diff": "",
                "backup_path": None,
                "metadata": {},
            }
        )
    )
    agent = FileAgent(memory=MagicMock(), runtime=runtime)

    with patch("src.agents.file_agent.load_prompt", return_value="prompt {user_message}"):
        result = await agent.execute(
            make_task("写入"),
            make_router('{"operation":"write","path":"prompts/demo.txt","content":"abc"}'),
        )

    assert "已写入" in result.content
    runtime.execute_tool.assert_awaited_once()
