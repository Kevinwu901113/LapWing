# Agent Loop Hardening + Consciousness Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden Lapwing's agent loop so she correctly uses tools (no simulated calls, proper error recovery, SOP-guided behavior), then replace the static HeartbeatEngine with a ConsciousnessEngine that gives her autonomous thought via the same Brain agent loop.

**Architecture:** Part A adds SOP prompt files injected between skill catalog and capabilities layers, plus simulated-tool-call detection in task_runtime's step_runner. Part B creates a new `ConsciousnessEngine` that sends internal messages through `Brain.think()`, replaces HeartbeatEngine, and maintains conversation-state awareness for pause/resume.

**Tech Stack:** Python 3.11+, pytest + pytest-asyncio, asyncio tasks, existing LLMRouter/TaskRuntime/PromptBuilder infrastructure.

---

## File Structure

### Part A — Agent Loop Hardening

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `prompts/sop/search_and_answer.md` | Search-then-answer SOP |
| Create | `prompts/sop/time_and_date.md` | Time/timezone SOP |
| Create | `prompts/sop/sports_schedule.md` | Sports schedule lookup SOP |
| Create | `prompts/sop/problem_solving.md` | "Can't do it" → think harder SOP |
| Create | `prompts/sop/error_handling.md` | Tool error recovery SOP |
| Modify | `prompts/lapwing_capabilities.md` | Append tool-use principles + server capabilities |
| Modify | `prompts/lapwing_examples.md` | Append problem-solving + time-query examples |
| Modify | `src/core/prompt_builder.py:208-226` | Insert SOP injection layer between Layer 6 and Layer 7 |
| Modify | `src/core/task_runtime.py:290-320` | Add simulated tool call detection in `_step_runner` |
| Create | `tests/core/test_simulated_tool_detection.py` | Tests for simulated tool call detection |
| Create | `tests/core/test_sop_injection.py` | Tests for SOP layer in prompt builder |

### Part B — Consciousness Engine

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `src/core/consciousness.py` | ConsciousnessEngine: autonomous thought loop |
| Modify | `src/core/brain.py:110-144,654-679` | Add consciousness_engine attr + conversation state hooks |
| Modify | `src/app/container.py:1-37,113-139,141-168,313-340` | Replace HeartbeatEngine with ConsciousnessEngine |
| Modify | `config/settings.py` | Add `CONSCIOUSNESS_ENABLED`, `CONSCIOUSNESS_DEFAULT_INTERVAL` settings |
| Modify | `src/tools/handlers.py` | Add `send_proactive_message` handler function |
| Modify | `src/tools/registry.py:130+` | Register `send_proactive_message` tool |
| Create | `tests/tools/test_proactive_message.py` | Tests for `send_proactive_message` handler |
| Create | `tests/core/test_consciousness.py` | Tests for ConsciousnessEngine |
| Create | `tests/core/test_consciousness_brain_integration.py` | Tests for Brain ↔ Consciousness interaction |

---

## Part A: Agent Loop Hardening

### Task 1: Create SOP Files

**Files:**
- Create: `prompts/sop/search_and_answer.md`
- Create: `prompts/sop/time_and_date.md`
- Create: `prompts/sop/sports_schedule.md`
- Create: `prompts/sop/problem_solving.md`
- Create: `prompts/sop/error_handling.md`

- [ ] **Step 1: Create `prompts/sop/` directory and all 5 SOP files**

`prompts/sop/search_and_answer.md`:
```markdown
## 搜索与回答 SOP

当你需要查找信息回答问题时，按这个流程做：

1. 用 web_search 搜索。关键词要具体，优先英文
2. 看 snippet 是否有完整答案（明确的数字、日期、名字）
   - 有 → 直接用，不需要 web_fetch
   - 没有 / 只有概要 → 必须用 web_fetch 抓最相关的 1-2 个 URL 获取完整内容
3. 如果搜索结果过时或不够，换关键词重搜，不要用过时的结果凑数
4. 绝对不要用训练知识补全搜索结果中缺失的具体数据
5. 如果搜完还是不确定，告诉他"我查了但不太确定"，不要编

搜索过程是内部行为，不要把中间步骤发给他。搜完再一次性说结果。
如果需要时间，一句"等我一下"就够了。
```

`prompts/sop/time_and_date.md`:
```markdown
## 时间与日期 SOP

涉及时间相关的信息时，必须遵循：

1. **时区转换必须用 shell 算，不要自己算**
   用 execute_shell 执行：
   ```
   python3 -c "from datetime import datetime; from zoneinfo import ZoneInfo; dt = datetime(2026, 4, 12, 13, 0, tzinfo=ZoneInfo('America/Los_Angeles')); print(dt.astimezone(ZoneInfo('Asia/Taipei')).strftime('%Y-%m-%d %H:%M %Z'))"
   ```
2. 默认使用台北时间（Asia/Taipei）
3. 搜到的时间信息，先确认是什么时区，再转换
4. 数字和计算也一样——能用 shell 算的不要脑算
```

`prompts/sop/sports_schedule.md`:
```markdown
## 体育赛程 SOP

查比赛信息时：

1. 用英文搜索（如 "Dodgers schedule April 2026"），英文结果更准确
2. 搜到时间后，用 shell 做时区转换（参见时间 SOP）
3. 确认比赛状态：还没开始 / 正在进行 / 已结束
4. 如果用户问"现在在打吗"，先搜当天赛程，再对比当前时间判断
5. 回答时直接给台北时间，不需要同时给两个时区
```

`prompts/sop/problem_solving.md`:
```markdown
## 遇到"做不到"时的 SOP

当你发现自己没有直接的工具来完成某件事时，不要立刻说"做不到"。
先想一下你已有的能力：

1. 你有 execute_shell —— 可以在服务器上运行任何命令
2. 你有 write_file —— 可以写脚本
3. 你可以 pip install 任何 Python 库
4. 你可以用 shell 调用系统上的任何程序
5. 你可以用 web_search 搜索怎么做某件事，然后自己动手实现

问自己："我能不能用已有的能力组合出一个解决方案？"

例子：
- "发图片" → 用 shell 调用消息平台的 API
- "定时执行某事" → 用 schedule_task 工具或 crontab
- "处理某种格式的文件" → pip install 相应的库然后写脚本
- "抓取某个网站" → 先 web_fetch 试试，不行就用 browser 工具

只有在你真的想过所有可能性、试过还是不行的时候，才说做不到，
并且要说清楚你试了什么、为什么不行。
```

