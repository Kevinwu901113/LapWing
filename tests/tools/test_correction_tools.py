"""add_correction 工具执行器测试。"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.authority_gate import AuthLevel, OPERATION_AUTH
from src.tools.correction_tools import ADD_CORRECTION_SPEC, add_correction_executor
from src.tools.types import ToolExecutionContext, ToolExecutionRequest, ToolExecutionResult


def _make_request(args: dict) -> ToolExecutionRequest:
    return ToolExecutionRequest(name="add_correction", arguments=args)


def _make_ctx(correction_manager=None) -> ToolExecutionContext:
    services = {}
    if correction_manager is not None:
        services["correction_manager"] = correction_manager
    return ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd="/tmp",
        services=services,
    )


class TestAddCorrectionExecutor:
    """add_correction 执行器单元测试。"""

    @pytest.mark.asyncio
    async def test_success_path(self):
        """正常调用应返回 success=True 以及规则 key 和计数。"""
        mock_mgr = MagicMock()
        mock_mgr.add_correction.return_value = 2

        ctx = _make_ctx(mock_mgr)
        req = _make_request({"rule_key": "不要列清单", "details": "第二次"})
        result = await add_correction_executor(req, ctx)

        assert result.success is True
        assert result.payload["rule_key"] == "不要列清单"
        assert result.payload["count"] == 2
        mock_mgr.add_correction.assert_called_once_with("不要列清单", "第二次")

    @pytest.mark.asyncio
    async def test_success_without_details(self):
        """不提供 details 时，应用空字符串调用 add_correction。"""
        mock_mgr = MagicMock()
        mock_mgr.add_correction.return_value = 1

        ctx = _make_ctx(mock_mgr)
        req = _make_request({"rule_key": "某规则"})
        result = await add_correction_executor(req, ctx)

        assert result.success is True
        mock_mgr.add_correction.assert_called_once_with("某规则", "")

    @pytest.mark.asyncio
    async def test_missing_rule_key_returns_error(self):
        """缺少 rule_key 时应返回 success=False。"""
        mock_mgr = MagicMock()
        ctx = _make_ctx(mock_mgr)
        req = _make_request({"details": "some detail"})
        result = await add_correction_executor(req, ctx)

        assert result.success is False
        assert result.reason == "missing_rule_key"
        mock_mgr.add_correction.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_rule_key_returns_error(self):
        """空白 rule_key 应视为缺失。"""
        mock_mgr = MagicMock()
        ctx = _make_ctx(mock_mgr)
        req = _make_request({"rule_key": "   "})
        result = await add_correction_executor(req, ctx)

        assert result.success is False
        assert result.reason == "missing_rule_key"

    @pytest.mark.asyncio
    async def test_missing_correction_manager_returns_error(self):
        """services 中没有 correction_manager 时应返回 success=False。"""
        ctx = _make_ctx(correction_manager=None)
        req = _make_request({"rule_key": "某规则"})
        result = await add_correction_executor(req, ctx)

        assert result.success is False
        assert result.reason == "unavailable"


class TestAddCorrectionSpec:
    """ADD_CORRECTION_SPEC 静态属性测试。"""

    def test_spec_name(self):
        assert ADD_CORRECTION_SPEC.name == "add_correction"

    def test_spec_requires_rule_key(self):
        """JSON schema 中 rule_key 应为必填项。"""
        assert "rule_key" in ADD_CORRECTION_SPEC.json_schema["required"]

    def test_spec_capability_and_risk(self):
        assert ADD_CORRECTION_SPEC.capability == "general"
        assert ADD_CORRECTION_SPEC.risk_level == "low"


class TestOperationAuth:
    """权限配置测试。"""

    def test_add_correction_requires_owner(self):
        """add_correction 应要求 OWNER 权限。"""
        assert OPERATION_AUTH.get("add_correction") == AuthLevel.OWNER
