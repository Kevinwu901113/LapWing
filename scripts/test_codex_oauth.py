"""
Codex OAuth 连通性测试脚本。

用法:
    python scripts/test_codex_oauth.py
"""

import asyncio
import sys


async def main():
    try:
        from oauth_codex import AsyncClient
    except ImportError:
        print("❌ oauth-codex 未安装。运行: pip install oauth-codex --break-system-packages")
        sys.exit(1)

    client = AsyncClient()
    print("正在认证...")
    await client.authenticate()
    print("✅ 认证成功")

    # 测试纯文本
    print("\n测试纯文本回复...")
    completion = await client.chat.completions.create(
        model="gpt-5.3-codex",
        messages=[{"role": "user", "content": "Say hello in one word."}],
    )
    text = completion.choices[0].message.content
    print(f"✅ 纯文本: {text!r}")

    # 测试 tool calling
    print("\n测试 tool calling...")
    tools = [{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather for a location",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City name"},
                },
                "required": ["location"],
            },
        },
    }]
    completion = await client.chat.completions.create(
        model="gpt-5.3-codex",
        messages=[{"role": "user", "content": "What's the weather in Tokyo?"}],
        tools=tools,
        tool_choice="auto",
    )
    msg = completion.choices[0].message
    if msg.tool_calls:
        tc = msg.tool_calls[0]
        print(f"✅ Tool call: {tc.function.name}({tc.function.arguments})")
    else:
        print(f"⚠️  未返回 tool call，文本回复: {msg.content!r}")

    print("\n✅ Codex OAuth 连通测试完成")


if __name__ == "__main__":
    asyncio.run(main())