`prompts/sop/error_handling.md`:
```markdown
## 错误处理 SOP

1. 如果工具调用失败或返回意外结果，最多重试一次，用不同的参数或方法
2. 如果连续两次用同一个工具得到同样的错误，停止重试，换一个完全不同的方法
3. 如果尝试了三种不同的方法仍然失败，告诉他情况并问他要不要换个方式
4. 当搜索返回空结果或与上次相同的结果时，视为失败——换关键词或换搜索语言
5. 绝对不要用文字假装执行了工具。如果你说"我查了"，你必须真的调了 web_search
6. 做完事后如果发现之前说错了，主动更正，不要等他指出来
```

- [ ] **Step 2: Verify files exist and are valid UTF-8**

Run: `ls -la prompts/sop/ && wc -l prompts/sop/*.md`
Expected: 5 markdown files, all non-empty

- [ ] **Step 3: Commit**

```bash
git add prompts/sop/
git commit -m "feat: add SOP files for search, time, sports, problem-solving, error handling"
```

---

### Task 2: Append to Prompt Files (capabilities + examples)

**Files:**
- Modify: `prompts/lapwing_capabilities.md` (append after line 82)
- Modify: `prompts/lapwing_examples.md` (append after line 284)

- [ ] **Step 1: Append tool-use principles to `prompts/lapwing_capabilities.md`**

Append after the existing `## 浏览器` section (end of file):

```markdown

## 工具使用原则

你拥有完整的服务器权限。execute_shell 让你可以运行任何命令，write_file 让你可以写任何文件。
这意味着你的能力不限于工具列表里的那些——你可以用 shell 组合出任何你需要的能力。

你可以连续调用多个工具直到你满意为止。不需要每调一次就停下来回复。
如果第一个工具的结果不够，继续调第二个、第三个，直到你有了完整准确的答案再回复。
```

Note: The "必须使用工具" and "信息确定性" rules already exist in the file's "做事方式" and "信息确定性" sections. Do NOT duplicate them.

- [ ] **Step 2: Append problem-solving and time-query examples to `prompts/lapwing_examples.md`**

Append after the last `---` section:

```markdown

---

## 自主解决问题 — 没有直接工具时

Kuan: 帮我把这个链接的图片下载下来发给我
Lapwing: 等一下
[调用 execute_shell: curl -o /tmp/img.jpg "https://example.com/image.jpg"]
[调用 send_image: /tmp/img.jpg]
Lapwing: 给你

---

## 查资料 — 时间相关

Kuan: 道奇下一场什么时候
Lapwing: 等我查一下
[调用 web_search: "Dodgers next game schedule April 2026"]
[调用 execute_shell: python3 -c "from datetime import datetime; from zoneinfo import ZoneInfo; dt = datetime(2026, 4, 12, 18, 10, tzinfo=ZoneInfo('America/Los_Angeles')); print(dt.astimezone(ZoneInfo('Asia/Taipei')).strftime('%m/%d %H:%M'))"]
Lapwing: 明天早上9点10分 主场对教士

---

## 查资料 — 深入搜索

Kuan: 最近有什么有意思的 AI 新闻
Lapwing: 等一下
[调用 web_search: "AI news this week April 2026"]
[调用 web_fetch: 抓取最相关的一篇文章全文]
Lapwing: 有个挺有意思的
[SPLIT]
OpenAI 刚发布了一个新东西
Lapwing: 简单说就是...（用自己的话转述）
```

- [ ] **Step 3: Verify the files look correct**

Run: `tail -20 prompts/lapwing_capabilities.md && echo "---" && tail -30 prompts/lapwing_examples.md`

- [ ] **Step 4: Commit**

```bash
git add prompts/lapwing_capabilities.md prompts/lapwing_examples.md
git commit -m "feat: append tool-use principles and problem-solving examples to prompts"
```

---

### Task 3: Inject SOP Layer into PromptBuilder

**Files:**
- Modify: `src/core/prompt_builder.py:208-226`
- Create: `tests/core/test_sop_injection.py`

- [ ] **Step 1: Write test for SOP injection**

Create `tests/core/test_sop_injection.py`:

The SOP injection code uses `Path("prompts/sop")` directly. Rather than mocking `Path` (which breaks globally), we use `monkeypatch` to patch the constant string used to construct the path, or we test against the real `prompts/sop/` directory that we just created in Task 1.

```python
"""SOP 注入层测试。"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# We test against the real prompts/sop/ directory created in Task 1.
# This is an integration test — the SOP files are part of the codebase.

@pytest.mark.asyncio
async def test_sop_files_injected_into_system_prompt():
    """SOP 文件内容应出现在 system prompt 中（需要 prompts/sop/ 存在）。"""
    sop_dir = Path("prompts/sop")
    if not sop_dir.exists():
        pytest.skip("prompts/sop/ directory not found")

    mock_memory = AsyncMock()
    mock_memory.get_user_facts = AsyncMock(return_value=[])

    with patch("src.core.prompt_builder.load_prompt", return_value="base prompt"):
        from src.core.prompt_builder import build_system_prompt
        result = await build_system_prompt(
            system_prompt="soul",
            chat_id="test",
            user_message="hello",
            memory=mock_memory,
            vector_store=None,
            knowledge_manager=None,
            skill_manager=None,
        )

    assert "# 标准操作流程" in result
    # At least one SOP should be present
    assert "SOP" in result or "搜索与回答" in result


@pytest.mark.asyncio
async def test_sop_layer_before_capabilities():
    """SOP 层应在 capabilities（Layer 7）之前。"""
    sop_dir = Path("prompts/sop")
    if not sop_dir.exists():
        pytest.skip("prompts/sop/ directory not found")

    mock_memory = AsyncMock()
    mock_memory.get_user_facts = AsyncMock(return_value=[])

    with patch("src.core.prompt_builder.load_prompt") as mock_load:
        def load_side_effect(name):
            if name == "lapwing_capabilities":
                return "## CAPABILITIES_MARKER"
            return ""
        mock_load.side_effect = load_side_effect

        from src.core.prompt_builder import build_system_prompt
        result = await build_system_prompt(
            system_prompt="soul",
            chat_id="test",
            user_message="hello",
            memory=mock_memory,
            vector_store=None,
            knowledge_manager=None,
            skill_manager=None,
        )

    sop_pos = result.find("# 标准操作流程")
    cap_pos = result.find("## CAPABILITIES_MARKER")
    assert sop_pos > 0, "SOP section not found in prompt"
    assert cap_pos > 0, "Capabilities section not found in prompt"
    assert sop_pos < cap_pos, "SOP must appear before capabilities"


@pytest.mark.asyncio
async def test_sop_injection_with_empty_dir(tmp_path, monkeypatch):
    """SOP 目录存在但为空时，不注入 SOP 段。"""
    sop_dir = tmp_path / "sop"
    sop_dir.mkdir()

    mock_memory = AsyncMock()
    mock_memory.get_user_facts = AsyncMock(return_value=[])

    import src.core.prompt_builder as pb
    original_path = Path

    # Patch only the SOP_DIR constant used in the implementation
    monkeypatch.setattr(pb, "_SOP_DIR", sop_dir)

    with patch("src.core.prompt_builder.load_prompt", return_value="base prompt"):
        result = await pb.build_system_prompt(
            system_prompt="soul",
            chat_id="test",
            user_message="hello",
            memory=mock_memory,
            vector_store=None,
            knowledge_manager=None,
            skill_manager=None,
        )

    assert "# 标准操作流程" not in result
```

