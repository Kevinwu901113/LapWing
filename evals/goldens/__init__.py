"""Golden dataset loader for Lapwing regression suite."""
from __future__ import annotations

import json
from pathlib import Path

from deepeval.test_case import ConversationalTestCase, Turn

FIXED_DIR = Path(__file__).parent / "fixed"


def load_fixed_goldens() -> list[ConversationalTestCase]:
    """Load all fixed goldens as DeepEval ConversationalTestCase."""
    cases: list[ConversationalTestCase] = []
    for path in sorted(FIXED_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        cases.append(
            ConversationalTestCase(
                turns=[
                    Turn(role=t["role"], content=t["content"])
                    for t in data["turns"]
                ],
                chatbot_role=data.get("chatbot_role", ""),
                name=data["name"],
            )
        )
    return cases
