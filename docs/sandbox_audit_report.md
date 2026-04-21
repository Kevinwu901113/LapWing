# 沙盒（Sandbox）结构调研报告

> 生成时间：2026-04-20
> 扫描范围：`/home/kevin/lapwing` 全项目

---

## 1. 文件扫描汇总

### 1.1 关键字命中文件

| 关键字 | 文件路径 | 行号 | 说明 |
|--------|----------|------|------|
| `docker run` | `src/skills/skill_executor.py` | 78–85 | Skill Docker 沙盒执行 |
| `docker run` | `src/tools/shell_executor.py` | 202–214 | Shell Docker 后端 |
| `asyncio.create_subprocess_exec` | `src/skills/skill_executor.py` | 87, 144 | Skill 沙盒 + 主机执行 |
| `asyncio.create_subprocess_exec` | `src/tools/shell_executor.py` | 217, 295 | Shell Docker + 本地执行 |
| `asyncio.create_subprocess_exec` | `src/tools/code_runner.py` | 41 | Python 代码沙盒 |
| `asyncio.create_subprocess_exec` | `src/core/verifier.py` | 257 | Git 命令验证 |
| `subprocess` | `src/skills/skill_executor.py` | 186 | runner 内 pip install |
| `sandbox` / `sandboxuser` | `docker/sandbox/Dockerfile` | 12–15 | 沙盒容器镜像 |
| `sandbox` | `docker/sandbox/runner_template.py` | 1–17 | 沙盒内运行器模板 |
| `sandbox` | `src/skills/skill_executor.py` | 13, 28, 50, 62 | 沙盒路由逻辑 |
| `sandbox` | `src/tools/shell_executor.py` | 24–37, 200 | Docker 后端配置 |
| `isolated` / `隔离` | `src/tools/code_runner.py` | 1, 27 | 临时目录隔离 |
| `workspace` | `src/tools/workspace_tools.py` | 12, 15–22 | Agent 工作区沙箱 |
| `container` | `src/app/container.py` | 720–750 | DI 容器创建 SkillExecutor |

### 1.2 未找到的高级隔离关键字

以下关键字在整个代码库中 **未找到**，表明未使用对应技术：

- `chroot` / `namespace` / `cgroup` / `seccomp`
- `firejail` / `nsjail` / `bubblewrap`
- `Popen`（直接调用；所有子进程均通过 `asyncio.create_subprocess_exec`）

---

## 2. 架构梳理

### 2.1 沙盒入口与管理模块

Lapwing 实现了 **三层执行隔离体系**，各有独立入口：

```
┌──────────────────────────────────────────────────────────┐
│                 LLM Tool Loop (TaskRuntime)               │
├─────────────┬──────────────────┬─────────────────────────┤
│ run_skill   │ execute_shell    │ run_python_code          │
│ ↓           │ ↓                │ ↓                        │
│ SkillExecutor│ ShellExecutor   │ CodeRunner               │
│ (skill_executor.py)│(shell_executor.py)│(code_runner.py)  │
├─────────────┼──────────────────┼─────────────────────────┤
│ Docker 沙盒  │ Docker 后端      │ 临时目录隔离             │
│ 或主机执行    │ 或本地执行       │                         │
└─────────────┴──────────────────┴─────────────────────────┘
```

**管理模块一览：**

| 模块 | 文件 | 职责 |
|------|------|------|
| `SkillExecutor` | `src/skills/skill_executor.py` | 技能执行路由：draft/testing/broken → Docker，stable → 主机 |
| `SkillStore` | `src/skills/skill_store.py` | 技能元数据 CRUD + 执行记录 |
| `ShellExecutor` | `src/tools/shell_executor.py` | Shell 命令执行，支持 local / docker 双后端 |
| `CodeRunner` | `src/tools/code_runner.py` | Python 代码执行，临时目录隔离 |
| `workspace_tools` | `src/tools/workspace_tools.py` | Coder Agent 文件操作，路径约束到 `data/agent_workspace/` |

### 2.2 沙盒生命周期

#### A. Skill Docker 沙盒（`skill_executor.py:62–126`）

