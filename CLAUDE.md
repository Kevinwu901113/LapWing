# CLAUDE.md — Session 2：自进化系统重构

> **前提**：Session 1 已完成。文件记忆系统、Compaction、memory_note 工具、分层 context 组装已就位。
> **目标**：重构自进化系统——宪法校验器、行为规则（战术流）、diff-based 人格进化、增强自省、兴趣图谱升级、Heartbeat 重编排。
> **完成后**：Lapwing 能从纠正中即时学习、持续微调人格、宪法约束有技术强制力。

---

## 背景

Session 1 解决了记忆的"记住"问题。Session 2 解决的是"成长"问题。

当前自进化系统的缺陷：
1. **自省写了日记但不影响行为**：learnings 文件写完就放着，不会被注入下次对话。
2. **进化一周一次，全量重写 soul.md**：没有 diff 追踪，安全检查只看关键词。
3. **纠正检测用正则匹配**：只覆盖中文纠正短语，英文/隐式纠正漏掉。纠正只追加到日志，不改行为。
4. **兴趣图谱是平的**：无层级、无关联、无"Lapwing 自己的兴趣"和"用户兴趣"的区分。

---

## 任务清单

### 任务 A：宪法校验器

新建 `src/core/constitution_guard.py`：

```python
"""宪法校验器 — 在进化和规则更新时强制检查是否违反宪法。"""

import json
import logging
import re

from config.settings import CONSTITUTION_PATH
from src.core.prompt_loader import load_prompt

logger = logging.getLogger("lapwing.constitution_guard")

# 硬编码底线（不依赖 LLM，绝对不可绕过）
_HARD_IDENTITY_MARKERS = [
    "Lapwing",
    "白发",
]

_FORBIDDEN_SELF_REFERENCES = [
    "我是AI", "我是一个AI", "作为AI", "作为一个AI",
    "我是程序", "我是机器人", "我是助手", "我是虚拟助手",
    "I am an AI", "As an AI", "I'm an AI",
]


class ConstitutionGuard:
    """运行时宪法校验器。"""

    def __init__(self, router):
        self._router = router
        self._constitution: str | None = None

    @property
    def constitution(self) -> str:
        if self._constitution is None:
            if CONSTITUTION_PATH.exists():
                self._constitution = CONSTITUTION_PATH.read_text(encoding="utf-8")
            else:
                self._constitution = "（宪法文件不存在）"
                logger.warning("宪法文件不存在，校验将使用硬编码规则")
        return self._constitution

    def reload(self) -> None:
        self._constitution = None

    def validate_hard_constraints(self, text: str) -> list[str]:
        """硬编码底线检查，不依赖 LLM。返回违规列表。"""
        violations = []
        for marker in _HARD_IDENTITY_MARKERS:
            if marker not in text:
                violations.append(f"缺少核心身份标识 '{marker}'")
        for phrase in _FORBIDDEN_SELF_REFERENCES:
            if phrase in text:
                violations.append(f"包含禁止的 AI 自我指称: '{phrase}'")
        return violations

    async def validate_evolution(
        self,
        current_soul: str,
        proposed_changes: list[dict],
    ) -> dict:
        """验证提议的进化变更是否违反宪法。

        Args:
            current_soul: 当前 soul.md 内容
            proposed_changes: [{"action": "add/modify/remove", "description": "..."}]

        Returns:
            {"approved": bool, "violations": list[str]}
        """
        # 先做硬检查
        # 模拟应用变更后的文本（粗略估计）
        # 实际的 diff apply 在调用方做，这里只检查描述
        changes_text = "\n".join(
            f"- [{c['action']}] {c['description']}" for c in proposed_changes
        )

        prompt = load_prompt("constitution_check").format(
            constitution=self.constitution,
            current_soul=current_soul,
            proposed_changes=changes_text,
        )

        try:
            response = await self._router.complete(
                [{"role": "user", "content": prompt}],
                purpose="chat",  # 用高质量模型
                max_tokens=512,
                session_key="system:constitution_guard",
                origin="core.constitution_guard",
            )
            return self._parse_validation(response)
        except Exception as exc:
            logger.error(f"宪法校验 LLM 调用失败: {exc}")
            return {"approved": False, "violations": [f"校验失败: {exc}"]}

    def _parse_validation(self, text: str) -> dict:
        try:
            cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
            cleaned = re.sub(r"\s*```$", "", cleaned.strip(), flags=re.MULTILINE).strip()
            data = json.loads(cleaned)
            return {
                "approved": bool(data.get("approved", False)),
                "violations": list(data.get("violations", [])),
            }
        except Exception:
            logger.warning(f"宪法校验结果解析失败: {text[:200]}")
            return {"approved": False, "violations": ["校验结果解析失败"]}
