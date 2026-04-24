"""TimeContextProvider——纯计算生成当前时间语境。

零外部调用、零 LLM 开销。接受 datetime 参数，方便测试。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from src.ambient.models import TimeContext

try:
    from lunardate import LunarDate
    _HAS_LUNARDATE = True
except ImportError:
    _HAS_LUNARDATE = False


# ── 时段映射 ────────────────────────────────────────────────────────

_WEEKDAY_NAMES: tuple[str, ...] = (
    "星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日",
)

_SEASON_NAMES: dict[int, str] = {
    1: "冬季", 2: "冬季", 3: "春季", 4: "春季", 5: "春季",
    6: "夏季", 7: "夏季", 8: "夏季", 9: "秋季", 10: "秋季",
    11: "秋季", 12: "冬季",
}

_LUNAR_MONTH_NAMES: tuple[str, ...] = (
    "", "正月", "二月", "三月", "四月", "五月", "六月",
    "七月", "八月", "九月", "十月", "冬月", "腊月",
)

_LUNAR_DAY_NAMES: tuple[str, ...] = (
    "",
    "初一", "初二", "初三", "初四", "初五", "初六", "初七", "初八", "初九", "初十",
    "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十",
    "廿一", "廿二", "廿三", "廿四", "廿五", "廿六", "廿七", "廿八", "廿九", "三十",
)


# ── 节假日表 ────────────────────────────────────────────────────────

def _chinese_holidays(year: int) -> list[tuple[datetime, str]]:
    """返回指定年份的中国固定节假日（公历日期）。

    春节、清明、端午、中秋需要农历转换；
    无 lunardate 时只返回公历固定的节日。
    """
    holidays: list[tuple[datetime, str]] = [
        (datetime(year, 1, 1), "元旦"),
        (datetime(year, 5, 1), "劳动节"),
        (datetime(year, 10, 1), "国庆节"),
    ]
    if _HAS_LUNARDATE:
        try:
            spring = LunarDate(year, 1, 1).toSolarDate()
            holidays.append((datetime(spring.year, spring.month, spring.day), "春节"))
        except Exception:
            pass
        try:
            dragon = LunarDate(year, 5, 5).toSolarDate()
            holidays.append((datetime(dragon.year, dragon.month, dragon.day), "端午节"))
        except Exception:
            pass
        try:
            mid_autumn = LunarDate(year, 8, 15).toSolarDate()
            holidays.append((datetime(mid_autumn.year, mid_autumn.month, mid_autumn.day), "中秋节"))
        except Exception:
            pass
    # 清明固定 4/4 或 4/5——简化为 4/5
    holidays.append((datetime(year, 4, 5), "清明节"))
    return holidays


def _us_holidays(year: int) -> list[tuple[datetime, str]]:
    """返回指定年份的美国主要节假日。"""
    holidays: list[tuple[datetime, str]] = [
        (datetime(year, 1, 1), "New Year's Day"),
        (datetime(year, 7, 4), "Independence Day"),
        (datetime(year, 12, 25), "Christmas"),
    ]
    # Presidents' Day: 2月第3个周一
    holidays.append((_nth_weekday(year, 2, 0, 3), "Presidents' Day"))
    # Memorial Day: 5月最后一个周一
    holidays.append((_last_weekday(year, 5, 0), "Memorial Day"))
    # Labor Day: 9月第1个周一
    holidays.append((_nth_weekday(year, 9, 0, 1), "Labor Day"))
    # Thanksgiving: 11月第4个周四
    holidays.append((_nth_weekday(year, 11, 3, 4), "Thanksgiving"))
    return holidays


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> datetime:
    """某月第 n 个指定 weekday（0=Mon, 3=Thu）。"""
    first = datetime(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> datetime:
    """某月最后一个指定 weekday。"""
    if month == 12:
        next_month = datetime(year + 1, 1, 1)
    else:
        next_month = datetime(year, month + 1, 1)
    last_day = next_month - timedelta(days=1)
    offset = (last_day.weekday() - weekday) % 7
    return last_day - timedelta(days=offset)


# ── 核心 Provider ───────────────────────────────────────────────────

def period_name(hour: int) -> str:
    """小时 → 中文时段标签。"""
    if 0 <= hour < 5:
        return "凌晨"
    if 5 <= hour < 8:
        return "早上"
    if 8 <= hour < 11:
        return "上午"
    if 11 <= hour < 13:
        return "中午"
    if 13 <= hour < 17:
        return "下午"
    if 17 <= hour < 19:
        return "傍晚"
    if 19 <= hour < 22:
        return "晚上"
    return "深夜"


def _lunar_date_str(dt: datetime) -> str | None:
    """尝试计算农历日期字符串，失败返回 None。"""
    if not _HAS_LUNARDATE:
        return None
    try:
        ld = LunarDate.fromSolarDate(dt.year, dt.month, dt.day)
        month_name = _LUNAR_MONTH_NAMES[ld.month] if ld.month <= 12 else f"{ld.month}月"
        day_name = _LUNAR_DAY_NAMES[ld.day] if ld.day <= 30 else f"{ld.day}日"
        return f"{month_name}{day_name}"
    except Exception:
        return None


def _upcoming_events(dt: datetime, horizon_days: int = 30) -> tuple[str, ...]:
    """返回未来 horizon_days 天内的节假日描述。"""
    today = dt.date()
    year = dt.year
    all_holidays = _chinese_holidays(year) + _us_holidays(year)
    # 也检查次年元旦/春节等（年末时需要）
    if dt.month >= 11:
        all_holidays += _chinese_holidays(year + 1) + _us_holidays(year + 1)

    events: list[tuple[int, str]] = []
    for h_date, h_name in all_holidays:
        delta = (h_date.date() - today).days
        if 0 < delta <= horizon_days:
            events.append((delta, h_name))
        elif delta == 0:
            events.append((0, h_name))

    events.sort(key=lambda x: x[0])
    # 去重
    seen: set[str] = set()
    result: list[str] = []
    for days, name in events:
        if name in seen:
            continue
        seen.add(name)
        if days == 0:
            result.append(f"今天是{name}。")
        else:
            result.append(f"距{name}还有{days}天。")
    return tuple(result)


class TimeContextProvider:
    """生成当前时间的语境信息。纯计算，零外部调用。"""

    def get_context(self, now: datetime) -> TimeContext:
        hour = now.hour
        minute = now.minute
        return TimeContext(
            datetime_str=f"{now.year}年{now.month}月{now.day}日 {hour:02d}:{minute:02d}",
            weekday=_WEEKDAY_NAMES[now.weekday()],
            time_period=period_name(hour),
            lunar_date=_lunar_date_str(now),
            season=_SEASON_NAMES[now.month],
            upcoming_events=_upcoming_events(now),
        )