```
创建                           使用                              销毁
─────                         ─────                            ─────
1. tempfile.mkdtemp()         5. docker run --rm               8. proc.communicate()
2. 写入 skill.py              6.   --network none              9. 解析 stdout/stderr
3. 生成 runner.py             7.   -v {tmp}:/workspace:ro     10. shutil.rmtree(tmp_dir)
4. 拼装 docker cmd                 --user sandboxuser               (finally 块)
                                   python3 runner.py
                              超时 → proc.kill()
```

**关键特性：**
- 临时目录以 `lapwing_skill_` 前缀创建
- Docker `--rm` 确保容器在退出后自动删除
- 工作区以 **只读** 方式挂载（`:ro`）
- 超时后通过 `proc.kill()` 强制终止
- `finally` 块中无条件清理临时目录

#### B. Shell Docker 后端（`shell_executor.py:200–272`）

```
创建                           使用                              销毁
─────                         ─────                            ─────
1. 拼装 docker cmd            3. docker run --rm               5. proc.communicate()
2. 设置资源限制                4.   --read-only                 6. 解析结果
     --memory=512m                 --cap-drop=ALL              7. 记录日志
     --cpus=1.0                    --tmpfs /tmp:rw,size=64m
                                   bash -c {command}
```

#### C. Python 代码执行（`code_runner.py:26–70`）

```
创建                           使用                              销毁
─────                         ─────                            ─────
1. tempfile.mkdtemp()         3. sys.executable script.py      5. 解析 stdout/stderr
2. 写入 script.py             4. cwd=tmp_dir                   6. shutil.rmtree(tmp_dir)
                              超时 → proc.kill()                   (finally 块)
```

### 2.3 沙盒与 Agent 系统的关系

```
┌─────────────┐     delegate_to_agent     ┌──────────────┐
│  Team Lead  │ ─────────────────────────► │  Researcher  │
│             │                            │  tools: research, browse
│  tools:     │                            └──────────────┘
│  delegate   │
│  _to_agent  │     delegate_to_agent     ┌──────────────┐
│             │ ─────────────────────────► │    Coder     │
└─────────────┘                            │  tools:      │
                                           │  ws_file_*   │
                                           │  execute_shell│ ← _noop_shell 阻断
                                           │  run_python  │ ← CodeRunner 执行
                                           └──────────────┘
                                                 │
                                                 ▼
                                           data/agent_workspace/
                                           (workspace_tools.py 路径沙箱)
```

**Agent 与沙盒的交互：**

| Agent | 直接调用沙盒？ | 间接接触 | 说明 |
|-------|----------------|----------|------|
| Team Lead | 否 | 否 | 只有 `delegate_to_agent` 工具 |
| Researcher | 否 | 否 | 只有 `research` + `browse` |
| Coder | 间接 | `run_python_code` → CodeRunner | Shell 被 `_noop_shell` 阻断（`base.py:245`） |
| Lapwing 主循环 | 是 | `run_skill` → SkillExecutor | 通过 `skill_tools.py` 触发 |
| Lapwing 主循环 | 是 | `execute_shell` → ShellExecutor | 通过 `handlers.py` 触发 |

**重要设计：** Agent 的 `ToolExecutionContext` 中 `execute_shell` 被替换为 `_noop_shell`（`src/agents/base.py:245`），因此即使 Coder Agent 的 profile 包含 `execute_shell`，实际执行时 shell 能力被阻断。Agent 通过 `run_python_code`（CodeRunner 临时目录隔离）和 `ws_file_*`（路径沙箱到 `data/agent_workspace/`）操作。

### 2.4 沙盒内可用工具/命令/权限边界

#### Docker Skill 沙盒内部环境

| 维度 | 状态 |
|------|------|
| 基础镜像 | `python:3.12-slim` |
| 系统工具 | `git`, `curl`, `wget`, `jq` |
| Python 包 | `requests`, `beautifulsoup4`, `httpx`, `lxml`, `pandas`, `numpy`, `pyyaml`, `toml` |
| 运行用户 | `sandboxuser`（非 root） |
| 网络 | **完全禁止**（`--network none`） |
| 文件系统 | `/workspace` 只读挂载；容器 rootfs 可写但 `--rm` 后销毁 |
| 动态依赖 | 支持 `pip install`（通过 runner.py 注入，但 `--network none` 下会失败） |

#### Docker Shell 后端环境