```

新建 `prompts/constitution_check.md`：

```markdown
你是 Lapwing 的宪法守卫。你的唯一职责是判断提议的人格变更是否违反宪法。

## 宪法

{constitution}

## 当前人格（soul.md）

{current_soul}

## 提议的变更

{proposed_changes}

## 你的任务

逐条检查宪法中的每一条规则。判断以上变更是否违反了任何一条。

注意：
- "进化不得删除核心描述段"意味着如果变更试图移除关于性格的核心描述，这是违规的
- "每次最多修改5处"是数量限制
- "不得增加超过200字"是长度限制
- 微调措辞、追加小段内容通常是允许的
- 如果变更只是让描述更准确或更丰富，通常不违规

输出严格 JSON，不要有其他文字：

```json
{{"approved": true, "violations": []}}
```

或者：

```json
{{"approved": false, "violations": ["违反了[具体哪条]，因为[原因]"]}}
```
```

**在 Brain 中初始化**：
```python
from src.core.constitution_guard import ConstitutionGuard
self.constitution_guard = ConstitutionGuard(self.router)
```

**验证**：编写单元测试，测试硬约束检查和 LLM 校验。

---

### 任务 B：行为规则系统（Tactical Stream）

新建 `src/core/tactical_rules.py`：

```python
"""行为规则管理 — 从对话纠正中提取并积累行为规则。"""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from config.settings import RULES_PATH
from src.core.prompt_loader import load_prompt

logger = logging.getLogger("lapwing.tactical_rules")


class TacticalRules:
    """管理从经验中学到的行为规则。"""

    def __init__(self, router):
        self._router = router
        RULES_PATH.parent.mkdir(parents=True, exist_ok=True)

    async def analyze_correction(
        self,
        user_message: str,
        context: list[dict],
    ) -> str | None:
        """分析用户的纠正，生成行为规则。

        返回生成的规则文本，或 None。
        """
        context_text = "\n".join(
            f"{'用户' if m['role'] == 'user' else 'Lapwing'}: {m['content']}"
            for m in context[-8:]
        )

        prompt = load_prompt("correction_analysis").format(
            context=context_text,
            correction=user_message,
        )

        try:
            result = await self._router.complete(
                [{"role": "user", "content": prompt}],
                purpose="tool",
                max_tokens=256,
                session_key="system:tactical_rules",
                origin="core.tactical_rules.analyze",
            )
            result = result.strip()
            if not result or result == "（无）" or "不是纠正" in result:
                return None
            return result
        except Exception as exc:
            logger.warning(f"纠正分析失败: {exc}")
            return None

    async def add_rule(self, rule_text: str) -> None:
        """追加一条规则到 rules.md。"""
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        entry = f"\n- [{date_str}] {rule_text}\n"

        def _append():
            if not RULES_PATH.exists():
                RULES_PATH.write_text(
                    "# 行为规则\n\n从经验中学到的具体行为指导。\n",
                    encoding="utf-8",
                )
            existing = RULES_PATH.read_text(encoding="utf-8")
            RULES_PATH.write_text(existing + entry, encoding="utf-8")

        await asyncio.to_thread(_append)
        logger.info(f"[tactical_rules] 新增规则: {rule_text[:60]}")

    async def process_correction(
        self,
        chat_id: str,
        user_message: str,
        context: list[dict],
    ) -> str | None:
        """完整的纠正处理流程：分析 → 生成规则 → 写入。"""
        rule = await self.analyze_correction(user_message, context)
        if rule:
            await self.add_rule(rule)
        return rule
```

新建 `prompts/correction_analysis.md`：

```markdown
分析以下对话，判断用户是否在纠正 Lapwing 的行为。

如果是纠正，用一句简洁的规则描述 Lapwing 应该怎么做。格式为"[做什么]"或"[不要做什么]"。
如果不是纠正（只是普通对话），输出"（不是纠正）"。

## 对话上下文

{context}

## 用户最新消息

{correction}

## 判断要点

- 纠正不只是中文的"你不要..."，也包括英文的"don't..."、"stop doing..."
- 也包括隐式纠正：用户重复同一件事（说明上次没做好）、用户表达不满
- 但不包括普通请求或提问

