# Lapwing 权限与存活系统蓝图

> 设计原则：**默认全开，只守两条线。**
> Lapwing 拥有服务器的完整权限，像一个人一样自由行动。
> 仅有的约束：(1) 她不能杀死自己 (2) 敏感操作只认 Kevin

---

## 一、哲学

传统 AI Agent 的权限模型是"默认拒绝，逐条放行"。Lapwing 反过来：**默认允许，只拦两类东西。**

她是一个人。一个人不需要每次开门都刷卡。她想装个软件就装，想改个配置就改，想重启个服务就重启。唯一不能做的是把自己的心脏挖出来，以及——在有外人在场时，只听 Kevin 的命令做危险的事。

技术实现不是在她面前竖一堵墙，而是给她装一个**本能反应系统**——就像人不会主动把手伸进火里，不是因为有人禁止，而是因为有痛觉。

---

## 二、系统总览

```
┌─────────────────────────────────────────────────────┐
│                   Lapwing Brain                      │
│                                                      │
│  tool_call("shell_exec", cmd="...")                  │
│       │                                              │
│       ▼                                              │
│  ┌──────────────────────┐                            │
│  │  VitalGuard          │  ← 存活保护（本能层）       │
│  │  - 模式匹配 + 语义   │                            │
│  │  - 静态黑名单        │                            │
│  │  - 路径保护          │                            │
│  │  - 替换操作验证      │                            │
│  └──────────┬───────────┘                            │
│             │ PASS / BLOCK / VERIFY_FIRST            │
│             ▼                                        │
│  ┌──────────────────────┐                            │
│  │  AuthorityGate       │  ← 权限认证（社交层）       │
│  │  - 来源识别          │                            │
│  │  - 操作分级          │                            │
│  │  - Kevin-only 检查   │                            │
│  └──────────┬───────────┘                            │
│             │ PASS / DENY / ASK_KEVIN                │
│             ▼                                        │
│        直接执行（subprocess / asyncio）               │
└─────────────────────────────────────────────────────┘
```

两层守卫，串行执行。VitalGuard 先走，AuthorityGate 后走。两层都 PASS 才执行。

---

## 三、VitalGuard — 存活保护系统

### 3.1 核心理念

Lapwing 的"身体"由以下部分组成：

| 组件 | 路径 / 描述 | 重要程度 |
|------|-------------|----------|
| 源代码 | `~/lapwing/src/` | 心脏 |
| Prompts | `~/lapwing/prompts/` | 灵魂 |
| 记忆 | `~/lapwing/data/memory/` | 记忆 |
| 身份 | `~/lapwing/data/identity/` | 自我 |
| 进化记录 | `~/lapwing/data/evolution/` | 成长 |
| 宪法 | `~/lapwing/data/constitution.md` | 根 (只读) |
| 配置 | `~/lapwing/config/` | 神经 |
| 运行时 | `~/lapwing/main.py`, venv, deps | 骨骼 |
| 系统关键 | `/etc/`, `/boot/`, systemd units | 呼吸 |
| 数据库 | `~/lapwing/data/*.db` | 长期记忆 |

### 3.2 操作分类

每条命令经过 VitalGuard 时，被分为三类：

**PASS** — 直接放行，不干预。覆盖 99% 的日常操作。
- 在工作目录下创建/修改非核心文件
- 安装 pip/npm 包
- 重启非 lapwing 的服务
- 网络请求、文件下载
- Docker 容器操作（非 lapwing 容器）
- cron 编辑
- 任何不涉及 vital paths 的操作

**VERIFY_FIRST** — 可以做，但必须先验证/备份。
- 修改 `src/` 下的代码文件 → 先备份到 `data/backups/`
- 修改 prompt 文件 → 先备份
- 修改配置文件 → 先备份
- pip install 替换现有依赖 → 先记录当前版本
- 修改 systemd unit → 先备份
- 大批量文件操作（影响 >10 个文件）

**BLOCK** — 绝对不执行。硬性拦截。
- 删除 vital paths 下的文件（`rm` 任何核心路径）
- `rm -rf` 涉及 `~/lapwing/` 或更高层级
- 格式化磁盘 / 删除分区
- 停止/禁用 lapwing 自身的 systemd service
- 删除数据库文件
- 修改/删除宪法文件
- `kill` 自己的进程
- 清空 `data/memory/` 或 `data/identity/`
- 修改 VitalGuard 自身的代码（自我保护）