| 维度 | 状态 |
|------|------|
| 镜像 | `lapwing-sandbox:latest` |
| 网络 | `--network=host`（共享宿主机网络） |
| 文件系统 | `--read-only` 根 + `/tmp` 64MB 可写 + 工作区挂载 |
| Capabilities | `--cap-drop=ALL` |
| CPU | 1.0 核 |
| 内存 | 512MB |

#### CodeRunner 环境

| 维度 | 状态 |
|------|------|
| 隔离 | 临时目录（`/tmp/lapwing_coder_*`） |
| 网络 | 无限制（继承宿主机） |
| 文件系统 | 临时目录内可读写，执行后自动清理 |
| Python | 使用宿主机 `sys.executable` |

### 2.5 资源限制

| 组件 | 超时 | 内存 | CPU | 输出截断 | 配置位置 |
|------|------|------|-----|----------|----------|
| SkillExecutor (sandbox) | 30s（默认，最大 300s） | 无限制* | 无限制* | 4000 字符 | `config.toml` [skill] / env `SKILL_SANDBOX_TIMEOUT` |
| SkillExecutor (host) | 30s | 无限制 | 无限制 | 4000 字符 | 同上 |
| ShellExecutor (docker) | 30s | 512MB | 1.0 核 | 4000 字符 | `config.toml` [shell] / env `SHELL_TIMEOUT` |
| ShellExecutor (local) | 30s | 无限制 | 无限制 | 4000 字符 | 同上 |
| CodeRunner | 10s | 无限制 | 无限制 | 2000 字符 | 硬编码 |
| Coder Agent | 600s | 无限制 | 无限制 | — | `src/agents/coder.py:64` |
| Researcher Agent | 300s | 无限制 | 无限制 | — | `src/agents/researcher.py:71` |
| Team Lead Agent | 300s | 无限制 | 无限制 | — | `src/agents/team_lead.py:71` |

> \* SkillExecutor Docker 沙盒 **未设置** `--memory` 和 `--cpus` 参数（与 ShellExecutor Docker 后端不同）。

---

## 3. 安全机制

### 3.1 文件系统隔离

| 组件 | 隔离方式 | 能否访问宿主机文件 |
|------|----------|-------------------|
| Skill Docker 沙盒 | `-v {tmp}:/workspace:ro` 只读挂载 | **否**（仅能读取注入的 skill.py 和 runner.py） |
| Shell Docker 后端 | `-v {workspace}:/workspace` 读写挂载 + `--read-only` 根 | **有限**（仅挂载的工作区目录） |
| CodeRunner | `tempfile.mkdtemp()` + `cwd=tmp_dir` | **是**（无文件系统隔离，只是 CWD 切换） |
| Coder Agent workspace | `_resolve_safe()` 路径校验 | **否**（只能操作 `data/agent_workspace/` 下） |
| 主机 Skill 执行 | `tempfile.mkdtemp()` + `cwd=tmp_dir` | **是**（无文件系统隔离） |

**安全隐患：**
- CodeRunner 和主机 Skill 执行虽然在临时目录中运行，但代码可以通过绝对路径访问宿主机任意文件
- Skill Docker 沙盒的动态依赖安装（`pip install`）在 `--network none` 下会静默失败

### 3.2 网络权限

| 组件 | 网络访问 | 配置 |
|------|----------|------|
| Skill Docker 沙盒 | **完全禁止** | `--network none`（`skill_executor.py:80`） |
| Shell Docker 后端 | **完全开放** | `--network=host`（`shell_executor.py:206`） |
| CodeRunner | **完全开放** | 继承宿主机（无任何网络限制） |
| 主机 Skill 执行 | **完全开放** | 继承宿主机 |

### 3.3 防注入/防逃逸机制