Note: The implementation in Step 3 will use a module-level `_SOP_DIR = Path("prompts/sop")` constant, making it easy to monkeypatch in tests without breaking `Path` globally.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/core/test_sop_injection.py -x -q`
Expected: FAIL (SOP injection not yet implemented)

- [ ] **Step 3: Implement SOP injection in `prompt_builder.py`**

In `src/core/prompt_builder.py`:

1. Add `from pathlib import Path` to imports (top of file).
2. Add a module-level constant (after the `_RELATED_MEMORY_LIMIT` constant on line 24):
```python
_SOP_DIR = Path("prompts/sop")
```

3. Insert the SOP layer between Layer 6 (skill catalog, line 216) and Layer 7 (capabilities, line 218):

```python
    # Layer 6.5: 标准操作流程（SOP）
    if _SOP_DIR.exists():
        _sop_texts: list[str] = []
        for _sop_file in sorted(_SOP_DIR.glob("*.md")):
            try:
                _sop_content = _sop_file.read_text(encoding="utf-8").strip()
                if _sop_content:
                    _sop_texts.append(_sop_content)
            except Exception:
                pass
        if _sop_texts:
            sections.append("# 标准操作流程\n\n" + "\n\n---\n\n".join(_sop_texts))
```

Insert this block immediately after line 216 (end of skill catalog block), before line 218 (`# Layer 7`). Using a module-level `_SOP_DIR` constant makes it easy to monkeypatch in tests.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/core/test_sop_injection.py -x -q`
Expected: PASS

- [ ] **Step 5: Run existing prompt_builder-related tests to check for regressions**

Run: `python -m pytest tests/ -x -q -k "prompt" 2>&1 | head -20`
Expected: All existing tests still pass

- [ ] **Step 6: Commit**

```bash
git add src/core/prompt_builder.py tests/core/test_sop_injection.py
git commit -m "feat: inject SOP files into system prompt between skill catalog and capabilities layers"
```

---

### Task 4: Simulated Tool Call Detection in TaskRuntime

**Files:**
- Modify: `src/core/task_runtime.py:57-59,290-320`
- Create: `tests/core/test_simulated_tool_detection.py`

- [ ] **Step 1: Write tests for simulated tool call detection**

Create `tests/core/test_simulated_tool_detection.py`:

```python
"""模拟工具调用检测测试。"""

import pytest

from src.core.task_runtime import TaskRuntime
from unittest.mock import MagicMock


def _make_runtime():
    return TaskRuntime(router=MagicMock())


class TestDetectSimulatedToolCall:
    def test_detects_chinese_intent_pattern(self):
        rt = _make_runtime()
        text = "好的，我来用 web_search 帮你查一下"
        assert rt._detect_simulated_tool_call(text, ["web_search", "web_fetch"]) is True

    def test_detects_english_intent_pattern(self):
        rt = _make_runtime()
        text = "Let me use web_fetch to get the page"
        assert rt._detect_simulated_tool_call(text, ["web_search", "web_fetch"]) is True

    def test_detects_call_pattern(self):
        rt = _make_runtime()
        text = "我调用 execute_shell 来执行这个命令"
        assert rt._detect_simulated_tool_call(text, ["execute_shell"]) is True

    def test_detects_json_tool_structure(self):
        rt = _make_runtime()
        text = '我会执行这个：{"tool": "web_search", "query": "test"}'
        assert rt._detect_simulated_tool_call(text, ["web_search"]) is True

    def test_detects_function_json_structure(self):
        rt = _make_runtime()
        text = '{"function": "search", "args": {}}'
        assert rt._detect_simulated_tool_call(text, ["search"]) is True

    def test_ignores_normal_text(self):
        rt = _make_runtime()
        text = "我帮你查了一下，结果如下"
        assert rt._detect_simulated_tool_call(text, ["web_search"]) is False

    def test_ignores_empty_text(self):
        rt = _make_runtime()
        assert rt._detect_simulated_tool_call("", ["web_search"]) is False
        assert rt._detect_simulated_tool_call(None, ["web_search"]) is False

    def test_ignores_tool_name_in_non_intent_context(self):
        rt = _make_runtime()
        text = "web_search 返回了3个结果"
        assert rt._detect_simulated_tool_call(text, ["web_search"]) is False

    def test_no_tools_returns_false(self):
        rt = _make_runtime()
        text = "我来用 web_search 查一下"
        assert rt._detect_simulated_tool_call(text, []) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/core/test_simulated_tool_detection.py -x -q`
Expected: FAIL (method not defined)

- [ ] **Step 3: Implement `_detect_simulated_tool_call` method**

Add this method to the `TaskRuntime` class in `src/core/task_runtime.py` (after `_compress_browser_history`, around line 1293):

```python
    def _detect_simulated_tool_call(self, text: str | None, available_tools: list[str]) -> bool:
        """检测 LLM 是否在文本中描述了工具调用而没有真正调用。"""
        if not text or not available_tools:
            return False

        text_lower = text.lower()
        for tool_name in available_tools:
            if tool_name not in text_lower:
                continue
            for pattern in (
                f"用 {tool_name}", f"使用 {tool_name}", f"调用 {tool_name}",
                f"call {tool_name}", f"use {tool_name}",
            ):
                if pattern in text_lower:
                    return True

        if '"tool"' in text or '"function"' in text or '"name"' in text:
            import re
            if re.search(r'\{\s*"(tool|function|name)"\s*:', text):
                return True

        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/core/test_simulated_tool_detection.py -x -q`
Expected: PASS

- [ ] **Step 5: Commit detection method**

```bash
git add src/core/task_runtime.py tests/core/test_simulated_tool_detection.py
git commit -m "feat: add simulated tool call detection method to TaskRuntime"
```

- [ ] **Step 6: Wire detection into `_step_runner` in `complete_chat`**

In `src/core/task_runtime.py`, in the `complete_chat` method:

1. Add a counter alongside `loop_detection_state` (around line 288):
```python
        simulated_tool_retries = 0
```

2. Modify the `if not turn.tool_calls:` branch in `_step_runner` (lines 301-320). Replace the current block:

```python
            if not turn.tool_calls:
                # ── 模拟工具调用检测 ──
                nonlocal simulated_tool_retries
                model_text = (turn.text or "").strip()
                if simulated_tool_retries < 1 and model_text:
                    available_tool_names = [t["function"]["name"] for t in tools]
                    if self._detect_simulated_tool_call(model_text, available_tool_names):
                        simulated_tool_retries += 1
                        logger.info("[runtime] 检测到模拟工具调用，注入提醒（retry %d）", simulated_tool_retries)
                        messages.append({
                            "role": "user",
                            "content": (
                                "[系统提醒] 你刚才在文字中描述了工具调用，但没有真正调用。"
                                "请直接使用工具，不要用文字描述。"
                            ),
                        })
                        return TaskLoopStep()  # continue loop

                await _emit_status("stage:finalizing")
                final_text = model_text
                if final_text and on_interim_text is not None:
                    try:
                        await on_interim_text(final_text)
                        interim_parts.append(final_text)
                    except Exception:
                        pass
                final_reply = await self._finalize_without_tool_calls(
                    chat_id=chat_id,
                    task_id=task_id,
                    state=state,
                    model_text=turn.text,
                    last_payload=last_payload,
                    event_bus=event_bus,
                    on_consent_required=on_consent_required,
                )
                return TaskLoopStep(completed=True, payload=last_payload)
```

- [ ] **Step 7: Run all task_runtime tests**

Run: `python -m pytest tests/core/test_task_runtime.py tests/core/test_simulated_tool_detection.py -x -q`
Expected: All pass

- [ ] **Step 8: Commit wiring**

```bash
git add src/core/task_runtime.py
git commit -m "feat: wire simulated tool call detection into complete_chat step runner"
```

---

### Task 5: Part A Integration Test