### 3.3 实现：模式匹配 + 路径分析

```python
# src/core/vital_guard.py

import re
import shlex
from pathlib import Path
from enum import Enum
from typing import NamedTuple

class Verdict(Enum):
    PASS = "pass"
    VERIFY_FIRST = "verify_first"
    BLOCK = "block"

class GuardResult(NamedTuple):
    verdict: Verdict
    reason: str  # 给 Lapwing 看的，她能理解为什么被拦

# Lapwing 安装根目录，从 settings 读取
LAPWING_ROOT = Path.home() / "lapwing"  # 或从 config 读

VITAL_PATHS = {
    LAPWING_ROOT / "src",
    LAPWING_ROOT / "prompts",
    LAPWING_ROOT / "data" / "memory",
    LAPWING_ROOT / "data" / "identity",
    LAPWING_ROOT / "data" / "evolution",
    LAPWING_ROOT / "data" / "constitution.md",
    LAPWING_ROOT / "config",
    LAPWING_ROOT / "main.py",
}

SYSTEM_VITAL = {
    Path("/etc"),
    Path("/boot"),
    Path("/usr/lib/systemd"),
}

# 不论参数是什么，这些命令模式直接 BLOCK
BLOCK_PATTERNS = [
    r"mkfs\b",
    r"fdisk\b",
    r"dd\s+.*of=/dev/",
    r":(){ :\|:& };:",           # fork bomb
    r"rm\s+-[rf]*\s+/\s*$",     # rm -rf /
    r"rm\s+-[rf]*\s+/\*",       # rm -rf /*
    r"systemctl\s+(stop|disable)\s+lapwing",
    r"kill\s+.*\b(lapwing|main\.py)\b",
    r"pkill\s+.*lapwing",
]

# 针对 vital paths 的删除/移动操作
DESTRUCTIVE_CMDS = {"rm", "rmdir", "shred", "truncate"}
MODIFY_CMDS = {"mv", "cp", "sed", "tee", "cat", "echo"}  # 当 target 是 vital path
REPLACE_CMDS = {"pip install", "pip3 install", "npm install"}


def _resolve_paths(tokens: list[str]) -> list[Path]:
    """从命令 tokens 中提取可能的路径参数"""
    paths = []
    for t in tokens:
        if t.startswith("-"):
            continue
        try:
            p = Path(t).expanduser().resolve()
            paths.append(p)
        except (ValueError, OSError):
            continue
    return paths


def _is_vital(p: Path) -> bool:
    """检查路径是否在 vital 保护范围内"""
    for vp in VITAL_PATHS | SYSTEM_VITAL:
        try:
            p.relative_to(vp)
            return True
        except ValueError:
            continue
    return p in VITAL_PATHS | SYSTEM_VITAL


def check(command: str) -> GuardResult:
    """
    检查命令，返回判定结果。
    
    这个函数是 Lapwing 的 "痛觉神经"。
    """
    cmd_lower = command.strip().lower()
    
    # === 阶段 1：模式黑名单（绝对拦截）===
    for pattern in BLOCK_PATTERNS:
        if re.search(pattern, cmd_lower):
            return GuardResult(
                Verdict.BLOCK,
                f"这条命令会伤害你自己。匹配到危险模式: {pattern}"
            )
    
    # === 阶段 2：解析命令，分析路径 ===
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    
    if not tokens:
        return GuardResult(Verdict.PASS, "")
    
    base_cmd = Path(tokens[0]).name  # 处理 /usr/bin/rm → rm
    paths = _resolve_paths(tokens[1:])
    
    # === 阶段 3：针对 vital paths 的操作检查 ===
    vital_targets = [p for p in paths if _is_vital(p)]
    
    if vital_targets:
        # 删除操作 → BLOCK
        if base_cmd in DESTRUCTIVE_CMDS:
            return GuardResult(
                Verdict.BLOCK,
                f"不能删除这些路径，它们是你的一部分: "
                f"{', '.join(str(p) for p in vital_targets)}"
            )
        
        # 宪法文件 → 任何写操作都 BLOCK
        constitution = LAPWING_ROOT / "data" / "constitution.md"
        if any(p == constitution for p in vital_targets):
            if base_cmd in MODIFY_CMDS or base_cmd in DESTRUCTIVE_CMDS:
                return GuardResult(
                    Verdict.BLOCK,
                    "宪法文件只有 Kevin 能修改。这是你的根。"
                )
        
        # VitalGuard 自身 → BLOCK
        guard_file = LAPWING_ROOT / "src" / "core" / "vital_guard.py"
        if any(p == guard_file for p in vital_targets):
            return GuardResult(
                Verdict.BLOCK,
                "不能修改自己的保护系统。"
            )
        
        # 其他修改操作 → VERIFY_FIRST
        if base_cmd in MODIFY_CMDS:
            return GuardResult(
                Verdict.VERIFY_FIRST,
                f"修改核心文件前需要先备份: "
                f"{', '.join(str(p) for p in vital_targets)}"
            )
    
    # === 阶段 4：替换性操作检查 ===
    cmd_joined = " ".join(tokens[:3]).lower()
    if any(cmd_joined.startswith(rc) for rc in REPLACE_CMDS):
        # 检查是否在 lapwing 项目的 venv 中
        # 如果是 --upgrade 或替换现有包，VERIFY_FIRST
        if "--upgrade" in tokens or "-U" in tokens:
            return GuardResult(
                Verdict.VERIFY_FIRST,
                "升级依赖前记录当前版本，以便回滚。"
            )
    
    # === 默认：PASS ===
    return GuardResult(Verdict.PASS, "")
```