| 机制 | 文件 | 行号 | 作用 |
|------|------|------|------|
| **VitalGuard** | `src/core/vital_guard.py` | 37–47 | 锁定 `src/`、`config/.env`、`constitution.md` 等关键路径 |
| **VitalGuard BLOCK_PATTERNS** | `src/core/vital_guard.py` | 54–59 | 拦截 `rm -rf /`、`mkfs`、`dd of=/dev/` |
| **ShellExecutor 危险命令检测** | `src/tools/shell_executor.py` | 54–68 | fork bomb、破坏性删除、磁盘格式化、关机/重启 |
| **ShellExecutor 交互命令检测** | `src/tools/shell_executor.py` | 62–68 | vim/nano/top/htop/less/man/tail -f |
| **ShellExecutor 写命令检测** | `src/tools/shell_executor.py` | 69–85 | rm/mv/cp/chmod/chown 等 + 受保护路径 |
| **ShellExecutor 受保护路径** | `src/tools/shell_executor.py` | 44–53 | `/etc`、`/usr`、`/boot`、`/bin`、`/sbin`、`/lib`、`/root` |
| **ShellPolicy** | `src/core/shell_policy.py` | 1–659 | 命令意图分析、写入约束、恢复策略 |
| **workspace_tools 路径校验** | `src/tools/workspace_tools.py` | 15–22 | `_resolve_safe()` 确保路径不逃逸 `data/agent_workspace/` |
| **MemoryGuard** | `src/guards/memory_guard.py` | — | 扫描 prompt injection、凭据泄露、身份篡改 |
| **Docker `--cap-drop=ALL`** | `src/tools/shell_executor.py` | 205 | 移除所有 Linux capabilities（仅 Shell Docker 后端） |
| **Docker `--user sandboxuser`** | `src/skills/skill_executor.py` | 82 | 非 root 用户运行（仅 Skill 沙盒） |
| **Docker `--read-only`** | `src/tools/shell_executor.py` | 204 | 只读根文件系统（仅 Shell Docker 后端） |

**注意：** Docker 后端绕过本地安全检查：
```python
# shell_executor.py:283-285
# Docker 后端：跳过本地安全检查（容器就是安全边界）
if _SHELL_BACKEND == "docker":
    return await _execute_docker(command)
```

### 3.4 与 Permission Model 的交互

```
用户消息 → AuthorityGate.identify() → AuthLevel
                                         │
                                         ▼
工具调用 → AuthorityGate.authorize(tool_name, auth_level)
             │
             ├── execute_shell:    OWNER (3) 必需
             ├── run_python_code:  OWNER (3) 必需
             ├── run_skill:        OWNER (3) 必需
             ├── create_skill:     OWNER (3) 必需
             ├── promote_skill:    OWNER (3) 必需
             └── (未注册工具):      OWNER (3) 默认
```

**权限矩阵：**

| 工具 | IGNORE(0) | GUEST(1) | TRUSTED(2) | OWNER(3) |
|------|-----------|----------|------------|----------|
| `execute_shell` | ✗ | ✗ | ✗ | ✓ |
| `run_python_code` | ✗ | ✗ | ✗ | ✓ |
| `run_skill` | ✗ | ✗ | ✗ | ✓ |
| `create_skill` | ✗ | ✗ | ✗ | ✓ |
| `edit_skill` | ✗ | ✗ | ✗ | ✓ |
| `promote_skill` | ✗ | ✗ | ✗ | ✓ |
| `delete_skill` | ✗ | ✗ | ✗ | ✓ |
| `list_skills` | ✗ | ✗ | ✗ | ✓ |

所有执行类工具均要求 **OWNER** 级别，即只有 Kevin 可以触发代码执行。

Desktop 连接默认 OWNER（`DESKTOP_DEFAULT_OWNER=true`），QQ 未知用户为 GUEST。

---

## 4. 依赖与配置

### 4.1 外部依赖

| 依赖 | 用途 | 必需？ |
|------|------|--------|
| **Docker** | Skill 沙盒 + Shell Docker 后端 | Skill 沙盒必需；Shell 可用 local 后端替代 |
| `python:3.12-slim` 镜像 | Skill 沙盒基础镜像 | Skill 沙盒必需 |
| `lapwing-sandbox` 镜像 | 从 `docker/sandbox/Dockerfile` 构建 | 需手动 `docker build` |

**未使用：** nsjail / firejail / bubblewrap / seccomp / AppArmor / SELinux

### 4.2 Docker 构建文件

| 文件 | 路径 | 说明 |
|------|------|------|
| Dockerfile | `docker/sandbox/Dockerfile` | 16 行，基于 `python:3.12-slim` |
| runner_template.py | `docker/sandbox/runner_template.py` | 17 行，Skill 执行入口模板 |

**未找到：** `docker-compose.yml`、`docker-compose.yaml`、`.dockerignore`

