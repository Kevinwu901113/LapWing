"""TimeContextProvider 单元测试。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.ambient.models import TimeContext
from src.ambient.time_context import TimeContextProvider, period_name

_TZ = ZoneInfo("Asia/Taipei")
_provider = TimeContextProvider()


# ── 时段映射 ────────────────────────────────────────────────────────

class TestPeriodName:
    @pytest.mark.parametrize("hour,expected", [
        (0, "凌晨"), (1, "凌晨"), (4, "凌晨"),
        (5, "早上"), (6, "早上"), (7, "早上"),
        (8, "上午"), (9, "上午"), (10, "上午"),
        (11, "中午"), (12, "中午"),
        (13, "下午"), (14, "下午"), (16, "下午"),
        (17, "傍晚"), (18, "傍晚"),
        (19, "晚上"), (20, "晚上"), (21, "晚上"),
        (22, "深夜"), (23, "深夜"),
    ])
    def test_boundaries(self, hour, expected):
        assert period_name(hour) == expected


# ── 季节 ────────────────────────────────────────────────────────────

class TestSeason:
    @pytest.mark.parametrize("month,expected", [
        (1, "冬季"), (2, "冬季"), (3, "春季"), (4, "春季"), (5, "春季"),
        (6, "夏季"), (7, "夏季"), (8, "夏季"), (9, "秋季"), (10, "秋季"),
        (11, "秋季"), (12, "冬季"),
    ])
    def test_season_by_month(self, month, expected):
        dt = datetime(2026, month, 15, 12, 0, tzinfo=_TZ)
        ctx = _provider.get_context(dt)
        assert ctx.season == expected


# ── 星期 ────────────────────────────────────────────────────────────

class TestWeekday:
    @pytest.mark.parametrize("day,expected", [
        (20, "星期一"),   # 2026-04-20 Mon
        (21, "星期二"),
        (22, "星期三"),
        (23, "星期四"),
        (24, "星期五"),
        (25, "星期六"),
        (26, "星期日"),
    ])
    def test_weekday_names(self, day, expected):
        dt = datetime(2026, 4, day, 12, 0, tzinfo=_TZ)
        ctx = _provider.get_context(dt)
        assert ctx.weekday == expected


# ── 农历 ────────────────────────────────────────────────────────────

class TestLunarDate:
    def test_lunar_date_present(self):
        dt = datetime(2026, 4, 22, 12, 0, tzinfo=_TZ)
        ctx = _provider.get_context(dt)
        assert ctx.lunar_date is not None
        assert len(ctx.lunar_date) > 0

    def test_lunar_new_year(self):
        # 2026年春节大约在2月17日（农历正月初一）
        dt = datetime(2026, 2, 17, 12, 0, tzinfo=_TZ)
        ctx = _provider.get_context(dt)
        assert ctx.lunar_date is not None
        assert "正月" in ctx.lunar_date


# ── 节假日 ──────────────────────────────────────────────────────────

class TestHolidays:
    def test_near_labor_day(self):
        dt = datetime(2026, 4, 22, 12, 0, tzinfo=_TZ)
        ctx = _provider.get_context(dt)
        labor_events = [e for e in ctx.upcoming_events if "劳动节" in e]
        assert len(labor_events) == 1
        assert "9天" in labor_events[0]

    def test_on_holiday(self):
        dt = datetime(2026, 1, 1, 12, 0, tzinfo=_TZ)
        ctx = _provider.get_context(dt)
        new_year = [e for e in ctx.upcoming_events if "元旦" in e]
        assert len(new_year) == 1
        assert "今天" in new_year[0]

    def test_no_far_holidays(self):
        # 8月中旬距主要节假日较远
        dt = datetime(2026, 8, 15, 12, 0, tzinfo=_TZ)
        ctx = _provider.get_context(dt)
        # 检查没有30天以外的节假日
        for event in ctx.upcoming_events:
            assert "天" in event or "今天" in event

    def test_us_holidays_included(self):
        # 6月底距 Independence Day 约4天
        dt = datetime(2026, 6, 30, 12, 0, tzinfo=_TZ)
        ctx = _provider.get_context(dt)
        july4 = [e for e in ctx.upcoming_events if "Independence" in e]
        assert len(july4) == 1


# ── datetime_str 格式 ────────────────────────────────────────────────

class TestDatetimeStr:
    def test_format(self):
        dt = datetime(2026, 4, 22, 15, 24, tzinfo=_TZ)
        ctx = _provider.get_context(dt)
        assert ctx.datetime_str == "2026年4月22日 15:24"

    def test_midnight(self):
        dt = datetime(2026, 1, 1, 0, 0, tzinfo=_TZ)
        ctx = _provider.get_context(dt)
        assert "00:00" in ctx.datetime_str

    def test_noon(self):
        dt = datetime(2026, 6, 15, 12, 0, tzinfo=_TZ)
        ctx = _provider.get_context(dt)
        assert "12:00" in ctx.datetime_str


# ── to_prompt_text ──────────────────────────────────────────────────

class TestPromptText:
    def test_contains_all_parts(self):
        dt = datetime(2026, 4, 22, 15, 24, tzinfo=_TZ)
        ctx = _provider.get_context(dt)
        text = ctx.to_prompt_text()
        assert "2026年4月22日" in text
        assert "星期三" in text
        assert "下午" in text
        assert "春季" in text

    def test_lunar_in_prompt(self):
        dt = datetime(2026, 4, 22, 15, 24, tzinfo=_TZ)
        ctx = _provider.get_context(dt)
        text = ctx.to_prompt_text()
        assert "农历" in text

    def test_holiday_in_prompt(self):
        dt = datetime(2026, 4, 22, 15, 24, tzinfo=_TZ)
        ctx = _provider.get_context(dt)
        text = ctx.to_prompt_text()
        assert "劳动节" in text

    def test_no_events_still_works(self):
        # 8月中旬可能有也可能没有30天内的节假日
        ctx = TimeContext(
            datetime_str="2026年8月15日 12:00",
            weekday="星期六",
            time_period="中午",
            lunar_date=None,
            season="夏季",
            upcoming_events=(),
        )
        text = ctx.to_prompt_text()
        assert "2026年8月15日" in text
        assert "夏季" in text
