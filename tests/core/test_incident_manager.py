"""IncidentManager 核心功能测试。"""

import json
import shutil
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.core.incident_manager import (
    AUTO_DOWNGRADE_THRESHOLD,
    AUTO_WONTFIX_THRESHOLD,
    DAILY_LIMIT,
    INCIDENTS_DIR,
    IncidentManager,
)


@pytest.fixture(autouse=True)
def clean_incidents_dir(tmp_path, monkeypatch):
    """每个测试使用独立的 incidents 目录。"""
    test_dir = tmp_path / "incidents"
    test_dir.mkdir()
    monkeypatch.setattr("src.core.incident_manager.INCIDENTS_DIR", test_dir)
    yield test_dir


@pytest.fixture
def manager():
    return IncidentManager()


# ── 创建 ──


async def test_create_basic(manager, clean_incidents_dir):
    inc_id = await manager.create(
        source="tool_failure",
        description="web_search 超时",
        context={"tool_name": "web_search", "error_type": "timeout"},
        severity="low",
        related_tool="web_search",
    )
    assert inc_id is not None
    assert inc_id.startswith("INC-")

    # 文件应存在
    path = clean_incidents_dir / f"{inc_id}.json"
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["source"] == "tool_failure"
    assert data["status"] == "open"
    assert data["occurrence_count"] == 1


async def test_create_dedup(manager, clean_incidents_dir):
    """同一工具+同一错误类型在窗口内应去重。"""
    id1 = await manager.create(
        source="tool_failure",
        description="web_search 超时",
        context={"tool_name": "web_search", "error_type": "timeout"},
        related_tool="web_search",
    )
    assert id1 is not None

    id2 = await manager.create(
        source="tool_failure",
        description="web_search 又超时了",
        context={"tool_name": "web_search", "error_type": "timeout"},
        related_tool="web_search",
    )
    assert id2 is None  # 被去重

    # 原始 incident 的 occurrence_count 应递增
    inc = manager.get_incident(id1)
    assert inc["occurrence_count"] == 2


async def test_create_no_dedup_different_error(manager):
    """不同错误类型不应去重。"""
    id1 = await manager.create(
        source="tool_failure",
        description="web_search 超时",
        context={"error_type": "timeout"},
        related_tool="web_search",
    )
    id2 = await manager.create(
        source="tool_failure",
        description="web_search 500",
        context={"error_type": "http_5xx"},
        related_tool="web_search",
    )
    assert id1 is not None
    assert id2 is not None
    assert id1 != id2


async def test_create_daily_limit(manager, monkeypatch):
    """超过日限额后不再创建。"""
    monkeypatch.setattr("src.core.incident_manager.DAILY_LIMIT", 3)
    ids = []
    for i in range(5):
        inc_id = await manager.create(
            source="self_note",
            description=f"测试 {i}",
            context={},
        )
        ids.append(inc_id)
    assert ids[:3] != [None, None, None]
    assert ids[3] is None
    assert ids[4] is None


# ── 查询 ──


async def test_get_open_incidents(manager):
    await manager.create(source="self_note", description="low issue", context={}, severity="low")
    await manager.create(source="self_note", description="high issue", context={}, severity="high")
    await manager.create(source="self_note", description="medium issue", context={}, severity="medium")

    incidents = manager.get_open_incidents()
    assert len(incidents) == 3
    # 应按 severity 排序：high, medium, low
    assert incidents[0]["severity"] == "high"
    assert incidents[1]["severity"] == "medium"
    assert incidents[2]["severity"] == "low"


async def test_get_stats(manager):
    await manager.create(source="self_note", description="issue 1", context={})
    id2 = await manager.create(source="self_note", description="issue 2", context={})
    await manager.resolve(id2, "fixed")

    stats = manager.get_stats()
    assert stats["open"] == 1
    assert stats["resolved"] == 1
    assert stats["total"] == 2


# ── 状态流转 ──


async def test_resolve(manager):
    inc_id = await manager.create(source="self_note", description="test", context={})
    ok = await manager.resolve(inc_id, "修好了")
    assert ok
    inc = manager.get_incident(inc_id)
    assert inc["status"] == "resolved"
    assert inc["resolution"] == "修好了"
    assert inc["resolved_at"] is not None


async def test_start_investigating(manager):
    inc_id = await manager.create(source="self_note", description="test", context={})
    ok = await manager.start_investigating(inc_id)
    assert ok
    inc = manager.get_incident(inc_id)
    assert inc["status"] == "investigating"


async def test_record_attempt_downgrade(manager):
    inc_id = await manager.create(
        source="self_note", description="test", context={}, severity="high",
    )
    for _ in range(AUTO_DOWNGRADE_THRESHOLD - 1):
        result = await manager.record_attempt(inc_id)
        assert result is None

    result = await manager.record_attempt(inc_id)
    assert result == "downgraded"
    inc = manager.get_incident(inc_id)
    assert inc["severity"] == "low"


async def test_record_attempt_wontfix(manager):
    notify_fn = AsyncMock()
    manager._send_notification = notify_fn

    inc_id = await manager.create(source="self_note", description="test", context={})
    for _ in range(AUTO_WONTFIX_THRESHOLD):
        await manager.record_attempt(inc_id)

    inc = manager.get_incident(inc_id)
    assert inc["status"] == "wont_fix"
    notify_fn.assert_called_once()


async def test_mark_wont_fix(manager):
    inc_id = await manager.create(source="self_note", description="test", context={})
    ok = await manager.mark_wont_fix(inc_id, "外部 API 问题")
    assert ok
    inc = manager.get_incident(inc_id)
    assert inc["status"] == "wont_fix"


# ── 关联规则 ──


async def test_link_rule(manager):
    inc_id = await manager.create(source="self_note", description="test", context={})
    manager.link_rule(inc_id, "搜索体育赛程时加上日期")
    inc = manager.get_incident(inc_id)
    assert inc["linked_rule"] == "搜索体育赛程时加上日期"


# ── 归档 ──


async def test_archive_resolved(manager, clean_incidents_dir, monkeypatch):
    from datetime import datetime, timedelta

    inc_id = await manager.create(source="self_note", description="old", context={})
    await manager.resolve(inc_id, "fixed long ago")

    # 手动将 resolved_at 修改为 31 天前
    inc = manager.get_incident(inc_id)
    inc["resolved_at"] = (datetime.now() - timedelta(days=31)).isoformat()
    manager._save_incident(inc)

    count = manager.archive_resolved(max_age_days=30)
    assert count == 1
    assert not (clean_incidents_dir / f"{inc_id}.json").exists()
    assert (clean_incidents_dir / "archive" / f"{inc_id}.json").exists()


# ── 意识循环摘要 ──


async def test_format_for_consciousness_none(manager):
    result = manager.format_for_consciousness()
    assert result is None


async def test_format_for_consciousness(manager):
    await manager.create(
        source="tool_failure",
        description="web_search 搜索体育赛程时超时",
        context={},
        severity="high",
        related_tool="web_search",
    )
    result = manager.format_for_consciousness()
    assert result is not None
    assert "1 个未解决的问题" in result
    assert "[high]" in result
    assert "web_search" in result