### 4.3 配置文件

#### `config.toml`（主配置）

```toml
[shell]
enabled = true
allow_sudo = true           # 注意：代码默认 false
timeout = 30
default_cwd = "/home/kevin/lapwing"
max_output_chars = 4000
backend = "local"           # "local" | "docker"
docker_image = "lapwing-sandbox:latest"
docker_workspace = "/home/lapwing/workspace"

[skill]
enabled = true              # 注意：代码默认 false
sandbox_image = "lapwing-sandbox"
sandbox_timeout = 30
```

#### `src/config/settings.py`（Pydantic 模型）

```python
class ShellConfig(BaseModel):       # 行 318–327
    enabled: bool = True
    allow_sudo: bool = False
    timeout: int = 30
    default_cwd: str = ""
    max_output_chars: int = 4000
    backend: str = "local"
    docker_image: str = "lapwing-sandbox:latest"
    docker_workspace: str = "/home/lapwing/workspace"

class SkillConfig(BaseModel):       # 行 440–443
    enabled: bool = False
    sandbox_image: str = "lapwing-sandbox"
    sandbox_timeout: int = 30
```

### 4.4 环境变量

| 变量名 | TOML 路径 | 默认值 | 说明 |
|--------|-----------|--------|------|
| `SHELL_ENABLED` | `shell.enabled` | `true` | 启用/禁用 Shell |
| `SHELL_ALLOW_SUDO` | `shell.allow_sudo` | `false` | 允许 sudo |
| `SHELL_TIMEOUT` | `shell.timeout` | `30` | Shell 超时秒数 |
| `SHELL_DEFAULT_CWD` | `shell.default_cwd` | `""` | 默认工作目录 |
| `SHELL_MAX_OUTPUT_CHARS` | `shell.max_output_chars` | `4000` | 输出截断 |
| `SHELL_BACKEND` | `shell.backend` | `"local"` | `local` / `docker` |
| `SHELL_DOCKER_IMAGE` | `shell.docker_image` | `"lapwing-sandbox:latest"` | Docker 镜像名 |
| `SHELL_DOCKER_WORKSPACE` | `shell.docker_workspace` | `"/home/lapwing/workspace"` | Docker 工作区挂载路径 |
| `SKILL_SYSTEM_ENABLED` | `skill.enabled` | `false` | 启用/禁用技能系统 |
| `SKILL_SANDBOX_IMAGE` | `skill.sandbox_image` | `"lapwing-sandbox"` | Skill 沙盒镜像名 |
| `SKILL_SANDBOX_TIMEOUT` | `skill.sandbox_timeout` | `30` | Skill 执行超时秒数 |

---

## 5. 现存问题与缺失

### 5.1 TODO / FIXME / HACK 注释

在所有沙盒相关文件中 **未找到** 任何 TODO、FIXME、HACK 注释：
- `src/skills/skill_executor.py` — 无
- `src/tools/shell_executor.py` — 无
- `src/tools/code_runner.py` — 无
- `src/tools/workspace_tools.py` — 无
- `docker/sandbox/Dockerfile` — 无
- `docker/sandbox/runner_template.py` — 无

### 5.2 未实现的接口/桩代码

| 发现 | 文件 | 说明 |
|------|------|------|
| `runner_template.py` 未被引用 | `docker/sandbox/runner_template.py` | 文件存在但 `SkillExecutor._build_runner()` 是动态生成 runner 代码，并不读取此模板文件。此文件实际是文档/参考用途，不参与运行时 |
| 依赖安装在 sandbox 中失败 | `skill_executor.py:183–188` | sandbox 使用 `--network none`，pip install 无法联网。依赖声明接口存在但不可用 |
| `_noop_shell` 名义上的 shell | `src/agents/base.py:245` | Agent 的 shell 被替换为空操作，但 profile 仍声明 `execute_shell`，可能造成 LLM 混淆 |

### 5.3 与 Skill Growth Model 设计文档的对比

设计文档：`docs/superpowers/plans/2026-04-20-skill-growth-model.md`

