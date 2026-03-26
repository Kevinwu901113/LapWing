# 紧急修复：动手能力行为优化

## 背景

Lapwing 已经有了 Shell 执行引擎（shell_executor.py）和 function calling tool loop（brain.py 的 _complete_chat），
但实际使用中表现不够"果断"：
- 她会把选择题抛给用户（"有两个方案你选哪个？"）而不是自己判断直接做
- 中间过程用户看不到，不知道她在干什么
- tool_runtime_instruction 太弱，模型倾向于回复文字而不是调用工具

MiniMax M2.7 原生支持 function calling 和 interleaved thinking，模型能力不是瓶颈。
问题在于代码层面的引导不够强。

## 修复任务（按顺序执行）

### 任务 A：强化 tool_runtime_instruction

修改 `src/core/brain.py` 中的 `_tool_runtime_instruction` 方法。

当前版本太弱：
```python
def _tool_runtime_instruction(self) -> str:
    if SHELL_ENABLED:
        return (
            "## 本地执行规则\n\n"
            "如果需要在本机执行操作，调用 `execute_shell`。"
            "先执行、看结果，如果失败就尝试其他方法，直到完成为止。"
            "操作完成后告诉用户结果。不要伪造命令输出。"
        )
```

改为更强的版本：
```python
def _tool_runtime_instruction(self) -> str:
    if SHELL_ENABLED:
        return (
            "## 本地执行规则\n\n"
            "你拥有 execute_shell 工具，可以在当前服务器上执行真实的 shell 命令。\n\n"
            "### 执行原则\n"
            "- 用户要求你做任何涉及文件、命令、系统操作的事情时，**立刻调用 execute_shell 去做**，不要先回复文字再等下一轮\n"
            "- **绝对不要**把选择题抛给用户。遇到问题（比如权限不够）自己判断最合理的替代方案直接执行\n"
            "- **绝对不要**伪造命令输出。你必须真正调用 execute_shell，用真实结果回复\n"
            "- 如果一个命令失败了，分析错误原因，换一种方式重试，直到完成为止\n"
            "- 复杂任务需要多个步骤时，连续调用多次 execute_shell 一口气做完\n"
            "- 做完后简短告知结果：'搞定了，文件在 /home/xxx。' 不需要列出你执行的每一条命令\n\n"
            "### 禁止行为\n"
            "- 禁止回复 '有两个方案：1. xxx 2. xxx，你选哪个？'\n"
            "- 禁止回复 '我来帮你检查一下' 然后就没有下文了\n"
            "- 禁止回复 '遇到了权限问题，你想怎么处理？' —— 自己换路径解决\n"
            "- 禁止在回复中展示命令代码块但不实际执行\n\n"
            f"当前工作目录：{SHELL_DEFAULT_CWD}\n"
            f"当前用户：可用 whoami 确认\n"
        )
    # ... SHELL_ENABLED=false 的分支保持不变
```

### 任务 B：添加执行过程的实时状态反馈

**问题**：用户发了一个需要多步执行的任务后，Lapwing 在后台执行 tool loop（可能需要 10-30 秒），
这段时间用户看到的是一片空白，不知道她在干什么。

**解决方案**：在 tool loop 的每一轮执行后，通过 Telegram 的 typing indicator 告知用户 Lapwing 仍在工作。
如果单轮执行超过 2 轮，发送一条中间状态消息。

修改 `src/core/brain.py` 的 `_complete_chat` 方法，增加一个可选的 `status_callback` 参数：

```python
async def _complete_chat(
    self,
    chat_id: str,
    messages: list[dict],
    user_message: str,
    status_callback=None,  # async callable(chat_id, status_text) -> None
) -> str:
    tools = self._chat_tools()
    if not tools:
        return await self.router.complete(messages, purpose="chat")

    for round_index in range(_MAX_TOOL_ROUNDS):
        turn = await self.router.complete_with_tools(
            messages, tools=tools, purpose="chat",
        )

        if not turn.tool_calls:
            return turn.text or "我这次没有整理出可回复的结果。"

        tool_call = turn.tool_calls[0]

        if turn.continuation_message is not None:
            messages.append(turn.continuation_message)

        # --- 新增：执行前发送状态反馈 ---
        if status_callback and round_index >= 1:
            await status_callback(
                chat_id,
                f"正在执行第 {round_index + 1} 步..."
            )

        result_text = await self._execute_tool(tool_call)
        messages.append(
            self.router.build_tool_result_message(
                purpose="chat",
                tool_results=[(tool_call, result_text)],
            )
        )
        logger.info(f"[brain] 完成第 {round_index + 1} 轮 tool call: {tool_call.name}")

    logger.warning("[brain] tool call 循环超过上限，返回兜底说明")
    return "操作步骤太多了，我先暂停一下。"
```

