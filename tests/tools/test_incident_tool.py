"""report_incident 工具测试。"""

from unittest.mock import AsyncMock

import pytest

from src.tools.incident_tool import execute_report_incident
from src.tools.types import ToolExecutionContext, ToolExecutionRequest


def _make_context(incident_manager=None):
    services = {}
    if incident_manager is not None:
        services["incident_manager"] = incident_manager
    return ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd="/tmp",
        services=services,
    )


async def test_report_incident_success():
    mock_manager = AsyncMock()
    mock_manager.create.return_value = "INC-20260412-0001"
    ctx = _make_context(mock_manager)
    req = ToolExecutionRequest(
        name="report_incident",
        arguments={"description": "搜索体育赛程总是超时", "severity": "medium"},
    )
    result = await execute_report_incident(req, ctx)
    assert result.success
    assert "INC-20260412-0001" in result.reason
    mock_manager.create.assert_called_once()


async def test_report_incident_dedup():
    mock_manager = AsyncMock()
    mock_manager.create.return_value = None  # 被去重
    ctx = _make_context(mock_manager)
    req = ToolExecutionRequest(
        name="report_incident",
        arguments={"description": "重复的问题"},
    )
    result = await execute_report_incident(req, ctx)
    assert result.success
    assert "去重" in result.reason


async def test_report_incident_no_manager():
    ctx = _make_context(incident_manager=None)
    req = ToolExecutionRequest(
        name="report_incident",
        arguments={"description": "test"},
    )
    result = await execute_report_incident(req, ctx)
    assert not result.success


async def test_report_incident_empty_description():
    mock_manager = AsyncMock()
    ctx = _make_context(mock_manager)
    req = ToolExecutionRequest(
        name="report_incident",
        arguments={"description": ""},
    )
    result = await execute_report_incident(req, ctx)
    assert not result.success
    mock_manager.create.assert_not_called()
