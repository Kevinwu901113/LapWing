"""Simulated conversation runner.

Uses DeepEval's ConversationSimulator to generate multi-turn dialogues
from scenario descriptions, then evaluates the generated conversations.

Note: simulated runs require a live LLM connection and are expensive.
Use `pytest evals/ --run-evals -k simulated` to run explicitly.
"""
from __future__ import annotations

import json
from pathlib import Path

from deepeval.test_case import ConversationalTestCase, Turn

from evals.config import JUDGE
from evals.fixtures.lapwing_client import lapwing_callback, reset_session

SCENARIOS_DIR = Path(__file__).parent.parent / "goldens" / "scenarios"


def load_scenarios() -> list[dict]:
    scenarios = []
    for path in sorted(SCENARIOS_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        scenarios.append(data)
    return scenarios


async def simulate_scenario(scenario: dict) -> ConversationalTestCase:
    """Run a single scenario through Lapwing and collect turns.

    Uses the real Lapwing brain to generate responses to simulated
    user messages. The judge model plays the user role based on the
    scenario description.
    """
    reset_session()

    turns: list[Turn] = []
    num_turns = scenario.get("num_turns", 6)

    first_user_msg = await JUDGE.a_generate(
        f"You are roleplaying as Kevin in this scenario: {scenario['scenario']}. "
        f"Kevin's description: {scenario['user_description']}. "
        f"Write Kevin's opening message. Just the message text, nothing else. "
        f"Write in Chinese, short and casual like a WeChat message."
    )
    first_user_msg = first_user_msg.strip().strip('"')

    for i in range(num_turns):
        if i == 0:
            user_msg = first_user_msg
        else:
            last_assistant = turns[-1].content if turns and turns[-1].role == "assistant" else ""
            user_msg = await JUDGE.a_generate(
                f"You are roleplaying as Kevin. Scenario: {scenario['scenario']}. "
                f"Kevin's description: {scenario['user_description']}. "
                f"The conversation so far:\n"
                + "\n".join(f"{'Kevin' if t.role == 'user' else 'Lapwing'}: {t.content}" for t in turns)
                + f"\n\nWrite Kevin's next message. Just the message text, nothing else. "
                f"Write in Chinese, short and casual."
            )
            user_msg = user_msg.strip().strip('"')

        turns.append(Turn(role="user", content=user_msg))

        assistant_reply = await lapwing_callback(user_msg)
        if assistant_reply:
            turns.append(Turn(role="assistant", content=assistant_reply))

    return ConversationalTestCase(
        turns=turns,
        chatbot_role="Lapwing：Kevin 的 AI 伴侣，白发淡蓝眼睛，温暖有情绪反应，短句对话风格",
        name=scenario["name"],
        scenario=scenario["scenario"],
        expected_outcome=scenario["expected_outcome"],
        user_description=scenario["user_description"],
    )


async def simulate_all() -> list[ConversationalTestCase]:
    cases = []
    for scenario in load_scenarios():
        case = await simulate_scenario(scenario)
        cases.append(case)
    return cases
