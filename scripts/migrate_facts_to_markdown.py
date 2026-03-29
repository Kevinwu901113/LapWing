"""一次性迁移脚本：将 SQLite user_facts 写入 KEVIN.md。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import DB_PATH, KEVIN_NOTES_PATH
from src.memory.conversation import ConversationMemory


async def main():
    memory = ConversationMemory(DB_PATH)
    await memory.init_db()

    chat_ids = await memory.get_all_chat_ids()
    all_facts = []
    for chat_id in chat_ids:
        facts = await memory.get_user_facts(chat_id)
        all_facts.extend(facts)

    if not all_facts:
        print("没有 user_facts 需要迁移。")
        await memory.close()
        return

    # 按分类整理
    categories: dict[str, list[str]] = {}
    for fact in all_facts:
        key = fact["fact_key"]
        value = fact["fact_value"]
        # 跳过 memory_summary 类型
        if key.startswith("memory_summary_"):
            continue
        # 提取分类前缀
        parts = key.split("_", 1)
        category = parts[0] if len(parts) > 1 else "其他"
        categories.setdefault(category, []).append(f"- {key}: {value}")

    if not categories:
        print("没有非摘要 facts 需要迁移。")
        await memory.close()
        return

    # 构建 markdown
    lines = ["# 关于 Kevin\n", "这里记录我对他的了解。\n"]
    for cat, items in sorted(categories.items()):
        lines.append(f"\n## {cat}\n")
        lines.extend(items)

    KEVIN_NOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
    KEVIN_NOTES_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"已迁移 {sum(len(v) for v in categories.values())} 条 facts 到 {KEVIN_NOTES_PATH}")

    await memory.close()


asyncio.run(main())