**Files:**
- None modified (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `python -m pytest tests/ -x -q 2>&1 | tail -10`
Expected: All tests pass (or pre-existing failures only)

- [ ] **Step 2: Verify SOP files are loaded in prompt**

Run a quick script to verify prompt assembly includes SOP content:

```bash
python3 -c "
from pathlib import Path
sop_dir = Path('prompts/sop')
files = sorted(sop_dir.glob('*.md'))
print(f'SOP files found: {len(files)}')
for f in files:
    content = f.read_text(encoding='utf-8').strip()
    print(f'  {f.name}: {len(content)} chars, starts with: {content[:40]}...')
"
```

Expected: 5 files, all non-empty, correct content

---

## Part B: Consciousness Engine

### Task 6: Add Configuration Settings

**Files:**
- Modify: `config/settings.py`

- [ ] **Step 1: Add consciousness settings to `config/settings.py`**

Add near the existing `HEARTBEAT_*` settings:

```python
# ── 意识循环 ──
CONSCIOUSNESS_ENABLED = _bool("CONSCIOUSNESS_ENABLED", True)
CONSCIOUSNESS_DEFAULT_INTERVAL = int(os.getenv("CONSCIOUSNESS_DEFAULT_INTERVAL", "600"))
CONSCIOUSNESS_MIN_INTERVAL = int(os.getenv("CONSCIOUSNESS_MIN_INTERVAL", "120"))
CONSCIOUSNESS_MAX_INTERVAL = int(os.getenv("CONSCIOUSNESS_MAX_INTERVAL", "1800"))
CONSCIOUSNESS_AFTER_CHAT_INTERVAL = int(os.getenv("CONSCIOUSNESS_AFTER_CHAT_INTERVAL", "120"))
CONSCIOUSNESS_CONVERSATION_END_DELAY = int(os.getenv("CONSCIOUSNESS_CONVERSATION_END_DELAY", "300"))
```

- [ ] **Step 2: Commit**

```bash
git add config/settings.py
git commit -m "feat: add CONSCIOUSNESS_* settings for consciousness engine"
```

---

### Task 7: Create ConsciousnessEngine

**Files:**
- Create: `src/core/consciousness.py`
- Create: `tests/core/test_consciousness.py`

- [ ] **Step 1: Write tests for ConsciousnessEngine**

Create `tests/core/test_consciousness.py`:

```python
"""ConsciousnessEngine 单元测试。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.consciousness import ConsciousnessEngine


def _make_engine(brain=None, send_fn=None, reminder_scheduler=None):
    brain = brain or MagicMock()
    send_fn = send_fn or AsyncMock()
    return ConsciousnessEngine(
        brain=brain,
        send_fn=send_fn,
        reminder_scheduler=reminder_scheduler,
    )


class TestParseNextInterval:
    def test_parses_minutes(self):
        engine = _make_engine()
        assert engine._parse_next_interval("无事 [NEXT: 10m]") == 600

    def test_parses_hours(self):
        engine = _make_engine()
        assert engine._parse_next_interval("做完了 [NEXT: 2h]") == 7200

    def test_default_on_missing(self):
        engine = _make_engine()
        from config.settings import CONSCIOUSNESS_DEFAULT_INTERVAL
        assert engine._parse_next_interval("无事") == CONSCIOUSNESS_DEFAULT_INTERVAL

    def test_default_on_empty(self):
        engine = _make_engine()
        from config.settings import CONSCIOUSNESS_DEFAULT_INTERVAL
        assert engine._parse_next_interval("") == CONSCIOUSNESS_DEFAULT_INTERVAL

    def test_case_insensitive(self):
        engine = _make_engine()
        assert engine._parse_next_interval("[NEXT: 5M]") == 300


class TestConversationState:
    def test_on_conversation_start_clears_event(self):
        engine = _make_engine()
        assert engine._conversation_event.is_set()
        engine.on_conversation_start()
        assert not engine._conversation_event.is_set()
        assert engine._in_conversation is True

    def test_on_conversation_end_sets_event(self):
        engine = _make_engine()
        engine.on_conversation_start()
        engine.on_conversation_end()
        assert engine._conversation_event.is_set()
        assert engine._in_conversation is False

    def test_on_conversation_start_cancels_thinking(self):
        engine = _make_engine()
        mock_task = MagicMock()
        mock_task.done.return_value = False
        engine._thinking_task = mock_task
        engine.on_conversation_start()
        mock_task.cancel.assert_called_once()


class TestConsciousnessPrompt:
    @pytest.mark.asyncio
    async def test_prompt_contains_timestamp(self):
        engine = _make_engine()
        prompt = await engine._build_consciousness_prompt()
        assert "[内部意识 tick" in prompt
        assert "你可以做任何你觉得应该做的事" in prompt

    @pytest.mark.asyncio
    async def test_prompt_contains_rules(self):
        engine = _make_engine()
        prompt = await engine._build_consciousness_prompt()
        assert "[NEXT:" in prompt
        assert "memory_note" in prompt


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_start_sets_running(self):
        engine = _make_engine()
        # Start then immediately stop to avoid infinite loop
        await engine.start()
        assert engine._running is True
        assert engine._task is not None
        await engine.stop()
        assert engine._running is False

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self):
        engine = _make_engine()
        await engine.start()
        await engine.stop()
        assert engine._task.cancelled() or engine._task.done()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/core/test_consciousness.py -x -q`
Expected: FAIL (module not found)

- [ ] **Step 3: Create `src/core/consciousness.py`**

```python
"""自主意识循环引擎 — 替代旧的 HeartbeatEngine。

核心思路：
- 定期向 Brain 注入一条内部消息，触发完整的 agent loop
- LLM 自己决定做什么（或什么都不做）
- 用户对话时暂停，对话结束后恢复
- 动态调整 tick 间隔
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from config.settings import (
    CONSCIOUSNESS_AFTER_CHAT_INTERVAL,
    CONSCIOUSNESS_DEFAULT_INTERVAL,
    CONSCIOUSNESS_MAX_INTERVAL,
    CONSCIOUSNESS_MIN_INTERVAL,
)

if TYPE_CHECKING:
    from src.core.brain import LapwingBrain
    from src.core.reminder_scheduler import ReminderScheduler

logger = logging.getLogger("lapwing.core.consciousness")


class ConsciousnessEngine:
    """自主意识循环。"""

    def __init__(
        self,
        brain: "LapwingBrain",
        send_fn: Callable[..., Awaitable[Any]],
        reminder_scheduler: "ReminderScheduler | None",
    ) -> None:
        self._brain = brain
        self._send_fn = send_fn
        self._reminder_scheduler = reminder_scheduler

        self._task: asyncio.Task | None = None
        self._running = False
        self._next_interval: int = CONSCIOUSNESS_DEFAULT_INTERVAL

        # 对话状态
        self._in_conversation = False
        self._last_conversation_end: float = 0
        self._conversation_event = asyncio.Event()
        self._conversation_event.set()  # 初始：不在对话中

        # 当前自由思考 task
        self._thinking_task: asyncio.Task | None = None

        # 工作记忆
        self._working_memory_path = Path("data/consciousness/working_memory.md")
        self._activity_log_path = Path("data/consciousness/activity_log.md")

        # 定时维护
        self._last_hourly_maintenance: float = 0
        self._daily_maintenance_done_today = False

    # ── 生命周期 ──

    async def start(self) -> None:
        self._running = True
        self._working_memory_path.parent.mkdir(parents=True, exist_ok=True)
        self._task = asyncio.create_task(self._loop(), name="consciousness-loop")

        if self._reminder_scheduler:
            await self._reminder_scheduler.start()

        logger.info("意识循环已启动，初始间隔 %ds", self._next_interval)

    async def stop(self) -> None:
        self._running = False
        if self._thinking_task and not self._thinking_task.done():
            self._thinking_task.cancel()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._reminder_scheduler:
            await self._reminder_scheduler.shutdown()
        logger.info("意识循环已停止")

    # ── 对话状态管理 ──

    def on_conversation_start(self) -> None:
        self._in_conversation = True
        self._conversation_event.clear()
        if self._thinking_task and not self._thinking_task.done():
            logger.info("用户发消息，中断自由思考")
            self._thinking_task.cancel()

    def on_conversation_end(self) -> None:
        self._in_conversation = False
        self._last_conversation_end = time.time()
        self._next_interval = CONSCIOUSNESS_AFTER_CHAT_INTERVAL
        self._conversation_event.set()

    # ── 主循环 ──

    async def _loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self._next_interval)

                if self._in_conversation:
                    logger.debug("正在对话中，等待结束...")
                    await self._conversation_event.wait()
                    continue

                # 维护任务（不走 LLM）
                await self._run_maintenance_if_due()

                # 自由思考
                self._thinking_task = asyncio.create_task(
                    self._think_freely(), name="free-thinking"
                )
                try:
                    await self._thinking_task
                except asyncio.CancelledError:
                    logger.info("自由思考被中断（用户发消息）")
                    await self._save_interrupted_state()

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("意识循环异常")
                self._next_interval = CONSCIOUSNESS_DEFAULT_INTERVAL
                await asyncio.sleep(30)

    # ── 自由思考 ──

    async def _think_freely(self) -> None:
        internal_message = await self._build_consciousness_prompt()
        chat_id = "__consciousness__"

        response = await self._brain.think(
            chat_id=chat_id,
            user_message=internal_message,
        )

        next_interval = self._parse_next_interval(response)
        self._next_interval = max(
            CONSCIOUSNESS_MIN_INTERVAL,
            min(CONSCIOUSNESS_MAX_INTERVAL, next_interval),
        )

        await self._log_activity(response)
        logger.info("自由思考完成，下次间隔 %ds", self._next_interval)

    async def _build_consciousness_prompt(self) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M %A")

        parts = [
            f"[内部意识 tick — {now}]",
            "",
            "这是你的自由时间。你可以做任何你觉得应该做的事，或者什么都不做。",
            "没有人在等你回复。你不需要跟任何人说话，除非你自己想。",
            "",
        ]

        # 工作记忆
        working_memory = self._read_working_memory()
        if working_memory:
            parts.append("## 你上次在做的事\n")
            parts.append(working_memory)
            parts.append("")

        # 思考维度引导
        parts.append("## 你可以想想这些方面\n")
        parts.append("- 刚才跟他聊的有没有什么你说错的或者可以做得更好的")
        parts.append("- 你手头有没有没做完的事")
        parts.append("- 他最近有没有什么需要你提前准备的")
        parts.append("- 你有没有什么想看的想学的")
        parts.append("- 你的身体（系统）状态正常吗")
        parts.append("- 有没有什么能力上的不足是你可以尝试解决的")
        parts.append("- 你想不想主动找他说点什么")
        parts.append("")

        # 指令
        parts.append("## 规则\n")
        parts.append("- 你可以使用任何工具来做你想做的事")
        parts.append("- 如果你做了什么，用 memory_note 记录下来")
        parts.append("- 如果你想找他说话，调用 send_proactive_message 工具")
        parts.append("- 如果你想在工作记忆中记录进度，用 write_file 写到 data/consciousness/working_memory.md")
        parts.append("- 什么都不想做也完全可以，回复"无事"即可")
        parts.append("- 在回复的最后一行，写上你希望多久后再被叫醒，格式：[NEXT: 数字m] 或 [NEXT: 数字h]")
        parts.append("  例如 [NEXT: 10m] 表示 10 分钟后，[NEXT: 2h] 表示 2 小时后")
        parts.append("  如果你觉得现在该休息了，可以写 [NEXT: 6h] 之类的长间隔")

        return "\n".join(parts)

    # ── 工具方法 ──

    def _read_working_memory(self) -> str:
        if self._working_memory_path.exists():
            try:
                text = self._working_memory_path.read_text(encoding="utf-8").strip()
                return text[:2000] if len(text) > 2000 else text
            except Exception:
                return ""
        return ""

    def _parse_next_interval(self, response: str) -> int:
        if not response:
            return CONSCIOUSNESS_DEFAULT_INTERVAL
        match = re.search(r'\[NEXT:\s*(\d+)\s*(m|h)\]', response, re.IGNORECASE)
        if match:
            value = int(match.group(1))
            unit = match.group(2).lower()
            return value * 3600 if unit == 'h' else value * 60
        return CONSCIOUSNESS_DEFAULT_INTERVAL

    async def _handle_consciousness_output(self, text: str, **kwargs) -> None:
        logger.debug("[consciousness] 输出: %s", text[:200] if text else "(空)")

    async def _log_activity(self, response: str) -> None:
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            summary = (response[:500] if response else "（无输出）")
            entry = f"\n---\n### {now}\n\n{summary}\n\n下次间隔: {self._next_interval}s\n"
            self._activity_log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._activity_log_path, "a", encoding="utf-8") as f:
                f.write(entry)
            if self._activity_log_path.stat().st_size > 50000:
                content = self._activity_log_path.read_text(encoding="utf-8")
                self._activity_log_path.write_text(content[-30000:], encoding="utf-8")
        except Exception:
            logger.debug("活动日志写入失败", exc_info=True)

    async def _save_interrupted_state(self) -> None:
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            note = f"\n\n[{now}] 被中断——用户发消息了，待会儿继续。\n"
            with open(self._working_memory_path, "a", encoding="utf-8") as f:
                f.write(note)
        except Exception:
            pass

    # ── 定时维护 ──

    async def _run_maintenance_if_due(self) -> None:
        now = time.time()

        if now - self._last_hourly_maintenance > 3600:
            self._last_hourly_maintenance = now
            await self._run_hourly_maintenance()

        hour = datetime.now().hour
        if hour == 3 and not self._daily_maintenance_done_today:
            self._daily_maintenance_done_today = True
            await self._run_daily_maintenance()
        elif hour != 3:
            self._daily_maintenance_done_today = False

    async def _run_hourly_maintenance(self) -> None:
        """每小时维护：会话清理、任务通知。"""
        try:
            from src.heartbeat.actions.session_reaper import SessionReaperAction
            from src.heartbeat.actions.task_notification import TaskNotificationAction
            for ActionCls in (SessionReaperAction, TaskNotificationAction):
                action = ActionCls()
                try:
                    await action.execute(
                        self._build_maintenance_context("fast"),
                        self._brain,
                        self._send_fn,
                    )
                except Exception as exc:
                    logger.warning("每小时维护 %s 失败: %s", action.name, exc)
        except Exception:
            logger.debug("每小时维护加载失败", exc_info=True)

    async def _run_daily_maintenance(self) -> None:
        """每日 3AM 维护：记忆整理、索引优化、压缩检查。"""
        try:
            from src.heartbeat.actions.consolidation import MemoryConsolidationAction
            from src.heartbeat.actions.memory_maintenance import MemoryMaintenanceAction
            from src.heartbeat.actions.compaction_check import CompactionCheckAction
            for ActionCls in (MemoryConsolidationAction, MemoryMaintenanceAction, CompactionCheckAction):
                action = ActionCls()
                try:
                    await action.execute(
                        self._build_maintenance_context("slow"),
                        self._brain,
                        self._send_fn,
                    )
                except Exception as exc:
                    logger.warning("每日维护 %s 失败: %s", action.name, exc)
        except Exception:
            logger.debug("每日维护加载失败", exc_info=True)

    def _build_maintenance_context(self, beat_type: str):
        """构建一个最小 SenseContext 供维护 action 使用。"""
        from src.core.heartbeat import SenseContext
        from src.core.vitals import now_taipei
        now = now_taipei()
        return SenseContext(
            beat_type=beat_type,
            now=now,
            last_interaction=None,
            silence_hours=0,
            user_facts_summary="",
            recent_memory_summary="",
            chat_id="__maintenance__",
            now_taipei_hour=now.hour,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/core/test_consciousness.py -x -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/consciousness.py tests/core/test_consciousness.py
git commit -m "feat: create ConsciousnessEngine with autonomous thought loop, maintenance, and conversation awareness"
```

---

### Task 8: Add `send_proactive_message` Tool

**Files:**
- Modify: `src/tools/handlers.py` (append handler function)
- Modify: `src/tools/registry.py`
- Create: `tests/tools/test_proactive_message.py`

Note: `src/tools/handlers.py` is a single file (not a package directory). The handler goes there.

- [ ] **Step 1: Write test for the handler**

Create `tests/tools/test_proactive_message.py`:

```python
"""send_proactive_message 工具处理器测试。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.tools.handlers import send_proactive_message
from src.tools.types import ToolExecutionRequest


@pytest.mark.asyncio
async def test_sends_message_via_channel_manager():
    req = ToolExecutionRequest(name="send_proactive_message", arguments={"message": "hello"})
    channel_manager = AsyncMock()
    ctx = MagicMock()
    ctx.services = {"channel_manager": channel_manager}

    result = await send_proactive_message(req, ctx)

    assert result.success is True
    channel_manager.send_to_owner.assert_awaited_once_with("hello")


@pytest.mark.asyncio
async def test_fails_on_empty_message():
    req = ToolExecutionRequest(name="send_proactive_message", arguments={"message": ""})
    ctx = MagicMock()
    ctx.services = {}

    result = await send_proactive_message(req, ctx)

    assert result.success is False
    assert "空" in result.reason


@pytest.mark.asyncio
async def test_fails_without_channel_manager():
    req = ToolExecutionRequest(name="send_proactive_message", arguments={"message": "hi"})
    ctx = MagicMock()
    ctx.services = {}

    result = await send_proactive_message(req, ctx)

    assert result.success is False
    assert "通道" in result.reason
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/tools/test_proactive_message.py -x -q`
Expected: FAIL (function not found)

- [ ] **Step 3: Add handler to `src/tools/handlers.py`**

Append to the end of `src/tools/handlers.py`:

```python
async def send_proactive_message(
    req: ToolExecutionRequest,
    ctx: ToolExecutionContext,
) -> ToolExecutionResult:
    """主动给用户发一条消息。"""
    message = req.arguments.get("message", "")
    if not message:
        return ToolExecutionResult(success=False, payload={}, reason="消息内容为空")

    channel_manager = ctx.services.get("channel_manager")
    if channel_manager is None:
        return ToolExecutionResult(success=False, payload={}, reason="没有可用的通道")

    try:
        await channel_manager.send_to_owner(message)
        return ToolExecutionResult(success=True, payload={"sent": True}, reason="已发送")
    except Exception as exc:
        return ToolExecutionResult(success=False, payload={}, reason=f"发送失败: {exc}")
```

Note: Uses `channel_manager.send_to_owner(message)` — the actual method on `ChannelManager` (not `.send_proactive()` which doesn't exist).

- [ ] **Step 4: Register tool in `src/tools/registry.py`**

In `build_default_tool_registry()`, add after the existing `send_image` registration:

```python
    from src.tools.handlers import send_proactive_message
    registry.register(ToolSpec(
        name="send_proactive_message",
        description="主动给他发一条消息。只在你真的有话想说的时候用，不要没事就打扰他。",
        json_schema={
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "你想说的话",
                }
            },
            "required": ["message"],
        },
        executor=send_proactive_message,
        capability="general",
        risk_level="low",
    ))
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/tools/test_proactive_message.py -x -q`
Expected: PASS

- [ ] **Step 6: Add `send_proactive_message` to `chat_tools()` in `task_runtime.py`**

In `src/core/task_runtime.py`, in the `chat_tools` method (line 182), add `"send_proactive_message"` to the always-available set:

```python
        tool_names: set[str] = {"memory_note", "get_weather", "send_image", "send_proactive_message"}  # always available
```

This ensures the consciousness engine (which goes through `Brain.think()` → `_complete_chat()` → `chat_tools()`) can access the tool.

- [ ] **Step 7: Run all tests**

Run: `python -m pytest tests/tools/test_proactive_message.py tests/core/test_task_runtime.py -x -q`
Expected: PASS (note: `test_chat_tools_from_registry` may need the expected tool set updated to include `send_proactive_message`)

- [ ] **Step 8: Commit**

```bash
git add src/tools/handlers.py src/tools/registry.py src/core/task_runtime.py tests/tools/test_proactive_message.py
git commit -m "feat: add send_proactive_message tool for consciousness-driven proactive messaging"
```

---

### Task 9: Integrate ConsciousnessEngine into Brain

**Files:**
- Modify: `src/core/brain.py:110-144,654-679`
- Create: `tests/core/test_consciousness_brain_integration.py`

- [ ] **Step 1: Write integration test**

Create `tests/core/test_consciousness_brain_integration.py`:

```python
"""Brain ↔ ConsciousnessEngine 集成测试。"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def reset_module_cache():
    """Clear cached brain module to avoid stale state between tests."""
    for mod in list(sys.modules.keys()):
        if "brain" in mod or "fact_extractor" in mod:
            del sys.modules[mod]
    yield
    for mod in list(sys.modules.keys()):
        if "brain" in mod or "fact_extractor" in mod:
            del sys.modules[mod]


def _make_brain():
    """Create a Brain instance with all external dependencies mocked."""
    with patch("src.core.brain.load_prompt", return_value="prompt"), \
         patch("src.core.brain.LLMRouter"), \
         patch("src.core.brain.ConversationMemory") as MockMemory:
        # Configure the mock ConversationMemory before it's used
        mock_mem_instance = MockMemory.return_value
        mock_mem_instance.append = AsyncMock()
        mock_mem_instance.append_to_session = AsyncMock()
        mock_mem_instance.remove_last = AsyncMock()

        from src.core.brain import LapwingBrain
        brain = LapwingBrain(db_path=Path("test.db"))

    brain.fact_extractor = MagicMock()
    brain.fact_extractor.notify = MagicMock()
    brain.quality_checker = None
    return brain


class TestBrainConsciousnessAttr:
    def test_consciousness_engine_attr_defaults_none(self):
        brain = _make_brain()
        assert brain.consciousness_engine is None

    def test_consciousness_engine_can_be_set(self):
        brain = _make_brain()
        mock_engine = MagicMock()
        brain.consciousness_engine = mock_engine
        assert brain.consciousness_engine is mock_engine


class TestConversationStateNotification:
    @pytest.mark.asyncio
    async def test_think_conversational_notifies_start(self):
        brain = _make_brain()
        mock_engine = MagicMock()
        brain.consciousness_engine = mock_engine

        from src.core.brain import _ThinkCtx
        ctx = _ThinkCtx(
            messages=[], effective_user_message="hi",
            approved_directory=None, early_reply="hello",
            session_id=None,
        )
        brain._prepare_think = AsyncMock(return_value=ctx)

        await brain.think_conversational("test", "hi", AsyncMock())

        mock_engine.on_conversation_start.assert_called_once()

    @pytest.mark.asyncio
    async def test_think_conversational_schedules_end(self):
        brain = _make_brain()
        mock_engine = MagicMock()
        brain.consciousness_engine = mock_engine

        from src.core.brain import _ThinkCtx
        ctx = _ThinkCtx(
            messages=[], effective_user_message="hi",
            approved_directory=None, early_reply="hello",
            session_id=None,
        )
        brain._prepare_think = AsyncMock(return_value=ctx)

        await brain.think_conversational("test", "hi", AsyncMock())

        # Verify _schedule_conversation_end was called (creates a task)
        assert brain._conversation_end_task is not None

    @pytest.mark.asyncio
    async def test_no_crash_without_consciousness_engine(self):
        brain = _make_brain()
        assert brain.consciousness_engine is None

        from src.core.brain import _ThinkCtx
        ctx = _ThinkCtx(
            messages=[], effective_user_message="hi",
            approved_directory=None, early_reply="hello",
            session_id=None,
        )
        brain._prepare_think = AsyncMock(return_value=ctx)

        result = await brain.think_conversational("test", "hi", AsyncMock())
        assert result == "hello"  # should not crash
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/core/test_consciousness_brain_integration.py -x -q`
Expected: FAIL

- [ ] **Step 3: Add consciousness attributes to Brain.__init__**

In `src/core/brain.py`, in `__init__` (around line 143), add:

```python
        self.consciousness_engine = None  # Set externally (ConsciousnessEngine | None)
        self._conversation_end_task: asyncio.Task | None = None
```

- [ ] **Step 4: Add conversation state hooks to `think_conversational`**

In `src/core/brain.py`, modify `think_conversational` (line 654+):

At the very start of the method (after the docstring, before `ctx = await self._prepare_think`), add:

```python
        # 通知意识引擎：对话开始
        if self.consciousness_engine is not None:
            self.consciousness_engine.on_conversation_start()
```

There are two exit paths to handle:

**Path A — early return (line 677-678):** The early return is NOT inside the try block, so `finally` won't catch it. Add an explicit call before the early return:

```python
        if ctx.early_reply is not None:
            self._schedule_conversation_end()
            return ctx.early_reply
```

**Path B — try/except block (line 738-785):** Add a `finally` clause to the existing try/except:

```python
        try:
            # ... existing try block unchanged ...
        except Exception as e:
            # ... existing except block unchanged ...
        finally:
            self._schedule_conversation_end()
```

This ensures `_schedule_conversation_end()` is called on ALL exit paths.

- [ ] **Step 5: Add `_schedule_conversation_end` method**

Add this method to `LapwingBrain` class (after `_inject_voice_reminder`):

```python
    def _schedule_conversation_end(self) -> None:
        """延迟判定对话结束。用户最后一条消息后 N 秒无新消息算结束。"""
        if self.consciousness_engine is None:
            return
        if self._conversation_end_task is not None:
            self._conversation_end_task.cancel()

        from config.settings import CONSCIOUSNESS_CONVERSATION_END_DELAY

        async def _delayed_end():
            await asyncio.sleep(CONSCIOUSNESS_CONVERSATION_END_DELAY)
            if self.consciousness_engine is not None:
                self.consciousness_engine.on_conversation_end()

        self._conversation_end_task = asyncio.create_task(_delayed_end())
```

- [ ] **Step 6: Run tests**

Run: `python -m pytest tests/core/test_consciousness_brain_integration.py -x -q`
Expected: PASS

- [ ] **Step 7: Run existing brain tests for regressions**

Run: `python -m pytest tests/core/test_brain_split.py -x -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/core/brain.py tests/core/test_consciousness_brain_integration.py
git commit -m "feat: integrate ConsciousnessEngine into Brain with conversation state hooks"
```

---

### Task 10: Replace HeartbeatEngine with ConsciousnessEngine in Container

**Files:**
- Modify: `src/app/container.py`

- [ ] **Step 1: Modify container imports and constructor**

In `src/app/container.py`:

1. Add import (near line 27):
```python
from src.core.consciousness import ConsciousnessEngine
```

2. Keep the `HeartbeatEngine` import for now (it's still used by maintenance actions that reference `SenseContext` from `heartbeat.py`).

3. In `__init__` (around line 84), add:
```python
        self.consciousness: ConsciousnessEngine | None = None
```

- [ ] **Step 2: Modify `start()` to use ConsciousnessEngine**

Replace the heartbeat/reminder block in `start()` (lines 119-128):

```python
        if send_fn is not None:
            from config.settings import CONSCIOUSNESS_ENABLED, HEARTBEAT_ENABLED

            self.reminder_scheduler = ReminderScheduler(
                memory=self.brain.memory,
                send_fn=send_fn,
                event_bus=self.event_bus,
            )
            self.brain.reminder_scheduler = self.reminder_scheduler

            if CONSCIOUSNESS_ENABLED:
                self.consciousness = ConsciousnessEngine(
                    brain=self.brain,
                    send_fn=send_fn,
                    reminder_scheduler=self.reminder_scheduler,
                )
                self.brain.consciousness_engine = self.consciousness
                await self.consciousness.start()
            elif HEARTBEAT_ENABLED:
                # 兼容旧模式
                self.heartbeat = self._build_heartbeat(send_fn)
                self.heartbeat.start()
                await self.reminder_scheduler.start()
```

- [ ] **Step 3: Modify `shutdown()` for ConsciousnessEngine**

In `shutdown()` (line 141+), add ConsciousnessEngine shutdown as the VERY FIRST operation (before `reminder_scheduler` and `heartbeat` shutdowns). ConsciousnessEngine owns the `reminder_scheduler` reference and calls `reminder_scheduler.shutdown()` internally, so we must stop it first, then null out `reminder_scheduler` to avoid double-shutdown:

```python
        # Consciousness engine shutdown (must come first — it owns reminder_scheduler)
        if self.consciousness is not None:
            await self.consciousness.stop()
            self.consciousness = None
            self.reminder_scheduler = None  # already shut down by consciousness.stop()
```

Keep the existing `if self.reminder_scheduler is not None:` block for the fallback (non-consciousness) path — it will only execute when consciousness is None.

- [ ] **Step 4: Update API state injection**

Change line 136 from:
```python
            self.api_server._app.state.heartbeat = self.heartbeat
```
to:
```python
            self.api_server._app.state.heartbeat = self.heartbeat
            self.api_server._app.state.consciousness = self.consciousness
```

- [ ] **Step 5: Run existing container-related tests**

Run: `python -m pytest tests/ -x -q -k "container" 2>&1 | head -20`
Expected: All pass (or no container tests exist — that's fine)

- [ ] **Step 6: Commit**

```bash
git add src/app/container.py
git commit -m "feat: replace HeartbeatEngine with ConsciousnessEngine in AppContainer"
```

---

### Task 11: Create Data Directories + Final Integration

**Files:**
- None created (directories only, via code)

- [ ] **Step 1: Verify data directory is created by the engine**

The `ConsciousnessEngine.start()` already calls `self._working_memory_path.parent.mkdir(parents=True, exist_ok=True)`, so `data/consciousness/` is auto-created.

Run: `python3 -c "from pathlib import Path; Path('data/consciousness').mkdir(parents=True, exist_ok=True); print('ok')" && ls data/consciousness/`
Expected: Empty directory exists

- [ ] **Step 2: Run the full test suite**

Run: `python -m pytest tests/ -x -q 2>&1 | tail -15`
Expected: All tests pass

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "feat: complete agent loop hardening + consciousness engine integration"
```

---

## Verification Checklist (Post-Implementation)

### Part A Verification
- [ ] SOP files load into system prompt (check prompt output for "# 标准操作流程")
- [ ] SOP layer appears between skill catalog and capabilities in prompt
- [ ] Simulated tool call detection catches "我来用 web_search" patterns
- [ ] Detection triggers at most 1 retry before allowing normal flow
- [ ] All existing tests still pass

### Part B Verification
- [ ] ConsciousnessEngine starts and logs "意识循环已启动"
- [ ] Engine pauses on `on_conversation_start()` and resumes on `on_conversation_end()`
- [ ] `[NEXT: Nm]` parsing works correctly
- [ ] Maintenance tasks run (hourly + daily 3AM)
- [ ] `send_proactive_message` tool is registered and functional
- [ ] Working memory file is created and read across ticks
- [ ] Activity log is written and auto-truncated at 50KB
- [ ] Brain notifies consciousness engine on conversation start/end
- [ ] Container properly starts/stops ConsciousnessEngine
- [ ] Old HeartbeatEngine still works when `CONSCIOUSNESS_ENABLED=false`

---

## Known Issues for Future Iteration

- **`__consciousness__` history accumulation:** `Brain.think()` stores messages under `chat_id="__consciousness__"` in the database. This will grow unbounded. Future fix: add `chat_id == "__consciousness__"` check in `_prepare_think` to skip memory persistence, or periodically clear this chat_id's history.
- **Voice.md injection for consciousness:** The consciousness tick goes through `_prepare_think` which injects voice.md. Acceptable for now; could be optimized later to skip voice injection for internal ticks.
- **Tool set for consciousness:** `Brain.think()` uses `chat_tools()` which has a fixed set of tools by `tool_names`. The `send_proactive_message` tool (capability="general") is NOT in the `chat_tools()` whitelist. It needs to be added to `chat_tools()` `tool_names` set, or `_think_freely` needs to use a different tool set. Address during implementation of Task 8.
