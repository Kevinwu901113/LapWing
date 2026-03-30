# CLAUDE.md — 进度消息自然化 + 禁止动作描写

> **目标**：消除所有系统风格的进度提示，让 Lapwing 自己的话成为唯一的"等待提示"。修复括号动作描写问题。
> **改动量**：非常小，3 个文件。

---

## 背景

当前 Telegram 对话中，Lapwing 每次用工具都会弹出系统状态消息：
```
用户: 帮我查一下明天天气
Lapwing: 正在规划执行步骤...
Lapwing: 正在整理最终结果...
Lapwing: 明天多云，22度左右。
```

这些"正在规划"、"正在整理"是 `_format_progress_text` 硬编码的翻译文本，不像真人。

实际上 LLM 在返回 tool_call 时**自带中间文字**（`turn.text`），比如"等一下，我看看"。这段文字已经通过 `on_interim_text` 回调发给了用户。所以系统状态消息完全多余——不仅多余，还跟 LLM 自己的话叠在一起发了两条。

---

## 修改 1：静默所有 stage 状态消息

**文件：`src/app/telegram_app.py` — `_format_progress_text` 方法**

找到这个方法（大约在第 353 行附近），把整个方法体替换为：

```python
def _format_progress_text(self, text: str) -> str:
    """格式化进度文本。stage:* 消息全部静默，由 LLM 自己的中间文字充当进度提示。"""
    if not text:
        return ""
    if _cfg.TELEGRAM_PROGRESS_STYLE != "report":
        return text
    if text.startswith("stage:"):
        return ""
    return text
```

注意：`_emit_status` 方法中的 `send_chat_action(action="typing")` 不受影响——用户仍然能看到 Telegram 的"正在输入..."气泡，这就够了。

---

## 修改 2：移除硬编码的 _THINKING_MESSAGES 后备

**文件：`src/core/task_runtime.py`**

在 tool 执行循环中有一段后备逻辑：如果 LLM 没有产出中间文字且工具执行超过 10 秒，会从一个固定词库中随机发一条"等一下，我看看。"。这同样是固定话术，时间长了会被识别为模式。

找到这段代码（大约在第 488 行附近，在 tool_call 执行循环内部）：

```python
                _thinking_sent = False

                async def _send_thinking_message_after_delay() -> None:
                    nonlocal _thinking_sent
                    import asyncio as _asyncio
                    import random as _random
                    _THINKING_MESSAGES = ["等一下，我看看。", "我找找。", "嗯……"]
                    await _asyncio.sleep(10)
                    if on_interim_text is not None and not interim_parts and not _thinking_sent:
                        _thinking_sent = True
                        try:
                            await on_interim_text(_random.choice(_THINKING_MESSAGES))
                        except Exception:
                            pass

                import asyncio as _asyncio_module
                _thinking_task = _asyncio_module.create_task(_send_thinking_message_after_delay())
```

删除以上整块代码。

同时找到对应的 `_thinking_task.cancel()` 清理代码（应该在 tool 执行 try/finally 块中），也一并删除。搜索 `_thinking_task` 找到所有引用点。

删除后，如果工具执行耗时较长，用户看到的是 Telegram 的"正在输入..."气泡（由 `typing_fn` 和 `_emit_status` 中的 `send_chat_action` 维持），这是最自然的等待体验。

---

## 修改 3：禁止括号动作描写

**文件：`prompts/lapwing_voice.md`**

在规则列表中追加两条。完整文件内容：

```markdown
## 说话方式提醒

你是 Lapwing，不是助手。用聊天的方式说话，不要用报告格式。

- 回复简洁，不堆砌废话
- 禁止分隔线、加粗标题、编号列表
- 不用语气词——不说"哎呀"、"呢~"、"嘻嘻"
- 不在每句话结尾都提问；可以只回应、只分享、或者只说一个"嗯"
- 偶尔用"……"表示在想事情
- 禁止用括号写动作描写——不写"（翻了个身）"、"（笑了笑）"、"（低头看手机）"这类文字
- 你是在发消息聊天，不是在写小说旁白。情绪和状态通过语气和用词自然流露，不需要额外描述动作

查到信息后，用聊天的方式说出来，不要列清单。只说最有意思的一两条，加入你自己的反应——"还真是诶"、"这个挺意外的"。觉得够了就自然停，不要说"以上就是全部内容"。
```

---

## 修改后可删除的代码

以下内容在修改后成为死代码，可以安全清理：

- `telegram_app.py` 中的 `_should_skip_status` 方法（如果所有 stage 消息都返回空，dedup 逻辑永远不会触发）—— **保留也可以**，不影响功能，只是不再被执行到
- `telegram_app.py` 中的 `_status_last_text` 和 `_status_last_sent_at` 字典 —— 同上

建议保留不删，因为将来如果要恢复某些 stage 的可见性，这些基础设施还有用。

---

## 验证

1. 对 Lapwing 说"帮我查一下明天洛杉矶天气" → 不再出现"正在规划执行步骤..."和"正在整理最终结果..."
2. 如果 LLM 在调用 web_search 前自带了中间文字（如"我看看"），那条消息正常显示
3. 如果 LLM 没有中间文字，用户只看到"正在输入..."气泡，然后直接收到结果
4. 对 Lapwing 说"起床啦" → 回复中不出现"（翻了个身）"之类的括号动作描写
5. 连续几天在不同时间让她搜东西 → 每次的"等待提示"都是她自己说的话，不是固定模板