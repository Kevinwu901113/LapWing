"""Phase-0 minimal prompt assembly.

A stripped-down path used by the ``PHASE0_MODE`` test harness. Loads
``soul_test.md`` + ``constitution_test.md`` from the identity directory
and appends a single time-anchor line. No runtime state, no trajectory,
no voice — just identity + time.

Moved out of ``prompt_builder.py`` in Step 3 M2.f so the PromptBuilder
class can be deleted without breaking Phase-0 boots.
"""

from __future__ import annotations


def build_phase0_prompt() -> str:
    """Phase 0 极简 prompt：只有身份 + 宪法 + 时间。"""
    from config.settings import IDENTITY_DIR
    from src.core.vitals import get_period_name, now_local

    soul_path = IDENTITY_DIR / "soul_test.md"
    constitution_path = IDENTITY_DIR / "constitution_test.md"

    parts = []
    for p in (soul_path, constitution_path):
        try:
            parts.append(p.read_text(encoding="utf-8").strip())
        except FileNotFoundError:
            pass

    now = now_local()
    weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekday = weekday_names[now.weekday()]
    period = get_period_name(now.hour)
    parts.append(
        f"当前时间：{now.year}年{now.month}月{now.day}日 {weekday} "
        f"{period}（约{now.hour}时）"
    )

    return "\n\n---\n\n".join(parts)