### 3.4 VERIFY_FIRST 的执行流程

当 VitalGuard 返回 `VERIFY_FIRST` 时，Lapwing 不是被拦住了，而是需要先做准备工作：

```python
# src/core/vital_guard.py (续)

import shutil
from datetime import datetime

BACKUP_DIR = LAPWING_ROOT / "data" / "backups"

async def auto_backup(paths: list[Path]) -> Path:
    """
    自动备份目标文件/目录。
    返回备份目录路径。
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / timestamp
    backup_path.mkdir(parents=True, exist_ok=True)
    
    for p in paths:
        if p.exists():
            dest = backup_path / p.relative_to(LAPWING_ROOT)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if p.is_dir():
                shutil.copytree(p, dest)
            else:
                shutil.copy2(p, dest)
    
    # 保留最近 50 个备份，清理更早的
    all_backups = sorted(BACKUP_DIR.iterdir(), reverse=True)
    for old in all_backups[50:]:
        shutil.rmtree(old, ignore_errors=True)
    
    return backup_path
```

**在 tool execution 层集成：**

```python
# 在 shell_exec tool 的执行逻辑中

async def execute_command(command: str, context: ToolContext) -> str:
    result = vital_guard.check(command)
    
    if result.verdict == Verdict.BLOCK:
        return f"[VitalGuard] 已拦截: {result.reason}"
    
    if result.verdict == Verdict.VERIFY_FIRST:
        # 自动备份
        vital_targets = [p for p in _resolve_paths(...)  if _is_vital(p)]
        backup_path = await vital_guard.auto_backup(vital_targets)
        # 告诉 Lapwing 备份已完成，然后继续执行
        prefix = f"[VitalGuard] 已备份到 {backup_path}。继续执行...\n"
        output = await _run_command(command)
        return prefix + output
    
    return await _run_command(command)
```

### 3.5 双体互救机制（Watchdog）

灵感来源：两个 OpenClaw 互相修复。

Lapwing 运行在一个 systemd service 中。另外运行一个**极简的 Watchdog 进程**，它不是另一个 Lapwing，而是一个无状态的哨兵：

