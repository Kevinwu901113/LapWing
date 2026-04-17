"""ScopeRouter 单元测试。"""

import pytest

from src.research.scope_router import ScopeRouter


@pytest.fixture
def router():
    return ScopeRouter()


async def test_pure_chinese_returns_both(router):
    assert await router.decide("今天上海天气怎么样") == "both"


async def test_pure_english_returns_global(router):
    assert await router.decide("who won the super bowl") == "global"


async def test_cn_platform_keyword_returns_cn(router):
    assert await router.decide("B 站最近有什么热门视频") == "cn"
    assert await router.decide("小红书上的护肤推荐") == "cn"


async def test_global_platform_keyword_returns_global(router):
    assert await router.decide("ESPN 道奇队赛程") == "global"
    assert await router.decide("MLB 今天的比赛") == "global"
    assert await router.decide("Reddit 上的讨论") == "global"


async def test_mixed_platform_returns_both(router):
    assert await router.decide("B 站 vs YouTube 谁赚钱") == "both"


async def test_empty_question_defaults_to_both(router):
    assert await router.decide("") == "both"


async def test_short_chinese_returns_both(router):
    assert await router.decide("油价") == "both"


async def test_english_about_chinese_topic_returns_global_by_language(router):
    """语言决策：纯英文 → global（关键词缺失时）。"""
    assert await router.decide("Dreame robot vacuum review") == "global"


async def test_case_insensitive_platform_detection(router):
    # 只有 global 平台关键词 → global（即便有中文字符）
    assert await router.decide("youtube 视频推荐") == "global"
    assert await router.decide("youtube videos") == "global"


async def test_cn_keyword_in_english_text(router):
    """中文平台关键词嵌在英文里，命中 CN。"""
    assert await router.decide("how to use bilibili abroad") == "cn"