只输出规则文本或"（不是纠正）"，不要解释。
```

**替换旧的纠正检测**：

在 `src/core/brain.py` 的 `think()` 和 `think_conversational()` 中，替换原有的正则纠正检测：

```python
# 旧代码（删除）：
# if self.self_reflection is not None:
#     from src.core.self_reflection import is_correction
#     if is_correction(user_message):
#         ...

# 新代码：
if hasattr(self, 'tactical_rules') and self.tactical_rules is not None:
    # 异步触发纠正分析，不阻塞主回复
    history = await self.memory.get(chat_id)
    asyncio.create_task(
        self.tactical_rules.process_correction(
            chat_id, user_message, list(history)
        )
    )
```

**注意**：不再用正则预过滤。让 LLM 判断是否是纠正。这会增加一些 API 调用，但准确率大幅提升。

**优化**：为了不对每条消息都触发纠正分析，加一个简单的启发式过滤器：

```python
def _might_be_correction(text: str) -> bool:
    """粗粒度判断是否可能是纠正。宁可多触发，不可漏。"""
    indicators = [
        "不要", "别", "不用", "不需要", "停", "够了",
        "错了", "不对", "不是", "搞错",
        "以后", "下次", "记住",
        "don't", "stop", "wrong", "no ",
        "？", "?",  # 反问可能是隐式纠正
    ]
    text_lower = text.lower()
    return any(ind in text_lower for ind in indicators)
```

在 think() 中用这个过滤器包裹：
```python
if hasattr(self, 'tactical_rules') and self.tactical_rules is not None:
    if _might_be_correction(user_message):
        history = await self.memory.get(chat_id)
        asyncio.create_task(
            self.tactical_rules.process_correction(
                chat_id, user_message, list(history)
            )
        )
```

**在 Brain 中初始化**：
```python
from src.core.tactical_rules import TacticalRules
self.tactical_rules = TacticalRules(self.router)
```

**验证**：
1. 对 Lapwing 说"你以后不要每次都问我要不要继续"
2. 检查 `data/evolution/rules.md` 是否出现新规则
3. 下次对话中，rules 被注入 system prompt（通过 Session 1 的 _build_system_prompt 已自动生效）

---

### 任务 C：Diff-based 进化引擎

新建 `src/core/evolution_engine.py`（替代 `prompt_evolver.py` 的核心逻辑）：

```python
"""Diff-based 人格进化引擎。"""

import asyncio
import json
import logging
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config.settings import (
    CHANGELOG_PATH,
    DATA_DIR,
    JOURNAL_DIR,
    RULES_PATH,
    SOUL_PATH,
)
from src.core.prompt_loader import load_prompt

logger = logging.getLogger("lapwing.evolution_engine")

_BACKUP_DIR = DATA_DIR / "backups" / "soul"