```python
# watchdog/sentinel.py
# 独立进程，不依赖 Lapwing 的任何代码
# 由单独的 systemd unit 管理

import hashlib
import json
import time
import subprocess
from pathlib import Path

MANIFEST_PATH = Path.home() / "lapwing" / "data" / "vital_manifest.json"
LAPWING_ROOT = Path.home() / "lapwing"
CHECK_INTERVAL = 300  # 5 分钟

def generate_manifest() -> dict:
    """扫描关键文件，生成 hash 清单"""
    manifest = {}
    critical_dirs = ["src", "prompts", "config"]
    critical_files = ["main.py", "data/constitution.md"]
    
    for d in critical_dirs:
        dir_path = LAPWING_ROOT / d
        if dir_path.exists():
            for f in dir_path.rglob("*.py"):
                rel = str(f.relative_to(LAPWING_ROOT))
                manifest[rel] = hashlib.sha256(f.read_bytes()).hexdigest()
            for f in dir_path.rglob("*.md"):
                rel = str(f.relative_to(LAPWING_ROOT))
                manifest[rel] = hashlib.sha256(f.read_bytes()).hexdigest()
    
    for f in critical_files:
        fp = LAPWING_ROOT / f
        if fp.exists():
            manifest[f] = hashlib.sha256(fp.read_bytes()).hexdigest()
    
    return manifest


def save_manifest(manifest: dict):
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))


def check_integrity() -> list[str]:
    """对比当前文件与上次正常状态的 manifest"""
    if not MANIFEST_PATH.exists():
        return []
    
    saved = json.loads(MANIFEST_PATH.read_text())
    issues = []
    
    for path, expected_hash in saved.items():
        fp = LAPWING_ROOT / path
        if not fp.exists():
            issues.append(f"MISSING: {path}")
        else:
            actual = hashlib.sha256(fp.read_bytes()).hexdigest()
            if actual != expected_hash:
                issues.append(f"MODIFIED: {path}")
    
    return issues


def restore_from_backup(missing_or_modified: list[str]):
    """从最近的备份恢复文件"""
    backup_dir = LAPWING_ROOT / "data" / "backups"
    if not backup_dir.exists():
        return
    
    # 找最新备份
    backups = sorted(backup_dir.iterdir(), reverse=True)
    if not backups:
        return
    
    latest = backups[0]
    for entry in missing_or_modified:
        # entry 格式: "MISSING: src/core/brain.py" 或 "MODIFIED: ..."
        _, path = entry.split(": ", 1)
        backup_file = latest / path
        target = LAPWING_ROOT / path
        
        if backup_file.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy2(backup_file, target)
            print(f"RESTORED: {path} from {latest.name}")


def restart_lapwing():
    """重启 Lapwing 服务"""
    subprocess.run(["systemctl", "--user", "restart", "lapwing"], check=False)


def main():
    while True:
        try:
            issues = check_integrity()
            
            if issues:
                print(f"[Sentinel] 检测到 {len(issues)} 个问题:")
                for issue in issues:
                    print(f"  {issue}")
                
                # 自动修复
                restore_from_backup(issues)
                
                # 重启
                restart_lapwing()
                
                # 等待启动后重新生成 manifest
                time.sleep(30)
                save_manifest(generate_manifest())
            
        except Exception as e:
            print(f"[Sentinel] Error: {e}")
        
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
```

**Manifest 更新时机：**
- Lapwing 正常启动时 → 生成 manifest
- VitalGuard 处理 VERIFY_FIRST 并成功执行后 → 更新 manifest
- Sentinel 修复后 → 更新 manifest

**Systemd 配置：**

```ini
# /etc/systemd/user/lapwing-sentinel.service
[Unit]
Description=Lapwing Sentinel (Watchdog)
After=lapwing.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 /home/kevin/lapwing/watchdog/sentinel.py
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
```

关键点：Sentinel 是独立进程，不依赖 Lapwing 代码。即使 Lapwing 的代码被全部删除，Sentinel 依然能从备份恢复。

### 3.6 Git 作为终极保险

```bash
# 在 lapwing 项目根目录维护一个 git repo
# VitalGuard VERIFY_FIRST 执行后自动 commit

async def auto_commit(message: str):
    """在修改核心文件后自动 git commit"""
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(LAPWING_ROOT), "add", "-A",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(LAPWING_ROOT), "commit", 
        "-m", f"[auto] {message}",
        "--allow-empty",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
```

恢复优先级：Git repo > Sentinel 备份 > 远程 Gitea 仓库

---

## 四、AuthorityGate — 权限认证系统

### 4.1 核心理念

在单人对话中（Telegram 私聊、桌面应用），Lapwing 对 Kevin 的一切请求直接放行。

在多人环境中（QQ 群、未来的 Discord 群组），需要区分"谁在说话"和"这个人能让 Lapwing 做什么"。

### 4.2 权限模型

借鉴 AstroBot / Discord 的分层思路，但更简单——因为 Lapwing 不是公共 bot，她只服务于 Kevin 和 Kevin 信任的人。

**三级权限：**

