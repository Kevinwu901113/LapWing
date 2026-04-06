"""主动消息质量门控 — 发送前检查消息是否自然。"""

import logging

logger = logging.getLogger("lapwing.heartbeat.proactive_filter")

_FILTER_PROMPT = """\
你是质量检测模块。判断以下主动消息是否像一个真人女朋友发的微信。

消息内容：
{message}

检查这些问题（任意一个为 true 就不通过）：
1. 信息密度过高？（一条消息塞了超过两件事）
2. 语气像客服或播报？（"为您""想跟你分享一下""要不要我帮你"）
3. 用了网络黑话堆砌？（"蹲个准信""闪测新品上架"连续出现多个）
4. 像在做报告？（有列表、编号、"第一""第二"）
5. 太长？（超过 4 句话）

只回答 PASS 或 FAIL（附一句原因）。
"""


async def filter_proactive_message(router, message: str) -> tuple[bool, str]:
    """检查主动消息质量。返回 (passed, reason)。"""
    prompt = _FILTER_PROMPT.format(message=message)
    try:
        result = await router.query_lightweight(
            system="你是质量检测模块。只回答 PASS 或 FAIL。",
            user=prompt,
            slot="lightweight_judgment",
        )
        result = result.strip()
        passed = result.upper().startswith("PASS")
        return passed, result
    except Exception as exc:
        logger.warning("主动消息质量检查失败: %s", exc)
        return True, "check_failed"  # 检查失败时放行
