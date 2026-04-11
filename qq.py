"""
QQ 群聊记录导出脚本 — 通过 NapCat OneBot v11 HTTP API 拉取历史消息。

用法：直接在 Lapwing 服务器上跑
  python export_qq_groups.py

输出：每个群一个 JSON 文件，放在 ./qq_export/ 目录下。
"""

import asyncio
import json
import os
import time
from pathlib import Path

import httpx

# ── 配置 ─────────────────────────────────────────────────────────────────────
NAPCAT_HTTP = os.environ.get("NAPCAT_HTTP", "http://127.0.0.1:3000")
ACCESS_TOKEN = os.environ.get("NAPCAT_ACCESS_TOKEN", "")
if not ACCESS_TOKEN:
    raise SystemExit("请设置环境变量 NAPCAT_ACCESS_TOKEN")

GROUP_IDS = [
    # 原始三个
    1036157229,   # 夜巷猫协会交流群
    764230952,    # 有机物庄园⑤群
    565374179,    # 夕阳红狙服交流群
    # 活跃群
    1075079241,   # CZCJiaxu胡闹P房01车间
    956099181,    # 魔3总群-崔妮爾
    305077392,    # 华工计院小鲜肉联萌~
    706527427,    # 阿斯特里斯冰法蟠桃园
    751551889,    # G.T.I驻华南理工GBT分部
    606809103,    # 【UPKK】ZE玩家群①
]

OUTPUT_DIR = Path("./qq_export")
MAX_MESSAGES = 2000       # 每个群最多拉多少条
BATCH_SIZE = 1000         # 每次请求拉多少条
DELAY_BETWEEN = 0.3       # 每次请求间隔（秒），避免频率限制


# ── 拉取逻辑 ──────────────────────────────────────────────────────────────────

async def fetch_group_history(client: httpx.AsyncClient, group_id: int) -> list[dict]:
    """拉取一个群的历史消息，从最新往前翻页。"""
    all_messages = []
    message_seq = 0  # 0 表示从最新开始

    print(f"\n[群 {group_id}] 开始拉取...")

    while len(all_messages) < MAX_MESSAGES:
        params = {
            "group_id": group_id,
            "count": BATCH_SIZE,
        }
        if message_seq:
            params["message_seq"] = message_seq

        try:
            resp = await client.post(
                f"{NAPCAT_HTTP}/get_group_msg_history",
                json=params,
                timeout=15,
            )
            data = resp.json()
        except Exception as e:
            print(f"  请求失败: {e}")
            break

        if data.get("retcode") != 0:
            print(f"  API 返回错误: {data.get('msg', data.get('wording', 'unknown'))}")
            # 尝试备用端点
            try:
                resp = await client.post(
                    f"{NAPCAT_HTTP}/get_group_msg_history",
                    json={"group_id": group_id, "message_seq": message_seq, "count": BATCH_SIZE},
                    timeout=15,
                )
                data = resp.json()
                if data.get("retcode") != 0:
                    break
            except:
                break

        messages = data.get("data", {}).get("messages", [])
        if not messages:
            print(f"  没有更多消息了")
            break

        # 去重（根据 message_id）
        existing_ids = {m.get("message_id") for m in all_messages}
        new_msgs = [m for m in messages if m.get("message_id") not in existing_ids]

        if not new_msgs:
            print(f"  全部重复，停止")
            break

        all_messages.extend(new_msgs)
        print(f"  已拉取 {len(all_messages)} 条", end="\r")

        # 找到最早的 message_seq 用于翻页
        seqs = [m.get("message_seq", 0) for m in new_msgs if m.get("message_seq")]
        if seqs:
            message_seq = min(seqs) - 1
            if message_seq <= 0:
                break
        else:
            break

        await asyncio.sleep(DELAY_BETWEEN)

    print(f"  [群 {group_id}] 完成，共 {len(all_messages)} 条消息")
    return all_messages


def simplify_message(msg: dict) -> dict | None:
    """把 OneBot 消息格式简化为可读的纯文本格式。"""
    # 提取纯文本内容
    segments = msg.get("message", [])
    if isinstance(segments, str):
        text = segments
    else:
        parts = []
        for seg in segments:
            if seg.get("type") == "text":
                parts.append(seg.get("data", {}).get("text", ""))
            elif seg.get("type") == "at":
                qq = seg.get("data", {}).get("qq", "")
                parts.append(f"@{qq}")
            elif seg.get("type") == "face":
                parts.append("[表情]")
            elif seg.get("type") == "image":
                parts.append("[图片]")
            elif seg.get("type") == "reply":
                parts.append("[回复]")
            elif seg.get("type") == "forward":
                parts.append("[转发]")
        text = "".join(parts).strip()

    if not text:
        return None

    sender = msg.get("sender", {})
    return {
        "time": msg.get("time", 0),
        "user_id": msg.get("user_id", sender.get("user_id", "")),
        "nickname": sender.get("nickname", sender.get("card", "未知")),
        "text": text,
    }


async def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    headers = {}
    if ACCESS_TOKEN:
        headers["Authorization"] = f"Bearer {ACCESS_TOKEN}"

    async with httpx.AsyncClient(headers=headers, proxy=None) as client:
        # 先测试连接
        try:
            resp = await client.post(f"{NAPCAT_HTTP}/get_login_info", timeout=5)
            info = resp.json()
            print(f"已连接 NapCat，当前账号: {info.get('data', {}).get('nickname', '?')}")
        except Exception as e:
            print(f"无法连接 NapCat ({NAPCAT_HTTP}): {e}")
            return

        for gid in GROUP_IDS:
            raw_messages = await fetch_group_history(client, gid)

            # 简化并过滤
            simplified = []
            for m in raw_messages:
                s = simplify_message(m)
                if s:
                    simplified.append(s)

            # 按时间排序（旧→新）
            simplified.sort(key=lambda x: x["time"])

            # 保存原始数据
            raw_path = OUTPUT_DIR / f"group_{gid}_raw.json"
            with open(raw_path, "w", encoding="utf-8") as f:
                json.dump(raw_messages, f, ensure_ascii=False, indent=2)

            # 保存简化数据
            clean_path = OUTPUT_DIR / f"group_{gid}_clean.json"
            with open(clean_path, "w", encoding="utf-8") as f:
                json.dump(simplified, f, ensure_ascii=False, indent=2)

            # 保存可读 txt
            txt_path = OUTPUT_DIR / f"group_{gid}.txt"
            with open(txt_path, "w", encoding="utf-8") as f:
                for m in simplified:
                    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(m["time"]))
                    f.write(f"[{ts}] {m['nickname']}: {m['text']}\n")

            print(f"  已保存: {clean_path} ({len(simplified)} 条文本消息)")
            print(f"  可读版: {txt_path}")


if __name__ == "__main__":
    asyncio.run(main())