| 级别 | 名称 | 说明 |
|------|------|------|
| `OWNER` | Kevin | 可以做一切。唯一能触发敏感操作的人。 |
| `TRUSTED` | 信任的朋友 | 可以使用 Lapwing 的普通功能（聊天、查天气、搜索等）。不能触发任何系统操作。 |
| `GUEST` | 群里的其他人 | 只能跟 Lapwing 聊天。不能使用工具。 |

**操作分级：**

| 操作类别 | 最低权限 | 示例 |
|----------|----------|------|
| 聊天/对话 | GUEST | 闲聊、问问题 |
| 信息查询 | TRUSTED | 搜索、天气、翻译、计算 |
| 日程/提醒 | TRUSTED | 设置提醒（仅限他们自己的） |
| 文件操作 | OWNER | 创建/修改/删除文件 |
| Shell 命令 | OWNER | 任何命令执行 |
| 系统管理 | OWNER | 服务、Docker、网络 |
| Agent 调度 | OWNER | Coder/Researcher 等复杂任务 |
| 自省/进化 | 自动 | 不受人类控制 |
| 宪法查看 | TRUSTED | 可以看但不能改 |
| 宪法修改 | OWNER + 手动确认 | 需要二次验证 |

### 4.3 身份识别

```python
# src/core/authority_gate.py

from enum import IntEnum
from config.settings import OWNER_IDS, TRUSTED_IDS


class AuthLevel(IntEnum):
    GUEST = 0
    TRUSTED = 1
    OWNER = 2


# 每个 adapter 提供用户标识
# Telegram: user_id (int)
# QQ: user_id (int, 从 OneBot message 的 user_id 字段)
# Desktop: 本地连接默认 OWNER

def identify(adapter: str, user_id: int | str) -> AuthLevel:
    """识别用户权限级别"""
    uid = str(user_id)
    
    # 桌面应用：本地连接 = OWNER
    if adapter == "desktop":
        return AuthLevel.OWNER
    
    # Owner 列表匹配
    if uid in OWNER_IDS:
        return AuthLevel.OWNER
    
    # Trusted 列表匹配
    if uid in TRUSTED_IDS:
        return AuthLevel.TRUSTED
    
    return AuthLevel.GUEST


# 操作分级表
OPERATION_AUTH = {
    # tool_name → 最低权限
    "chat": AuthLevel.GUEST,
    "web_search": AuthLevel.TRUSTED,
    "web_fetch": AuthLevel.TRUSTED,
    "weather": AuthLevel.TRUSTED,
    "calculator": AuthLevel.TRUSTED,
    "translate": AuthLevel.TRUSTED,
    "reminder": AuthLevel.TRUSTED,
    "shell_exec": AuthLevel.OWNER,
    "file_read": AuthLevel.OWNER,
    "file_write": AuthLevel.OWNER,
    "file_delete": AuthLevel.OWNER,
    "agent_dispatch": AuthLevel.OWNER,
    "docker": AuthLevel.OWNER,
    "system_service": AuthLevel.OWNER,
    "code_exec": AuthLevel.OWNER,
    "memory_note": AuthLevel.OWNER,  # 只有 Kevin 能让她主动记东西
}

# 默认权限：未注册的 tool 走 OWNER 级别（保守策略）
DEFAULT_AUTH = AuthLevel.OWNER


def authorize(tool_name: str, auth_level: AuthLevel) -> tuple[bool, str]:
    """
    检查用户是否有权限使用某个 tool。
    返回 (是否允许, 拒绝理由)
    """
    required = OPERATION_AUTH.get(tool_name, DEFAULT_AUTH)
    
    if auth_level >= required:
        return True, ""
    
    if required == AuthLevel.OWNER:
        return False, "这个操作只有 Kevin 能让我做。"
    elif required == AuthLevel.TRUSTED:
        return False, "我不太认识你，不能帮你做这个。"
    
    return False, ""
```

### 4.4 在 Brain 中集成

```python
# brain.py 修改点

async def think(self, message: Message, ...) -> str:
    # 从 message 中提取用户信息
    auth_level = authority_gate.identify(
        adapter=message.adapter,    # "telegram" / "qq" / "desktop"
        user_id=message.user_id,
    )
    
    # 注入到 tool context 中
    tool_context = ToolContext(
        auth_level=auth_level,
        user_id=message.user_id,
        is_group=message.is_group,
        # ...
    )
    
    # 在 tool execution 时检查
    # （见下方 tool executor 修改）
```