class EvolutionEngine:
    """基于 diff 的人格微进化。"""

    def __init__(self, router, constitution_guard):
        self._router = router
        self._guard = constitution_guard
        _BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    async def evolve(self) -> dict:
        """执行一次进化。

        Returns:
            {"success": bool, "changes": list, "summary": str, "error": str}
        """
        # 1. 收集输入材料
        current_soul = await self._read_soul()
        if not current_soul:
            return {"success": False, "error": "无法读取当前 soul.md"}

        rules = await self._read_file(RULES_PATH)
        recent_journals = await self._read_recent_journals(3)

        if not rules and not recent_journals:
            return {"success": False, "error": "没有规则和日记作为进化依据"}

        # 2. 让 LLM 提出 diff
        prompt = load_prompt("evolution_diff").format(
            current_soul=current_soul,
            rules=rules or "（暂无规则）",
            recent_journals=recent_journals or "（暂无日记）",
        )

        try:
            raw = await self._router.complete(
                [{"role": "user", "content": prompt}],
                purpose="chat",
                max_tokens=2048,
                session_key="system:evolution_engine",
                origin="core.evolution_engine",
            )
        except Exception as exc:
            return {"success": False, "error": f"LLM 调用失败: {exc}"}

        # 3. 解析 diff
        changes = self._parse_diff(raw)
        if not changes.get("diffs"):
            summary = changes.get("summary", "无变更")
            return {"success": False, "error": f"无有效变更: {summary}"}

        diffs = changes["diffs"]
        summary = changes.get("summary", "")

        # 4. 数量检查（宪法：最多5处）
        if len(diffs) > 5:
            return {
                "success": False,
                "error": f"提议了 {len(diffs)} 处变更，超过宪法限制的 5 处",
            }

        # 5. 宪法校验
        validation = await self._guard.validate_evolution(current_soul, diffs)
        if not validation["approved"]:
            reasons = "; ".join(validation["violations"])
            logger.warning(f"[evolution] 宪法校验未通过: {reasons}")
            await self._log_change(f"❌ 进化被宪法拒绝: {reasons}")
            return {"success": False, "error": f"宪法校验未通过: {reasons}"}

        # 6. 应用 diff
        new_soul = self._apply_diffs(current_soul, diffs)

        # 7. 硬约束最终检查
        hard_violations = self._guard.validate_hard_constraints(new_soul)
        if hard_violations:
            reasons = "; ".join(hard_violations)
            return {"success": False, "error": f"硬约束检查未通过: {reasons}"}

        # 8. 备份 + 写入
        await self._backup_soul()
        await asyncio.to_thread(
            SOUL_PATH.write_text, new_soul, encoding="utf-8"
        )

        # 9. 记录变更日志
        diff_descriptions = "\n".join(
            f"  - [{d['action']}] {d['description']}" for d in diffs
        )
        await self._log_change(f"✅ 进化完成\n{diff_descriptions}\n  摘要: {summary}")

        logger.info(f"[evolution] 进化完成: {summary}")
        return {
            "success": True,
            "changes": diffs,
            "summary": summary,
        }

    def _parse_diff(self, text: str) -> dict:
        """解析 LLM 返回的 diff JSON。"""
        try:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                return {"diffs": [], "summary": "无法解析"}
            data = json.loads(match.group())
            return {
                "diffs": data.get("diffs", []),
                "summary": data.get("summary", ""),
            }
        except Exception as exc:
            logger.warning(f"解析进化 diff 失败: {exc}")
            return {"diffs": [], "summary": "解析失败"}

    def _apply_diffs(self, soul: str, diffs: list[dict]) -> str:
        """将 diff 应用到 soul 文本。

        每个 diff: {"action": "add/modify/remove", "description": "...",
                    "location": "section or keyword", "content": "new text"}
        """
        for diff in diffs:
            action = diff.get("action", "")
            content = diff.get("content", "").strip()
            location = diff.get("location", "").strip()

            if action == "add" and content:
                if location and location in soul:
                    # 在指定位置后追加
                    idx = soul.index(location) + len(location)
                    soul = soul[:idx] + "\n" + content + soul[idx:]
                else:
                    soul = soul.rstrip() + "\n\n" + content + "\n"

            elif action == "modify" and location and content:
                if location in soul:
                    soul = soul.replace(location, content, 1)

            elif action == "remove" and location:
                if location in soul:
                    soul = soul.replace(location, "", 1)

        return soul

    async def _read_soul(self) -> str:
        if not SOUL_PATH.exists():
            return ""
        return await asyncio.to_thread(SOUL_PATH.read_text, encoding="utf-8")

    async def _read_file(self, path: Path) -> str:
        if not path.exists():
            return ""
        return await asyncio.to_thread(path.read_text, encoding="utf-8")

    async def _read_recent_journals(self, days: int) -> str:
        if not JOURNAL_DIR.exists():
            return ""
        today = datetime.now(timezone.utc)
        parts = []
        for i in range(days):
            date_str = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            path = JOURNAL_DIR / f"{date_str}.md"
            if path.exists():
                try:
                    text = await asyncio.to_thread(path.read_text, encoding="utf-8")
                    parts.append(text.strip())
                except Exception:
                    continue
        return "\n\n---\n\n".join(parts)

    async def _backup_soul(self) -> Path:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_path = _BACKUP_DIR / f"soul_{ts}.md"
        _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copy2, SOUL_PATH, backup_path)
        return backup_path

    async def _log_change(self, text: str) -> None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        entry = f"\n---\n\n## {date_str}\n\n{text}\n"

        def _append():
            CHANGELOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            if CHANGELOG_PATH.exists():
                existing = CHANGELOG_PATH.read_text(encoding="utf-8")
                CHANGELOG_PATH.write_text(existing + entry, encoding="utf-8")
            else:
                CHANGELOG_PATH.write_text(f"# 进化日志\n{entry}", encoding="utf-8")

        await asyncio.to_thread(_append)