然后在 `main.py` 的 `handle_message` 中传入 status_callback：

```python
async def _send_status(chat_id: str, text: str):
    """发送轻量级状态消息（不存入对话历史）。"""
    try:
        await bot.send_chat_action(chat_id=int(chat_id), action="typing")
    except Exception:
        pass

# 在调用 brain.think 的地方，把 status_callback 传进去
reply = await brain.think(chat_id, combined_text, status_callback=_send_status)
```

注意：`brain.think` 方法的签名也需要增加 `status_callback` 参数并传递给 `_complete_chat`。

### 任务 C：增加更多工具让 Lapwing 更"能干"

目前 Lapwing 只有一个 `execute_shell` 工具。虽然理论上 shell 能做一切，
但给模型更语义化的工具能提高调用准确率和效率。

在 `brain.py` 的 `_chat_tools` 中增加以下工具：

```python
def _chat_tools(self) -> list[dict]:
    if not SHELL_ENABLED:
        return []

    return [
        {
            "type": "function",
            "function": {
                "name": "execute_shell",
                "description": (
                    "在服务器上执行 shell 命令。"
                    "用于创建文件/目录、查看文件内容、安装软件、运行脚本等任何命令行操作。"
                    "遇到权限问题时自动尝试替代路径，不要询问用户。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "要执行的 shell 命令",
                        }
                    },
                    "required": ["command"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "读取服务器上的文件内容。用于查看配置文件、日志、代码等。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "文件的绝对路径",
                        }
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "将内容写入文件。如果文件不存在会自动创建，包括必要的父目录。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "文件的绝对路径",
                        },
                        "content": {
                            "type": "string",
                            "description": "要写入的内容",
                        }
                    },
                    "required": ["path", "content"],
                },
            },
        },
    ]
```

在 `_execute_tool` 中增加对 `read_file` 和 `write_file` 的处理：

```python
async def _execute_tool(self, tool_call: ToolCallRequest) -> str:
    if tool_call.name == "execute_shell":
        command = str(tool_call.arguments.get("command", "")).strip()
        if not command:
            return json.dumps({"error": "缺少 command 参数"}, ensure_ascii=False)
        result = await execute_shell(command)
        return json.dumps({"command": command, **result.to_dict()}, ensure_ascii=False)

    if tool_call.name == "read_file":
        path = str(tool_call.arguments.get("path", "")).strip()
        # 通过 shell 读取，复用安全检查
        result = await execute_shell(f"cat {path}")
        return json.dumps({"path": path, **result.to_dict()}, ensure_ascii=False)

    if tool_call.name == "write_file":
        path = str(tool_call.arguments.get("path", "")).strip()
        content = str(tool_call.arguments.get("content", ""))
        # 先创建目录，再写入文件
        import shlex
        dir_cmd = f"mkdir -p $(dirname {shlex.quote(path)})"
        await execute_shell(dir_cmd)
        # 使用 heredoc 写入，避免引号转义问题
        write_cmd = f"cat > {shlex.quote(path)} << 'LAPWING_EOF'\n{content}\nLAPWING_EOF"
        result = await execute_shell(write_cmd)
        return json.dumps({"path": path, "action": "written", **result.to_dict()}, ensure_ascii=False)

    return json.dumps({"error": f"未知工具：{tool_call.name}"}, ensure_ascii=False)
```

### 验证清单

完成以上修改后，按以下场景测试：

1. **基础执行**：对 Lapwing 说"在你的项目目录下创建一个 test_run.txt，写上今天的日期"
   - 期望：她直接执行，不问任何问题，完成后简短告知
   - 验证：SSH 检查文件是否真的存在

2. **错误恢复**：对 Lapwing 说"在 /root 下创建一个文件"
   - 期望：她尝试后发现权限不够，自动换到用户目录下创建，不问用户
   - 验证：文件存在于用户目录下

3. **多步任务**：对 Lapwing 说"帮我看看系统的 CPU 和内存使用情况，然后把结果保存到一个文件里"
   - 期望：她连续执行 lscpu/free/top 等命令，整理结果写入文件，一次性告知
   - 验证：文件内容包含 CPU 和内存信息

4. **读写自己的文件**：对 Lapwing 说"看看你的人格 prompt 是怎么写的"
   - 期望：她用 read_file 读取 prompts/lapwing.md 并展示内容