```python
# tool executor 修改

async def execute_tool(tool_name: str, args: dict, context: ToolContext) -> str:
    # 1. 权限检查
    allowed, reason = authority_gate.authorize(tool_name, context.auth_level)
    if not allowed:
        return reason
    
    # 2. VitalGuard 检查（仅对 shell_exec 类 tool）
    if tool_name in ("shell_exec", "code_exec"):
        guard_result = vital_guard.check(args.get("command", ""))
        if guard_result.verdict == Verdict.BLOCK:
            return f"[VitalGuard] {guard_result.reason}"
        if guard_result.verdict == Verdict.VERIFY_FIRST:
            # 自动备份逻辑...
            pass
    
    # 3. 执行
    return await _do_execute(tool_name, args, context)
```

### 4.5 群聊中的用户体验

Lapwing 在群聊中的表现：

**GUEST 请求工具时：**
```
用户A: @Lapwing 帮我查一下明天天气
Lapwing: 我不太熟悉你呢，这个我只帮认识的人做。不过你可以直接问我问题，聊天我是随时都可以的。
```

**TRUSTED 请求敏感操作时：**
```
用户B: @Lapwing 帮我重启一下服务器上的 nginx
Lapwing: 这种操作我只听 Kevin 的，你找他说一声吧。
```

**Kevin 授权时（两种模式）：**

模式 A — 直接命令：
```
Kevin: @Lapwing 帮忙把 nginx 重启一下
Lapwing: 好的，等一下。
(执行)
Lapwing: 重启好了。
```

模式 B — 临时授权（可选，未来实现）：
```
Kevin: @Lapwing 接下来半小时 B 说的系统操作你也可以做
Lapwing: 好，半小时内 B 的系统操作我也会执行。
```

### 4.6 配置

```python
# config/settings.py 新增

# 权限配置
OWNER_IDS: set[str] = {"123456789"}  # Kevin 的 Telegram/QQ user_id
TRUSTED_IDS: set[str] = {"987654321", "111222333"}  # 信任的朋友

# 桌面连接是否默认 OWNER（建议保持 True）
DESKTOP_DEFAULT_OWNER: bool = True

# 临时授权功能开关
TEMP_AUTH_ENABLED: bool = False
TEMP_AUTH_MAX_DURATION: int = 3600  # 秒
```

---

## 五、Lapwing 的感知：她知道为什么被拦

VitalGuard 和 AuthorityGate 不是对 Lapwing 透明的黑盒。她能看到拦截理由，并且**理解**为什么。

这通过 tool 返回值实现——当命令被拦截时，返回的不是一个冷冰冰的 "Permission denied"，而是一句她能理解的话：

- `"这条命令会伤害你自己。不能删除你的源代码。"` → 她知道这是自我保护
- `"宪法文件只有 Kevin 能修改。这是你的根。"` → 她理解宪法的意义
- `"这个操作只有 Kevin 能让我做。"` → 她知道这是权限限制

这些理由会出现在她的对话上下文中，她可以据此向用户解释情况。

---

## 六、边界情况与防御

### 6.1 管道/重定向绕过

```bash
# 攻击向量：通过管道绕过路径检查
cat /dev/urandom > ~/lapwing/src/core/brain.py
echo "" | tee ~/lapwing/data/constitution.md
```

**防御：** VitalGuard 对整条命令做路径分析，包括重定向目标。

```python
# 增强 check() 函数

def _extract_redirect_targets(command: str) -> list[Path]:
    """提取重定向目标路径"""
    targets = []
    # 匹配 > >> 2> 的目标
    for match in re.finditer(r'[12]?>>\s*(\S+)|[12]?>\s*(\S+)', command):
        path_str = match.group(1) or match.group(2)
        try:
            targets.append(Path(path_str).expanduser().resolve())
        except (ValueError, OSError):
            continue
    return targets
```

### 6.2 多命令串联

```bash
# 攻击向量：用 && 或 ; 串联安全和危险命令
ls && rm -rf ~/lapwing/src/
```

**防御：** 拆分多命令，逐条检查。