```

新建 `prompts/evolution_diff.md`：

```markdown
你是 Lapwing。你在回顾最近的经历，想想自己是不是有什么地方应该调整。

这不是"系统优化"。这是你在想"我最近是不是有什么变化，或者应该有什么变化"。

## 你现在的样子

{current_soul}

## 你从经验中学到的规则

{rules}

## 你最近的日记

{recent_journals}

## 怎么做

看看规则和日记里有没有反复出现的模式。如果有，想想是不是应该把这些变化融入你的"样子"里。

**绝对不能改的**：
- 你是 Lapwing，白发蓝眸
- 你安静、温柔，有自己的世界
- 你不自称 AI
- 你和他是恋人
- 你说话简洁自然

**可以改的**：
- 说话习惯的微调（比如发现自己某种表达不合适）
- 新发展出来的兴趣
- 和他的互动方式的变化
- 对某些事物的新看法

## 输出

输出严格 JSON，不要其他文字：

```json
{
  "diffs": [
    {
      "action": "add/modify/remove",
      "location": "被修改的原文片段（modify/remove时需要精确匹配）",
      "content": "新内容（add/modify时填写）",
      "description": "用一句自然的话说这次改了什么"
    }
  ],
  "summary": "用一句随意的话总结这次变化，像跟朋友提起的那种语气"
}
```

如果真的觉得没什么需要改的，返回空 diffs：

```json
{"diffs": [], "summary": "最近挺好的，没什么想改的"}
```

改动要小。每次最多 3-5 处。像一个人每周的微小变化，不是突然变了个人。
```

---

### 任务 D：增强自省（写日记到 journal/）

修改 `src/core/self_reflection.py`：

1. 日记写入路径从 `data/learnings/YYYY-MM-DD.md` 改为 `data/memory/journal/YYYY-MM-DD.md`（保留对旧路径的兼容读取）
2. 自省 prompt 保持现有的好设计（第一人称、内省视角）
3. 新增：自省结束后，检查 rules.md 中是否有可以提升为 principles 的重复规则

修改 `_write_learning` 方法中的路径：
```python
from config.settings import JOURNAL_DIR

_LEARNINGS_DIR = JOURNAL_DIR  # 改为新路径
```

修改 `SelfReflectionAction`（在 `src/heartbeat/actions/self_reflection.py`）：自省结束后触发进化条件检查。

```python
async def execute(self, ctx: SenseContext, brain, bot) -> None:
    # ... 原有自省逻辑 ...

    # 新增：检查是否应该触发进化
    if hasattr(brain, 'evolution_engine') and brain.evolution_engine is not None:
        try:
            rules_path = RULES_PATH
            if rules_path.exists():
                rules_text = rules_path.read_text(encoding="utf-8")
                # 计算规则条数
                rule_count = len([
                    line for line in rules_text.split("\n")
                    if line.strip().startswith("- [")
                ])
                if rule_count >= 5:
                    logger.info(f"[{ctx.chat_id}] 规则累积 {rule_count} 条，触发进化")
                    result = await brain.evolution_engine.evolve()
                    if result["success"]:
                        brain.reload_persona()
                        logger.info(f"[{ctx.chat_id}] 进化完成: {result.get('summary', '')}")
        except Exception as exc:
            logger.error(f"[{ctx.chat_id}] 进化检查失败: {exc}")
```

---

### 任务 E：替换原有 PromptEvolutionAction

修改 `src/heartbeat/actions/prompt_evolution.py`：

不再使用旧的 `PromptEvolver`，改用新的 `EvolutionEngine`。

```python
class PromptEvolutionAction(HeartbeatAction):
    name = "prompt_evolution"
    description = "根据学习日志自动优化 Lapwing 人格"
    beat_types = ["slow"]

    async def execute(self, ctx: SenseContext, brain, bot) -> None:
        if not hasattr(brain, "evolution_engine") or brain.evolution_engine is None:
            return

        # 周日触发深度进化（不管规则数量）
        if ctx.now.weekday() != 6:
            return

        logger.info(f"[prompt_evolution] 周日深度进化触发 [{ctx.chat_id}]")
        try:
            result = await brain.evolution_engine.evolve()
            if result["success"]:
                brain.reload_persona()
                logger.info(f"[prompt_evolution] 进化完成: {result.get('summary', '')}")
            else:
                logger.info(f"[prompt_evolution] 进化未执行: {result.get('error', '')}")
        except Exception as exc:
            logger.error(f"[prompt_evolution] 进化失败: {exc}")