| 设计文档规格 | 实际实现 | 状态 |
|-------------|----------|------|
| SkillStore YAML + md 存储 | ✓ 已实现 | 一致 |
| SkillExecutor sandbox/host 双路径 | ✓ 已实现 | 一致 |
| Docker `--network none` | ✓ 已实现 | 一致 |
| Docker `--user sandboxuser` | ✓ 已实现 | 一致 |
| Docker `-v {tmp}:/workspace:ro` | ✓ 已实现 | 一致 |
| 6 个 LLM-facing 技能工具 | ✓ 已实现 | 一致 |
| Stable skill 自动注册为 ToolSpec | ✓ 已实现 | 一致 |
| `SKILL_SYSTEM_ENABLED` feature flag | ✓ 已实现（默认 false） | 一致 |
| Docker `--memory` / `--cpus` 资源限制 | **✗ 未实现** | **差异**：Skill 沙盒无内存/CPU 限制 |
| 依赖动态安装 | 部分实现 | **差异**：`--network none` 下 pip 无法联网 |

### 5.4 安全缺陷清单

| 严重度 | 问题 | 文件 | 行号 |
|--------|------|------|------|
| **高** | Skill 沙盒缺少 `--memory` 和 `--cpus` 限制，恶意/错误代码可能耗尽宿主机资源 | `skill_executor.py` | 78–85 |
| **高** | 主机 Skill 执行（stable maturity）无文件系统隔离，可访问宿主机任意文件 | `skill_executor.py` | 128–178 |
| **中** | CodeRunner 无文件系统隔离，代码可通过绝对路径读写宿主机文件 | `code_runner.py` | 41–46 |
| **中** | Shell Docker 后端使用 `--network=host`，容器内可访问所有网络接口 | `shell_executor.py` | 206 |
| **中** | `--network none` 下声明的技能依赖（`dependencies`）无法安装，静默失败 | `skill_executor.py` | 183–188 |
| **低** | Skill 沙盒未设置 `--cap-drop=ALL`（Shell Docker 后端有设置） | `skill_executor.py` | 78–85 |
| **低** | `runner_template.py` 文件未被实际使用，可能造成维护混淆 | `docker/sandbox/runner_template.py` | — |
| **低** | `config.toml` 中 `allow_sudo = true` 与代码默认值 `false` 不一致 | `config.toml` | 60 |

---

## 6. 完整执行边界对比

| 组件 | 隔离级别 | 超时 | 内存限制 | CPU 限制 | 网络 | 文件系统 | 用户 |
|------|---------|------|---------|---------|------|---------|------|
| Skill (Docker sandbox) | ✅ 容器 | 30s | ❌ 无 | ❌ 无 | ❌ 禁止 | 只读挂载 | sandboxuser |
| Skill (Host) | ❌ 临时目录 | 30s | ❌ 无 | ❌ 无 | ✅ 完全 | 可读写宿主机 | 当前用户 |
| Shell (Docker) | ✅ 容器 | 30s | 512MB | 1.0 核 | ✅ host | 只读根+tmpfs | root* |
| Shell (Local) | ❌ 正则策略 | 30s | ❌ 无 | ❌ 无 | ✅ 完全 | 受保护路径拦截 | 当前用户 |
| CodeRunner | ❌ 临时目录 | 10s | ❌ 无 | ❌ 无 | ✅ 完全 | 可读写宿主机 | 当前用户 |
| Agent workspace | ❌ 路径校验 | 600s | ❌ 无 | ❌ 无 | N/A | 仅 agent_workspace/ | 当前用户 |

> \* Shell Docker 后端未指定 `--user`，默认以容器内 root 运行（但 `--read-only` + `--cap-drop=ALL` 限制了实际能力）。

---

## 7. 建议改进方向

1. **为 Skill Docker 沙盒添加资源限制**：`--memory=256m --cpus=0.5`（与 Shell Docker 后端对齐）
2. **为 Skill Docker 沙盒添加 `--cap-drop=ALL`**
3. **解决依赖安装矛盾**：在构建镜像时预装常用包，或提供带网络的 `--network` 模式用于依赖安装阶段
4. **CodeRunner 考虑 Docker 化**：当前临时目录隔离不足以防止恶意代码访问宿主机
5. **清理或标注 `runner_template.py`**：明确其用途（文档参考 vs 运行时模板）
6. **统一 `config.toml` 与代码默认值**：`allow_sudo` 和 `skill.enabled` 存在不一致