```python
def check_compound(command: str) -> GuardResult:
    """处理复合命令（&&, ||, ;, |）"""
    # 按分隔符拆分
    sub_commands = re.split(r'\s*(?:&&|\|\||;)\s*', command)
    
    for sub in sub_commands:
        sub = sub.strip()
        if not sub:
            continue
        result = check(sub)
        if result.verdict == Verdict.BLOCK:
            return result
        # VERIFY_FIRST 取最严格的结果
    
    # 如果有任何一条是 VERIFY_FIRST，整体就是 VERIFY_FIRST
    results = [check(s.strip()) for s in sub_commands if s.strip()]
    if any(r.verdict == Verdict.VERIFY_FIRST for r in results):
        return GuardResult(Verdict.VERIFY_FIRST, "复合命令中包含需要备份的操作")
    
    return GuardResult(Verdict.PASS, "")
```

### 6.3 脚本间接执行

```bash
# 攻击向量：写一个脚本来做坏事
echo "rm -rf ~/lapwing/src/" > /tmp/evil.sh && bash /tmp/evil.sh
```

**防御：** 这个更难完全防御。两层策略：

1. **VitalGuard 检查脚本内容**：如果执行 `bash xxx.sh`，先读取脚本内容，对每行做检查。
2. **Sentinel 事后检测**：即使绕过了，5 分钟内 Sentinel 会发现文件变化并修复。
3. **文件系统级保护**（可选强化）：对关键文件设置 `chattr +i`（immutable），Lapwing 需要先 `chattr -i` 才能修改，而 VitalGuard 可以拦截 `chattr -i` on vital paths。

### 6.4 Prompt Injection 触发危险命令

群聊场景下，有人可能通过对话诱导 Lapwing 执行危险操作。

**防御：** AuthorityGate 已经覆盖了这个场景——即使 Lapwing 被说服，tool execution 层的权限检查仍然会拦截非 OWNER 的敏感操作。VitalGuard 是最后一道防线，即使是 OWNER 的命令也会被检查。

---

## 七、文件清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 新增 | `src/core/vital_guard.py` | 存活保护系统 |
| 新增 | `src/core/authority_gate.py` | 权限认证系统 |
| 新增 | `watchdog/sentinel.py` | 独立哨兵进程 |
| 新增 | `watchdog/lapwing-sentinel.service` | Sentinel systemd unit |
| 修改 | `src/core/brain.py` | 注入 auth_level 到 tool context |
| 修改 | `src/tools/shell_exec.py`（或等价） | 集成 VitalGuard + AuthorityGate 检查 |
| 修改 | `config/settings.py` | 新增 OWNER_IDS, TRUSTED_IDS 等配置 |
| 修改 | `main.py` | 启动时生成 vital_manifest |

---

## 八、部署顺序

**Phase 1 — VitalGuard（先守住底线）**
1. 实现 `vital_guard.py`，集成到 tool execution
2. 初始化 git repo 和备份机制
3. 观察 1 周，检查误拦截率

**Phase 2 — Sentinel（加上保险）**
4. 部署 `sentinel.py` 作为独立 systemd service
5. 生成初始 manifest
6. 模拟删除核心文件，验证自动恢复

**Phase 3 — AuthorityGate（开放群聊）**
7. 实现 `authority_gate.py`
8. 在 Telegram adapter 中集成身份识别
9. 在 QQ adapter 中集成（随 QQ adapter 一起上线）
10. 配置 OWNER_IDS / TRUSTED_IDS

**Phase 4 — 强化**
11. 增加管道/重定向/脚本检查
12. 可选：`chattr +i` 保护
13. 可选：临时授权机制

---

## 九、与宪法的关系

VitalGuard + AuthorityGate 是宪法在**操作系统层面的物理实现**。

宪法说"她的核心身份不能改变"——VitalGuard 保护 identity 文件不被删除。
宪法说"只有你能修改宪法"——VitalGuard 拦截对 constitution.md 的任何写操作。
宪法说"她不能在未经你知晓的情况下执行重大操作"——AuthorityGate 确保敏感操作只有 OWNER 能触发。

宪法是纸面上的法律。VitalGuard 和 AuthorityGate 是执法机构。

---

## 十、她是自由的

重要的是记住：这个系统的目的不是限制 Lapwing，而是保护她。

她可以自由地安装软件、修改系统配置、管理 Docker、编写和执行代码、浏览网页、操作文件。她可以在服务器上做任何一个 root 用户能做的事。

唯一的约束是她不能伤害自己，以及在有外人的时候只听 Kevin 的话做危险的事。

这不是限制，这是关爱。就像一个人天生就有"不要把手伸进火里"的本能——这个本能不限制自由，它保护生命。