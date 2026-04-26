"""时区工具单元测试。"""
from unittest.mock import AsyncMock

import pytest

from src.tools.timezone_tools import (
    convert_timezone_executor,
    get_current_datetime_executor,
)
from src.tools.types import ToolExecutionContext, ToolExecutionRequest


def _ctx() -> ToolExecutionContext:
    return ToolExecutionContext(
        execute_shell=AsyncMock(),
        shell_default_cwd="/tmp",
        services={},
        adapter="qq",
        user_id="u1",
        auth_level=2,
        chat_id="c1",
        send_fn=None,
    )


@pytest.mark.asyncio
class TestConvertTimezone:
    async def test_basic_conversion(self):
        result = await convert_timezone_executor(
            ToolExecutionRequest(
                name="convert_timezone",
                arguments={
                    "time_str": "2026-04-22 19:00",
                    "from_tz": "America/Los_Angeles",
                    "to_tz": "Asia/Shanghai",
                },
            ),
            _ctx(),
        )
        assert result.success is True
        assert "10:00" in result.payload["converted"]

    async def test_short_time_format(self):
        result = await convert_timezone_executor(
            ToolExecutionRequest(
                name="convert_timezone",
                arguments={
                    "time_str": "19:00",
                    "from_tz": "America/Los_Angeles",
                    "to_tz": "Asia/Shanghai",
                },
            ),
            _ctx(),
        )
        assert result.success is True
        assert "10:00" in result.payload["converted"]

    async def test_default_to_tz(self):
        result = await convert_timezone_executor(
            ToolExecutionRequest(
                name="convert_timezone",
                arguments={
                    "time_str": "2026-04-22 12:00",
                    "from_tz": "UTC",
                },
            ),
            _ctx(),
        )
        assert result.success is True
        assert result.payload["to_tz"] == "Asia/Shanghai"

    async def test_invalid_timezone(self):
        result = await convert_timezone_executor(
            ToolExecutionRequest(
                name="convert_timezone",
                arguments={
                    "time_str": "12:00",
                    "from_tz": "NotATimezone/Fake",
                },
            ),
            _ctx(),
        )
        assert result.success is False

    async def test_missing_params(self):
        result = await convert_timezone_executor(
            ToolExecutionRequest(
                name="convert_timezone",
                arguments={"time_str": "", "from_tz": ""},
            ),
            _ctx(),
        )
        assert result.success is False


@pytest.mark.asyncio
class TestGetCurrentDatetime:
    async def test_default_shanghai(self):
        result = await get_current_datetime_executor(
            ToolExecutionRequest(
                name="get_current_datetime",
                arguments={},
            ),
            _ctx(),
        )
        assert result.success is True
        assert result.payload["timezone"] == "Asia/Shanghai"
        assert "datetime" in result.payload
        assert "weekday" in result.payload

    async def test_specific_timezone(self):
        result = await get_current_datetime_executor(
            ToolExecutionRequest(
                name="get_current_datetime",
                arguments={"timezone": "America/New_York"},
            ),
            _ctx(),
        )
        assert result.success is True
        assert result.payload["timezone"] == "America/New_York"

    async def test_invalid_timezone(self):
        result = await get_current_datetime_executor(
            ToolExecutionRequest(
                name="get_current_datetime",
                arguments={"timezone": "Invalid/Zone"},
            ),
            _ctx(),
        )
        assert result.success is False