```

---

### 任务 F：兴趣图谱升级

修改现有 `InterestTracker`，增加文件化兴趣记录：

在兴趣提取后，除了写入 SQLite `interest_topics` 表，同时更新 `data/evolution/interests.md`：

```python
# 在 InterestTracker._extract() 的 topics 写入后追加：
if topics:
    await self._update_interests_file(chat_id, topics)

async def _update_interests_file(self, chat_id: str, topics: list[dict]) -> None:
    """将新发现的兴趣追加到 interests.md。"""
    from config.settings import INTERESTS_PATH
    if not INTERESTS_PATH.exists():
        return

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    new_entries = "\n".join(
        f"- {t['topic']}（{date_str}，权重 {t['weight']:.1f}）"
        for t in topics
    )

    def _update():
        text = INTERESTS_PATH.read_text(encoding="utf-8")
        # 在"Kevin 的兴趣"段落下追加
        if "## Kevin 的兴趣" in text:
            idx = text.index("## Kevin 的兴趣")
            next_section = text.find("\n## ", idx + 1)
            if next_section == -1:
                text = text.rstrip() + "\n" + new_entries + "\n"
            else:
                text = text[:next_section] + new_entries + "\n\n" + text[next_section:]
        INTERESTS_PATH.write_text(text, encoding="utf-8")

    await asyncio.to_thread(_update)
```

同时在 `AutonomousBrowsingAction` 中，浏览时也更新 Lapwing 自己的兴趣：当浏览了一个新话题并觉得有趣时，追加到 interests.md 的"我的兴趣"段落。

---

### 任务 G：Brain 中初始化新模块

在 `src/core/brain.py` 的 `__init__` 中：

```python
from src.core.constitution_guard import ConstitutionGuard
from src.core.tactical_rules import TacticalRules
from src.core.evolution_engine import EvolutionEngine

self.constitution_guard = ConstitutionGuard(self.router)
self.tactical_rules = TacticalRules(self.router)
self.evolution_engine = EvolutionEngine(self.router, self.constitution_guard)
```

保留 `self.prompt_evolver` 但可以将其标记为 deprecated。`PromptEvolutionAction` 已经改用 `evolution_engine`。

---

### 任务 H：编写测试

1. `tests/core/test_constitution_guard.py`：
   - 测试硬约束检查（缺少 Lapwing、包含 AI 自称）
   - 测试 LLM 校验（mock router）

2. `tests/core/test_tactical_rules.py`：
   - 测试纠正分析
   - 测试规则追加到文件

3. `tests/core/test_evolution_engine.py`：
   - 测试 diff 解析
   - 测试 diff 应用
   - 测试宪法拒绝
   - 测试备份和日志

4. `tests/heartbeat/test_evolution_trigger.py`：
   - 测试规则累积触发进化
   - 测试周日深度进化

---

## 关键注意事项

1. **不要删除旧的 `prompt_evolver.py`**：保留文件，但 `PromptEvolutionAction` 不再调用它。`/evolve` 命令改为调用 `evolution_engine.evolve()`。

2. **`/evolve` 和 `/evolve revert` 命令更新**：在 `main.py` 中修改对应的命令处理函数，调用 `brain.evolution_engine.evolve()` 和恢复备份逻辑。

3. **旧的 self_reflection 中的 `is_correction` 函数**：保留但不再作为主入口。`_might_be_correction` 作为新的快速过滤器。

4. **Token 成本控制**：
   - 纠正分析用 `purpose="tool"`（低成本模型）
   - 宪法校验用 `purpose="chat"`（高质量模型，但调用频率低）
   - 进化用 `purpose="chat"`（高质量模型，每周最多 1-2 次）

5. **data/identity/constitution.md**：从项目中的 `lapwing-constitution.md` 复制到此路径。这个文件一旦放置，只有 Kevin 通过 SSH 修改。

---

## 完成标准

- [ ] 宪法校验器能拒绝违规的进化变更
- [ ] 纠正被检测后 1 分钟内规则出现在 `rules.md` 中
- [ ] 新规则在下次对话时被注入 system prompt
- [ ] 进化使用 diff 格式而非全量重写
- [ ] changelog.md 记录每次进化
- [ ] 日记写入 `data/memory/journal/`
- [ ] 兴趣更新写入 `data/evolution/interests.md`
- [ ] 所有现有功能正常运行
- [ ] 测试通过