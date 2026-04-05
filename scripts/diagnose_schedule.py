#!/usr/bin/env python3
"""诊断脚本：逐环测试定时任务管道。

在服务器上运行：
  cd ~/lapwing && python3 scripts/diagnose_schedule.py

会按顺序测试 7 个环节，在第一个失败的地方停下来并告诉你原因。
"""

import asyncio
import json
import sys
import os

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main():
    print("=" * 60)
    print("定时任务管道诊断")
    print("=" * 60)

    # ── 环节 1：数据库连接 + interval_minutes 列 ──
    print("\n[1/7] 数据库连接 + interval_minutes 列...")
    try:
        import aiosqlite
        from config.settings import DB_PATH
        db = await aiosqlite.connect(str(DB_PATH))
        # 检查 reminders 表是否存在
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='reminders'"
        ) as cursor:
            table = await cursor.fetchone()
        if not table:
            print("  ✗ reminders 表不存在！需要先运行一次 init_db()。")
            print("    → 重启 lapwing 服务即可（启动时会 init_db）。")
            return

        # 检查 interval_minutes 列
        async with db.execute("PRAGMA table_info(reminders)") as cursor:
            columns = [row[1] async for row in cursor]
        if "interval_minutes" not in columns:
            print("  ✗ reminders 表缺少 interval_minutes 列！")
            print("    → 重启 lapwing 服务（启动时 migration 会加列）。")
            return
        print(f"  ✓ 数据库连接正常，reminders 表有 {len(columns)} 列（含 interval_minutes）")
    except Exception as e:
        print(f"  ✗ 数据库错误: {e}")
        return

    # ── 环节 2：add_reminder 能否写入 ──
    print("\n[2/7] add_reminder 写入测试...")
    try:
        from src.memory.conversation import ConversationMemory
        from datetime import datetime, timedelta, timezone
        memory = ConversationMemory(DB_PATH)
        await memory.init_db()

        test_chat_id = "__diag_test__"
        now = datetime.now(timezone.utc)
        next_trigger = now + timedelta(minutes=5)

        rid = await memory.add_reminder(
            chat_id=test_chat_id,
            content="诊断测试提醒",
            recurrence_type="once",
            next_trigger_at=next_trigger,
            interval_minutes=None,
        )
        if rid:
            print(f"  ✓ add_reminder 成功，ID={rid}")
            # 清理
            await memory.cancel_reminder(test_chat_id, rid)
        else:
            print("  ✗ add_reminder 返回 0！")
            print("    可能原因：next_trigger_at <= now（时间计算问题）")
            print(f"    now={now.isoformat()}, next_trigger={next_trigger.isoformat()}")
            return
    except Exception as e:
        print(f"  ✗ add_reminder 异常: {e}")
        import traceback; traceback.print_exc()
        return

    # ── 环节 3：get_due_reminders 能否读取 ──
    print("\n[3/7] get_due_reminders 读取测试...")
    try:
        # 创建一个已过期的提醒
        past_trigger = now - timedelta(seconds=10)
        # add_reminder 会拒绝过去的 once 类型，所以先创建未来的再手动改
        rid2 = await memory.add_reminder(
            chat_id=test_chat_id,
            content="诊断到期测试",
            recurrence_type="once",
            next_trigger_at=now + timedelta(minutes=1),
        )
        if rid2:
            # 手动把 next_trigger_at 改到过去
            await db.execute(
                "UPDATE reminders SET next_trigger_at = ? WHERE id = ?",
                (past_trigger.isoformat(), rid2),
            )
            await db.commit()

            due = await memory.get_due_reminders(
                test_chat_id,
                now=now,
                grace_seconds=60,
                limit=10,
            )
            if due:
                print(f"  ✓ get_due_reminders 找到 {len(due)} 条到期提醒")
            else:
                print("  ✗ get_due_reminders 返回空！提醒写入了但查不到。")
                # 调试：直接查库
                async with db.execute(
                    "SELECT id, chat_id, next_trigger_at, active FROM reminders WHERE chat_id = ?",
                    (test_chat_id,),
                ) as cursor:
                    rows = [row async for row in cursor]
                print(f"    数据库中 {test_chat_id} 的提醒: {rows}")
                return
            # 清理
            await memory.cancel_reminder(test_chat_id, rid2)
        else:
            print("  ✗ 无法创建测试提醒")
            return
    except Exception as e:
        print(f"  ✗ get_due_reminders 异常: {e}")
        import traceback; traceback.print_exc()
        return

    # ── 环节 4：QQ 对话的 chat_id 存在吗？──
    print("\n[4/7] QQ chat_id 检查...")
    try:
        async with db.execute(
            "SELECT DISTINCT chat_id FROM conversations"
        ) as cursor:
            chat_ids = [row[0] async for row in cursor]
        print(f"  对话表中的 chat_id: {chat_ids}")

        # 检查是否有 QQ chat_id
        from config.settings import QQ_KEVIN_ID
        if QQ_KEVIN_ID:
            if QQ_KEVIN_ID in chat_ids:
                print(f"  ✓ QQ Kevin ID '{QQ_KEVIN_ID}' 在对话表中")
            else:
                print(f"  ✗ QQ Kevin ID '{QQ_KEVIN_ID}' 不在对话表中！")
                print("    → 心跳不会为这个 chat_id 运行 ReminderDispatchAction")
                print("    → 你需要先在 QQ 发一条消息让 chat_id 写入数据库")
        else:
            print("  ⚠ QQ_KEVIN_ID 未配置")
    except Exception as e:
        print(f"  ✗ chat_id 检查异常: {e}")
        import traceback; traceback.print_exc()

    # ── 环节 5：对话历史里有没有拒绝类残留 ──
    print("\n[5/7] 对话历史检查...")
    try:
        from config.settings import QQ_KEVIN_ID
        if QQ_KEVIN_ID:
            async with db.execute(
                "SELECT content FROM conversations WHERE chat_id = ? AND role = 'assistant' "
                "ORDER BY id DESC LIMIT 20",
                (QQ_KEVIN_ID,),
            ) as cursor:
                recent_replies = [row[0] async for row in cursor]

            poison_keywords = ["被禁", "没办法", "设不了", "不支持", "无法设置", "这个功能"]
            poisoned = [r for r in recent_replies if any(k in r for k in poison_keywords)]
            if poisoned:
                count = len(poisoned)
                print(f"  ✗ 发现 {count} 条拒绝类回复在对话历史中！")
                for p in poisoned[:3]:
                    print(f'    → "{p[:80]}"')
                print("    → 必须清除对话历史！运行：")
                print(f'    python3 -c "import sqlite3; db=sqlite3.connect(\'data/lapwing.db\'); '
                      f"db.execute(\\\"DELETE FROM conversations WHERE chat_id='{QQ_KEVIN_ID}'\\\"); "
                      f'db.commit(); print(\'cleared\')"')
            else:
                count = len(recent_replies)
                print(f"  ✓ 最近 {count} 条回复中没有拒绝类残留")
    except Exception as e:
        print(f"  ✗ 历史检查异常: {e}")

    # ── 环节 6：模型能否用新 schema 调 tool ──
    print("\n[6/7] MiniMax tool calling 测试...")
    try:
        from openai import AsyncOpenAI
        from config.settings import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

        client = AsyncOpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
        tools = [{
            "type": "function",
            "function": {
                "name": "schedule_task",
                "description": "设置提醒或定时任务。用户说「5分钟后叫我」时用这个。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "提醒内容"},
                        "trigger_type": {
                            "type": "string",
                            "enum": ["delay", "daily", "once", "interval"],
                            "description": "触发方式。delay=N分钟后",
                        },
                        "delay_minutes": {
                            "type": "integer",
                            "description": "delay类型的延迟分钟数",
                        },
                    },
                    "required": ["content", "trigger_type"],
                },
            },
        }]

        # 测试 1: 简单 prompt
        r = await client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": "你是助手。用户要求设提醒时必须调用 schedule_task 工具。"},
                {"role": "user", "content": "5分钟后叫我"},
            ],
            tools=tools,
            tool_choice="auto",
        )
        msg = r.choices[0].message
        if msg.tool_calls:
            tc = msg.tool_calls[0]
            args = json.loads(tc.function.arguments)
            print(f"  ✓ 简单 prompt: 模型调了 {tc.function.name}")
            print(f"    参数: {json.dumps(args, ensure_ascii=False)}")
        else:
            print(f"  ✗ 简单 prompt: 模型没调工具！")
            print(f"    回复: {msg.content[:200]}")
            print("    → MiniMax 可能不支持新的结构化 schema")

        # 测试 2: Lapwing 人格 prompt
        with open("prompts/lapwing_soul.md") as f:
            soul = f.read()
        with open("prompts/lapwing_capabilities.md") as f:
            caps = f.read()

        r2 = await client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": soul + "\n\n" + caps},
                {"role": "user", "content": "5分钟后叫我"},
            ],
            tools=tools,
            tool_choice="auto",
        )
        msg2 = r2.choices[0].message
        if msg2.tool_calls:
            tc2 = msg2.tool_calls[0]
            args2 = json.loads(tc2.function.arguments)
            print(f"  ✓ 人格 prompt: 模型调了 {tc2.function.name}")
            print(f"    参数: {json.dumps(args2, ensure_ascii=False)}")
        else:
            print(f"  ✗ 人格 prompt: 模型没调工具！")
            print(f"    回复: {msg2.content[:200]}")
            print("    → Lapwing 人格 prompt 可能抑制了 tool calling")

    except Exception as e:
        print(f"  ✗ MiniMax 调用异常: {e}")
        import traceback; traceback.print_exc()

    # ── 环节 7：心跳状态 ──
    print("\n[7/7] 心跳状态检查...")
    try:
        import subprocess
        result = subprocess.run(
            ["grep", "-i", "心跳已启动\\|heartbeat.*start\\|分钟心跳\\|reminder_dispatch",
             "logs/lapwing.log"],
            capture_output=True, text=True, timeout=5,
        )
        lines = result.stdout.strip().split("\n")[-10:]
        if lines and lines[0]:
            print("  最近心跳日志：")
            for line in lines:
                print(f"    {line[-120:]}")
        else:
            print("  ⚠ 日志中没找到心跳相关记录")

        # 检查 ReminderDispatchAction 是否执行过
        result2 = subprocess.run(
            ["grep", "-c", "reminder_dispatch\\|已发送提醒", "logs/lapwing.log"],
            capture_output=True, text=True, timeout=5,
        )
        count = int(result2.stdout.strip() or "0")
        if count:
            print(f"  ✓ ReminderDispatchAction 在日志中出现了 {count} 次")
        else:
            print("  ⚠ ReminderDispatchAction 从未在日志中出现过")
    except Exception as e:
        print(f"  ✗ 日志检查异常: {e}")

    # ── 清理 ──
    try:
        await db.execute("DELETE FROM reminders WHERE chat_id = ?", ("__diag_test__",))
        await db.commit()
        await db.close()
        await memory.close()
    except Exception:
        pass

    print("\n" + "=" * 60)
    print("诊断完成。把以上输出贴给我。")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())