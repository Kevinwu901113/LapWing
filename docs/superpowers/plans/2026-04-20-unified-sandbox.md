# Unified Execution Sandbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace three independent execution isolation mechanisms (SkillExecutor, ShellExecutor, CodeRunner) with a unified `ExecutionSandbox` module using three security tiers (STRICT/STANDARD/PRIVILEGED), adding credential sanitization and output security along the way.

**Architecture:** Three-phase incremental approach. Phase 1 patches the worst security holes in existing code. Phase 2 adds two new cross-cutting modules (credential sanitizer, output security). Phase 3 extracts the unified `ExecutionSandbox` abstraction and migrates all callers. Each phase produces working, tested code independently.

**Tech Stack:** Python 3.12, Docker, pytest, asyncio

**Design document:** `docs/sandbox_audit_report.md`

---

## File Structure

### New files
| File | Responsibility |
|------|---------------|
| `src/core/credential_sanitizer.py` | Env-var whitelist + secret-pattern redaction for execution output |
| `src/core/execution_sandbox.py` | Unified Docker/local execution with three tiers |
| `tests/core/test_credential_sanitizer.py` | Tests for credential sanitizer |
| `tests/core/test_execution_sandbox.py` | Tests for unified sandbox |
| `scripts/setup_docker_network.sh` | One-time Docker bridge network creation |

### Modified files
| File | Changes |
|------|---------|
| `src/skills/skill_executor.py` | Phase 1: add Docker resource flags. Phase 3: delegate to ExecutionSandbox |
| `src/tools/shell_executor.py` | Phase 1: fix network + restore VitalGuard. Phase 3: delegate to ExecutionSandbox |
| `src/tools/code_runner.py` | Phase 2: sanitize env. Phase 3: delegate to ExecutionSandbox |
| `docker/sandbox/Dockerfile` | Phase 2: security hardening |
| `src/config/settings.py` | Phase 3: add `SandboxTierConfig` + `SandboxConfig` models |
| `config/settings.py` | Phase 3: expose sandbox settings |
| `config.toml` | Phase 3: add `[sandbox]` section |
| `config.example.toml` | Phase 1: align defaults. Phase 3: add `[sandbox]` section |
| `tests/skills/test_skill_executor.py` | New tests for Docker flags |
| `tests/tools/test_shell_executor.py` | New tests for VitalGuard in Docker mode |
| `tests/tools/test_code_runner.py` | New tests for env sanitization |

### Deleted files
| File | Reason |
|------|--------|
| `docker/sandbox/runner_template.py` | Dead code; SkillExecutor generates runner dynamically via `_build_runner()` |

---

## Phase 1: Urgent Security Fixes

### Task 1: SkillExecutor Docker resource limits

**Files:**
- Modify: `src/skills/skill_executor.py:78-85`
- Test: `tests/skills/test_skill_executor.py`

- [ ] **Step 1: Write failing test for Docker flags**

```python
# In tests/skills/test_skill_executor.py — add new test class

class TestSandboxDockerFlags:
    async def test_sandbox_docker_command_includes_resource_limits(self, executor):
        """Verify the Docker command includes --memory, --cpus, --cap-drop."""
        from unittest.mock import patch, AsyncMock

        captured_cmd = []

        async def fake_create_subprocess_exec(*args, **kwargs):
            captured_cmd.extend(args)
            mock_proc = AsyncMock()
            mock_proc.communicate.return_value = (b'{"ok": true}', b'')
            mock_proc.returncode = 0
            return mock_proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec):
            await executor._run_in_sandbox(
                'def run():\n    return {"ok": True}',
                {},
                [],
                timeout=30,
            )

        cmd_str = " ".join(captured_cmd)
        assert "--memory" in cmd_str, "Missing --memory flag"
        assert "--cpus" in cmd_str, "Missing --cpus flag"
        assert "--cap-drop" in cmd_str, "Missing --cap-drop flag"

    async def test_sandbox_docker_command_memory_256m(self, executor):
        """Verify memory limit is 256m for sandbox mode."""
        from unittest.mock import patch, AsyncMock

        captured_cmd = []

        async def fake_create_subprocess_exec(*args, **kwargs):
            captured_cmd.extend(args)
            mock_proc = AsyncMock()
            mock_proc.communicate.return_value = (b'{}', b'')
            mock_proc.returncode = 0
            return mock_proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec):
            await executor._run_in_sandbox('def run():\n    return {}', {}, [], timeout=30)

        # Find the value after --memory
        for i, arg in enumerate(captured_cmd):
            if arg == "--memory":
                assert captured_cmd[i + 1] == "256m"
                break
        else:
            pytest.fail("--memory flag not found")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/skills/test_skill_executor.py::TestSandboxDockerFlags -v`
Expected: FAIL — `--memory` / `--cpus` / `--cap-drop` not in current Docker command

- [ ] **Step 3: Add resource limits to SkillExecutor Docker command**

In `src/skills/skill_executor.py`, replace the `cmd` list (lines 78-85):

```python
            cmd = [
                "docker", "run", "--rm",
                "--network", "none",
                "--cap-drop=ALL",
                "--memory", "256m",
                "--cpus", "0.5",
                "-v", f"{tmp_dir}:/workspace:ro",
                "--user", "sandboxuser",
                self._sandbox_image,
                "python3", "/workspace/runner.py",
            ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/skills/test_skill_executor.py::TestSandboxDockerFlags -v`
Expected: PASS

- [ ] **Step 5: Run full skill executor test suite**

Run: `pytest tests/skills/test_skill_executor.py -v`
Expected: All existing tests still pass

- [ ] **Step 6: Commit**

```bash
git add src/skills/skill_executor.py tests/skills/test_skill_executor.py
git commit -m "fix(sandbox): add --memory, --cpus, --cap-drop ALL to SkillExecutor Docker"
```

---

### Task 2: ShellExecutor Docker backend — restore safety checks + fix network

`_blocked_reason()` is ShellExecutor's own safety-check function (dangerous commands, interactive commands, protected paths). The Docker backend currently skips it entirely, assuming the container is sufficient isolation. This fix moves the check before the Docker dispatch so both backends share the same command-level safety net.

**Files:**
- Modify: `src/tools/shell_executor.py:200-214,275-285`
- Test: `tests/tools/test_shell_executor.py`

- [ ] **Step 1: Write failing test — safety checks block dangerous commands in Docker mode**

```python
# In tests/tools/test_shell_executor.py — add new tests

@pytest.mark.asyncio
async def test_docker_backend_blocks_dangerous_command(monkeypatch, isolated_shell_log):
    """Docker backend must still block dangerous commands (fork bomb, rm -rf /)."""
    monkeypatch.setattr(shell_executor, "_SHELL_BACKEND", "docker")

    result = await shell_executor.execute("rm -rf /")

    assert result.blocked is True
    assert "删除根目录" in result.reason


@pytest.mark.asyncio
async def test_docker_backend_blocks_interactive_command(monkeypatch, isolated_shell_log):
    """Docker backend must still block interactive commands."""
    monkeypatch.setattr(shell_executor, "_SHELL_BACKEND", "docker")

    result = await shell_executor.execute("vim README.md")

    assert result.blocked is True
    assert "交互式编辑器" in result.reason
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/tools/test_shell_executor.py::test_docker_backend_blocks_dangerous_command -v`
Expected: FAIL — Docker backend currently skips `_blocked_reason()` and calls `_execute_docker()` directly

- [ ] **Step 3: Move safety checks before Docker dispatch**

In `src/tools/shell_executor.py`, modify the `execute()` function (starting at line 275). Move `_blocked_reason()` above the Docker backend check:

```python
async def execute(command: str) -> ShellResult:
    """执行 shell 命令并返回真实结果。"""
    start = time.perf_counter()
    if not SHELL_ENABLED:
        result = _build_blocked_result("本地 shell 执行已禁用。")
        await _log_execution(command, result)
        return result

    reason = _blocked_reason(command)
    if reason is not None:
        logger.warning(f"[shell] 拒绝执行命令: {command!r} — {reason}")
        result = _build_blocked_result(reason)
        await _log_execution(command, result)
        return result

    if _SHELL_BACKEND == "docker":
        return await _execute_docker(command)

    try:
        proc = await asyncio.create_subprocess_exec(
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/tools/test_shell_executor.py::test_docker_backend_blocks_dangerous_command tests/tools/test_shell_executor.py::test_docker_backend_blocks_interactive_command -v`
Expected: PASS

- [ ] **Step 5: Write test — Docker backend uses bridge network, not host**

```python
@pytest.mark.asyncio
async def test_docker_backend_uses_bridge_network(monkeypatch, isolated_shell_log):
    """Docker backend must NOT use --network=host."""
    monkeypatch.setattr(shell_executor, "_SHELL_BACKEND", "docker")

    captured_cmd = []

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured_cmd.extend(args)
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b'output', b'')
        mock_proc.returncode = 0
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec):
        await shell_executor.execute("echo hello")

    cmd_str = " ".join(captured_cmd)
    assert "--network=host" not in cmd_str, "Must not use --network=host"
    assert "--network" in cmd_str, "Must specify a network"
```

Add import at top of test file:

```python
from unittest.mock import patch, AsyncMock
```

- [ ] **Step 6: Run test to verify it fails**

Run: `pytest tests/tools/test_shell_executor.py::test_docker_backend_uses_bridge_network -v`
Expected: FAIL — current code uses `--network=host`

- [ ] **Step 7: Change Docker network from host to bridge**

In `src/tools/shell_executor.py`, update `_execute_docker()` (line 206):

```python
        "--network=lapwing-sandbox",                  # 隔离 bridge 网络
```

- [ ] **Step 8: Run test to verify it passes**

Run: `pytest tests/tools/test_shell_executor.py::test_docker_backend_uses_bridge_network -v`
Expected: PASS

- [ ] **Step 9: Run full shell executor test suite**

Run: `pytest tests/tools/test_shell_executor.py -v`
Expected: All tests pass

- [ ] **Step 10: Commit**

```bash
git add src/tools/shell_executor.py tests/tools/test_shell_executor.py
git commit -m "fix(sandbox): restore VitalGuard checks in Docker backend + use bridge network"
```

---

### Task 3: Docker network setup script

**Files:**
- Create: `scripts/setup_docker_network.sh`

- [ ] **Step 1: Create the network setup script**

```bash
#!/usr/bin/env bash
set -euo pipefail

NETWORK_NAME="lapwing-sandbox"

if docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
    echo "[OK] Network '$NETWORK_NAME' already exists."
else
    docker network create \
        --driver bridge \
        --opt com.docker.network.bridge.enable_icc=false \
        "$NETWORK_NAME"
    echo "[OK] Created network '$NETWORK_NAME' (bridge, ICC disabled)."
fi
```

- [ ] **Step 2: Make it executable and commit**

```bash
chmod +x scripts/setup_docker_network.sh
git add scripts/setup_docker_network.sh
git commit -m "feat(sandbox): add Docker bridge network setup script"
```

---

### Task 4: Config defaults alignment

**Files:**
- Modify: `config.example.toml:59-60,164`

- [ ] **Step 1: Verify current divergence**

Production `config.toml` has `allow_sudo = true` and `skill.enabled = true`.
Example `config.example.toml` has `allow_sudo = false` and `skill.enabled = false`.

The example file should be the safe defaults — `allow_sudo = false` and `skill.enabled = false` are correct there. No changes needed to `config.example.toml`.

The code defaults in `src/config/settings.py` should also be safe:
- `ShellConfig.allow_sudo: bool = False` — correct
- `SkillConfig.enabled: bool = False` — correct

**This is already correct.** The example and code defaults are safe; production config intentionally overrides. Mark as done.

- [ ] **Step 2: Commit (skip — no changes needed)**

---

## Phase 2: Credential Sanitization + Output Security

### Task 5: Credential sanitizer — env whitelist

**Files:**
- Create: `src/core/credential_sanitizer.py`
- Create: `tests/core/test_credential_sanitizer.py`

- [ ] **Step 1: Write failing tests for env sanitization**

```python
# tests/core/test_credential_sanitizer.py

import pytest
from src.core.credential_sanitizer import sanitize_env


class TestSanitizeEnv:
    def test_passes_safe_vars(self):
        env = {"PATH": "/usr/bin", "HOME": "/home/user", "LANG": "en_US.UTF-8"}
        result = sanitize_env(env)
        assert result == env

    def test_strips_api_keys(self):
        env = {
            "PATH": "/usr/bin",
            "LLM_API_KEY": "sk-secret",
            "TAVILY_API_KEY": "tvly-xxx",
            "NIM_API_KEY": "nvapi-xxx",
        }
        result = sanitize_env(env)
        assert "PATH" in result
        assert "LLM_API_KEY" not in result
        assert "TAVILY_API_KEY" not in result
        assert "NIM_API_KEY" not in result

    def test_strips_credential_patterns(self):
        env = {
            "PATH": "/usr/bin",
            "MY_PASSWORD": "hunter2",
            "DB_TOKEN": "tok-abc",
            "AWS_SECRET_ACCESS_KEY": "wJalrXUtnFEMI",
        }
        result = sanitize_env(env)
        assert "MY_PASSWORD" not in result
        assert "DB_TOKEN" not in result
        assert "AWS_SECRET_ACCESS_KEY" not in result

    def test_passes_python_vars(self):
        env = {
            "PATH": "/usr/bin",
            "PYTHONPATH": "/app",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        result = sanitize_env(env)
        assert "PYTHONPATH" in result
        assert "PYTHONDONTWRITEBYTECODE" in result

    def test_passes_tz(self):
        env = {"PATH": "/usr/bin", "TZ": "Asia/Shanghai"}
        result = sanitize_env(env)
        assert result["TZ"] == "Asia/Shanghai"

    def test_strips_proxy_vars_when_strict(self):
        env = {
            "PATH": "/usr/bin",
            "http_proxy": "http://proxy:8080",
            "HTTPS_PROXY": "http://proxy:8080",
        }
        result = sanitize_env(env, allow_network=False)
        assert "http_proxy" not in result
        assert "HTTPS_PROXY" not in result

    def test_passes_proxy_vars_when_network_allowed(self):
        env = {
            "PATH": "/usr/bin",
            "http_proxy": "http://proxy:8080",
        }
        result = sanitize_env(env, allow_network=True)
        assert "http_proxy" in result

    def test_empty_env(self):
        assert sanitize_env({}) == {}

    def test_none_env_returns_safe_default(self):
        result = sanitize_env(None)
        assert isinstance(result, dict)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/core/test_credential_sanitizer.py::TestSanitizeEnv -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement credential sanitizer**

```python
# src/core/credential_sanitizer.py
"""环境变量白名单清洗 + 执行输出凭证遮蔽。"""

from __future__ import annotations

import re

_ENV_WHITELIST_PREFIXES = (
    "PATH", "HOME", "USER", "LOGNAME", "SHELL",
    "LANG", "LC_", "TZ", "TERM",
    "PYTHON", "VIRTUAL_ENV",
    "TMPDIR", "TEMP", "TMP",
    "COLUMNS", "LINES",
    "XDG_",
)

_ENV_WHITELIST_EXACT = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "SHELL",
    "LANG", "TZ", "TERM", "TMPDIR", "TEMP", "TMP",
    "VIRTUAL_ENV", "COLUMNS", "LINES",
    "SHLVL", "PWD", "OLDPWD", "HOSTNAME",
})

_NETWORK_VARS = frozenset({
    "http_proxy", "HTTP_PROXY",
    "https_proxy", "HTTPS_PROXY",
    "no_proxy", "NO_PROXY",
    "ALL_PROXY", "all_proxy",
})

_CREDENTIAL_PATTERNS = (
    re.compile(r"(?i)api[_-]?key"),
    re.compile(r"(?i)secret"),
    re.compile(r"(?i)password"),
    re.compile(r"(?i)passwd"),
    re.compile(r"(?i)token"),
    re.compile(r"(?i)credential"),
    re.compile(r"(?i)auth"),
    re.compile(r"(?i)private[_-]?key"),
)


def _is_safe_var(name: str) -> bool:
    if name in _ENV_WHITELIST_EXACT:
        return True
    return any(name.startswith(p) for p in _ENV_WHITELIST_PREFIXES)


def _looks_like_credential(name: str) -> bool:
    return any(p.search(name) for p in _CREDENTIAL_PATTERNS)


def sanitize_env(
    env: dict[str, str] | None,
    *,
    allow_network: bool = False,
) -> dict[str, str]:
    """Return a copy of *env* with only safe variables.

    Whitelist + credential-pattern double-check:
    even if a var passes the whitelist, it is dropped if the name
    matches a credential pattern (belt-and-suspenders).
    """
    if env is None:
        return {}
    result: dict[str, str] = {}
    for name, value in env.items():
        if name in _NETWORK_VARS:
            if allow_network:
                result[name] = value
            continue
        if not _is_safe_var(name):
            continue
        if _looks_like_credential(name):
            continue
        result[name] = value
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/core/test_credential_sanitizer.py::TestSanitizeEnv -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/credential_sanitizer.py tests/core/test_credential_sanitizer.py
git commit -m "feat(sandbox): add credential sanitizer with env-var whitelist"
```

---

### Task 6: Credential sanitizer — output secret scanning

**Files:**
- Modify: `src/core/credential_sanitizer.py`
- Modify: `tests/core/test_credential_sanitizer.py`

- [ ] **Step 1: Write failing tests for output secret scanning**

```python
# Append to tests/core/test_credential_sanitizer.py

from src.core.credential_sanitizer import redact_secrets


class TestRedactSecrets:
    def test_redacts_github_pat(self):
        text = "token is ghp_ABCDEFghijklmnopqrstuvwxyz012345"
        result = redact_secrets(text)
        assert "ghp_" not in result
        assert "[REDACTED]" in result

    def test_redacts_aws_key(self):
        text = "key=AKIAIOSFODNN7EXAMPLE"
        result = redact_secrets(text)
        assert "AKIAIOSFODNN7EXAMPLE" not in result

    def test_redacts_jwt(self):
        text = "auth: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        result = redact_secrets(text)
        assert "eyJhbGciOi" not in result

    def test_redacts_private_key_block(self):
        text = "key:\n-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA\n-----END RSA PRIVATE KEY-----\ndone"
        result = redact_secrets(text)
        assert "MIIEpAIBAAKCAQEA" not in result

    def test_redacts_generic_sk_prefix(self):
        text = "api_key = sk-proj-abcdef1234567890abcdef"
        result = redact_secrets(text)
        assert "sk-proj-" not in result

    def test_preserves_normal_text(self):
        text = "Hello world, this is a normal output\nwith line 2"
        assert redact_secrets(text) == text

    def test_empty_input(self):
        assert redact_secrets("") == ""

    def test_redacts_nvapi_key(self):
        text = "key=nvapi-abcdef1234567890"
        result = redact_secrets(text)
        assert "nvapi-" not in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/core/test_credential_sanitizer.py::TestRedactSecrets -v`
Expected: FAIL — `redact_secrets` not defined

- [ ] **Step 3: Implement output secret scanning**

Append to `src/core/credential_sanitizer.py`:

```python
_SECRET_PATTERNS: list[tuple[re.Pattern, str]] = [
    # GitHub PAT (classic + fine-grained)
    (re.compile(r"ghp_[A-Za-z0-9]{36,}"), "[REDACTED:github_pat]"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "[REDACTED:github_pat]"),
    # AWS Access Key
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED:aws_key]"),
    # JWT
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "[REDACTED:jwt]"),
    # PEM private key blocks
    (re.compile(r"-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |DSA )?PRIVATE KEY-----"), "[REDACTED:private_key]"),
    # Generic sk- prefix (OpenAI, MiniMax, etc.)
    (re.compile(r"sk-(?:proj-)?[A-Za-z0-9]{20,}"), "[REDACTED:api_key]"),
    # NVIDIA NIM
    (re.compile(r"nvapi-[A-Za-z0-9_-]{20,}"), "[REDACTED:nvapi_key]"),
    # Tavily
    (re.compile(r"tvly-[A-Za-z0-9]{20,}"), "[REDACTED:tavily_key]"),
    # Generic bearer token (long hex/base64)
    (re.compile(r"(?i)(?:bearer|token|authorization)[:\s]+[A-Za-z0-9_\-\.]{40,}"), "[REDACTED:bearer]"),
]


def redact_secrets(text: str) -> str:
    """Scan text for known secret patterns and replace with [REDACTED]."""
    if not text:
        return text
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/core/test_credential_sanitizer.py::TestRedactSecrets -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/credential_sanitizer.py tests/core/test_credential_sanitizer.py
git commit -m "feat(sandbox): add output secret scanning to credential sanitizer"
```

---

### Task 7: Head+tail output truncation

**Files:**
- Modify: `src/core/credential_sanitizer.py`
- Modify: `tests/core/test_credential_sanitizer.py`

- [ ] **Step 1: Write failing tests for head+tail truncation**

```python
# Append to tests/core/test_credential_sanitizer.py

from src.core.credential_sanitizer import truncate_head_tail


class TestTruncateHeadTail:
    def test_short_text_unchanged(self):
        text = "short output"
        assert truncate_head_tail(text, max_chars=1000) == text

    def test_long_text_truncated(self):
        head = "HEAD\n" * 100   # 500 chars
        middle = "M" * 5000
        tail = "\nTAIL" * 100  # 500 chars
        text = head + middle + tail
        result = truncate_head_tail(text, max_chars=2000)
        assert len(result) <= 2200  # allow small overhead for marker
        assert "HEAD" in result
        assert "TAIL" in result
        assert "[truncated" in result.lower() or "..." in result

    def test_tail_bias(self):
        """Tail portion should be larger than head (results usually at end)."""
        lines = [f"line-{i:04d}" for i in range(1000)]
        text = "\n".join(lines)
        result = truncate_head_tail(text, max_chars=500)
        # Last line should be present (tail preserved)
        assert "line-0999" in result
        # First line should be present (head preserved)
        assert "line-0000" in result

    def test_empty_text(self):
        assert truncate_head_tail("", max_chars=100) == ""

    def test_exact_limit(self):
        text = "x" * 1000
        assert truncate_head_tail(text, max_chars=1000) == text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/core/test_credential_sanitizer.py::TestTruncateHeadTail -v`
Expected: FAIL — `truncate_head_tail` not defined

- [ ] **Step 3: Implement head+tail truncation**

Append to `src/core/credential_sanitizer.py`:

```python
def truncate_head_tail(
    text: str,
    max_chars: int,
    *,
    head_ratio: float = 0.3,
) -> str:
    """Truncate keeping head and tail, with tail-bias.

    Args:
        text: The text to truncate.
        max_chars: Maximum character count.
        head_ratio: Fraction of max_chars for the head portion (default 0.3).
                    Remaining goes to tail.
    """
    if not text or len(text) <= max_chars:
        return text
    head_chars = int(max_chars * head_ratio)
    tail_chars = max_chars - head_chars
    omitted = len(text) - head_chars - tail_chars
    marker = f"\n\n... [{omitted} chars truncated] ...\n\n"
    return text[:head_chars] + marker + text[-tail_chars:]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/core/test_credential_sanitizer.py::TestTruncateHeadTail -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/credential_sanitizer.py tests/core/test_credential_sanitizer.py
git commit -m "feat(sandbox): add head+tail output truncation"
```

---

### Task 8: Integrate credential sanitizer into CodeRunner

**Files:**
- Modify: `src/tools/code_runner.py:40-46,58-59`
- Modify: `tests/tools/test_code_runner.py`

CodeRunner is the highest-priority target: it runs local subprocesses that inherit the parent's full environment, including all API keys.

> **Note:** Task 16 (Phase 3) will rewrite CodeRunner to use `ExecutionSandbox.run_local()`, which supersedes the code changes here. This task is intentional: it closes the security gap immediately so Phase 2 can be deployed independently of Phase 3. The tests written here survive the rewrite and validate the final behavior.

- [ ] **Step 1: Write failing test — CodeRunner strips env vars**

```python
# Append to tests/tools/test_code_runner.py

@pytest.mark.asyncio
async def test_env_vars_sanitized():
    """Subprocess must not see parent's API keys."""
    import os
    os.environ["LLM_API_KEY"] = "sk-test-secret-key-for-testing"
    try:
        result = await run_python(
            "import os; print(os.environ.get('LLM_API_KEY', 'NOT_FOUND'))"
        )
        assert result.exit_code == 0
        assert "NOT_FOUND" in result.stdout
        assert "sk-test" not in result.stdout
    finally:
        del os.environ["LLM_API_KEY"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/tools/test_code_runner.py::test_env_vars_sanitized -v`
Expected: FAIL — currently inherits parent env

- [ ] **Step 3: Add env sanitization to CodeRunner**

In `src/tools/code_runner.py`, add import and modify subprocess call:

```python
# Add import at top
from src.core.credential_sanitizer import sanitize_env

# In run_python(), modify create_subprocess_exec call (around line 41):
        clean_env = sanitize_env(dict(os.environ))
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(script_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=tmp_dir,
            env=clean_env,
        )
```

Also add `import os` to the imports if not already there.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/tools/test_code_runner.py::test_env_vars_sanitized -v`
Expected: PASS

- [ ] **Step 5: Write test — CodeRunner output redacts leaked secrets**

```python
@pytest.mark.asyncio
async def test_output_redacts_secrets():
    """If code prints a secret pattern, output should be redacted."""
    result = await run_python('print("my key is ghp_ABCDEFghijklmnopqrstuvwxyz012345xx")')
    assert result.exit_code == 0
    assert "ghp_" not in result.stdout
    assert "REDACTED" in result.stdout
```

- [ ] **Step 6: Run test to verify it fails**

Run: `pytest tests/tools/test_code_runner.py::test_output_redacts_secrets -v`
Expected: FAIL — no output redaction yet

- [ ] **Step 7: Add output redaction + head/tail truncation to CodeRunner**

In `src/tools/code_runner.py`, update the imports and output processing:

```python
# Add import
from src.core.credential_sanitizer import sanitize_env, redact_secrets, truncate_head_tail

# Replace lines 58-59 (the output truncation):
        stdout = redact_secrets(truncate_head_tail(
            raw_out.decode("utf-8", errors="replace"), _MAX_OUTPUT
        ))
        stderr = redact_secrets(truncate_head_tail(
            raw_err.decode("utf-8", errors="replace"), _MAX_OUTPUT
        ))
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/tools/test_code_runner.py -v`
Expected: All tests pass

- [ ] **Step 9: Commit**

```bash
git add src/tools/code_runner.py tests/tools/test_code_runner.py
git commit -m "feat(sandbox): sanitize env + redact output secrets in CodeRunner"
```

---

### Task 9: Integrate credential sanitizer into ShellExecutor + SkillExecutor

**Files:**
- Modify: `src/tools/shell_executor.py:118-122,239-244`
- Modify: `src/skills/skill_executor.py:104-105`

- [ ] **Step 1: Add output redaction + head/tail truncation to ShellExecutor**

In `src/tools/shell_executor.py`, update imports:

```python
from src.core.credential_sanitizer import redact_secrets, truncate_head_tail
```

Replace `_truncate_output()` function (lines 118-122):

```python
def _truncate_output(text: str) -> tuple[str, bool]:
    limit = max(SHELL_MAX_OUTPUT_CHARS, 1)
    if len(text) <= limit:
        return redact_secrets(text), False
    return redact_secrets(truncate_head_tail(text, limit)), True
```

- [ ] **Step 2: Add output redaction to SkillExecutor**

In `src/skills/skill_executor.py`, update imports:

```python
from src.core.credential_sanitizer import redact_secrets, truncate_head_tail
```

Replace output truncation in `_run_in_sandbox()` (lines 104-105):

```python
            stdout = redact_secrets(truncate_head_tail(
                raw_out.decode("utf-8", errors="replace"), _MAX_OUTPUT
            ))
            stderr = redact_secrets(truncate_head_tail(
                raw_err.decode("utf-8", errors="replace"), _MAX_OUTPUT
            ))
```

Do the same in `_run_on_host()` (lines 162-163):

```python
            stdout = redact_secrets(truncate_head_tail(
                raw_out.decode("utf-8", errors="replace"), _MAX_OUTPUT
            ))
            stderr = redact_secrets(truncate_head_tail(
                raw_err.decode("utf-8", errors="replace"), _MAX_OUTPUT
            ))
```

- [ ] **Step 3: Update existing test that breaks due to truncation format change**

The existing `test_execute_truncates_long_output` in `tests/tools/test_shell_executor.py` asserts `result.stdout == "1234567890"`, which assumed simple `[:limit]` truncation. With head+tail truncation, the format is different. Update the test:

```python
@pytest.mark.asyncio
async def test_execute_truncates_long_output(monkeypatch, isolated_shell_log):
    monkeypatch.setattr(shell_executor, "SHELL_MAX_OUTPUT_CHARS", 10)

    result = await shell_executor.execute("printf '123456789012345'")

    assert result.return_code == 0
    assert result.stdout_truncated is True
    # Head+tail truncation preserves start and end
    assert result.stdout.startswith("123")
    assert "truncated" in result.stdout.lower()
```

Similarly, update `test_stdout_truncated` in `tests/tools/test_code_runner.py` (if it asserts exact length):

```python
@pytest.mark.asyncio
async def test_stdout_truncated():
    """超过 2000 字符的 stdout 被截断。"""
    result = await run_python("print('A' * 3000)")
    assert result.exit_code == 0
    # Head+tail truncation adds a marker, so total may slightly exceed limit
    assert len(result.stdout) < 3000
    assert "truncated" in result.stdout.lower()
```

- [ ] **Step 4: Run all executor tests**

Run: `pytest tests/tools/test_shell_executor.py tests/skills/test_skill_executor.py tests/tools/test_code_runner.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add src/tools/shell_executor.py src/skills/skill_executor.py tests/tools/test_shell_executor.py tests/tools/test_code_runner.py
git commit -m "feat(sandbox): integrate secret redaction + head/tail truncation into all executors"
```

---

### Task 10: Dockerfile security hardening

**Files:**
- Modify: `docker/sandbox/Dockerfile`

- [ ] **Step 1: Harden the Dockerfile**

```dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl wget jq \
    && rm -rf /var/lib/apt/lists/* \
    && find / -perm /6000 -type f -exec chmod a-s {} + 2>/dev/null || true

RUN pip install --no-cache-dir \
    requests beautifulsoup4 httpx lxml \
    pandas numpy \
    pyyaml toml \
    matplotlib pillow scipy sympy

RUN useradd -m -s /bin/bash sandboxuser
USER sandboxuser

WORKDIR /workspace
```

Changes from original:
- Remove setuid/setgid bits from all binaries (`find / -perm /6000 ...`)
- Add more common Python packages (matplotlib, pillow, scipy, sympy) to reduce runtime pip install needs

- [ ] **Step 2: Rebuild sandbox image**

```bash
docker build -t lapwing-sandbox:latest docker/sandbox/
```

- [ ] **Step 3: Commit**

```bash
git add docker/sandbox/Dockerfile
git commit -m "fix(sandbox): harden Dockerfile — strip setuid + pre-install common packages"
```

---

### Task 11: Remove dead runner_template.py

**Files:**
- Delete: `docker/sandbox/runner_template.py`

- [ ] **Step 1: Verify it is truly unused**

Run: `grep -r "runner_template" src/ tests/` — expect no results.

- [ ] **Step 2: Delete and commit**

```bash
git rm docker/sandbox/runner_template.py
git commit -m "chore: remove dead runner_template.py (SkillExecutor generates runner dynamically)"
```

---

## Phase 3: Unified ExecutionSandbox

### Task 12: ExecutionSandbox core module

**Files:**
- Create: `src/core/execution_sandbox.py`
- Create: `tests/core/test_execution_sandbox.py`

- [ ] **Step 1: Write failing tests for ExecutionSandbox**

```python
# tests/core/test_execution_sandbox.py

import pytest
from unittest.mock import patch, AsyncMock
from src.core.execution_sandbox import (
    ExecutionSandbox,
    SandboxTier,
    SandboxResult,
)


class TestSandboxTier:
    def test_strict_has_no_network(self):
        sb = ExecutionSandbox()
        flags = sb._build_docker_flags(SandboxTier.STRICT, workspace=None)
        assert "--network" in flags
        idx = flags.index("--network")
        assert flags[idx + 1] == "none"

    def test_standard_uses_bridge(self):
        sb = ExecutionSandbox()
        flags = sb._build_docker_flags(SandboxTier.STANDARD, workspace=None)
        idx = flags.index("--network")
        assert flags[idx + 1] == "lapwing-sandbox"

    def test_privileged_uses_host(self):
        sb = ExecutionSandbox()
        flags = sb._build_docker_flags(SandboxTier.PRIVILEGED, workspace=None)
        assert "--network=host" in flags or (
            "--network" in flags and flags[flags.index("--network") + 1] == "host"
        )

    def test_all_tiers_have_cap_drop(self):
        sb = ExecutionSandbox()
        for tier in SandboxTier:
            flags = sb._build_docker_flags(tier, workspace=None)
            assert "--cap-drop=ALL" in flags

    def test_all_tiers_have_rm(self):
        sb = ExecutionSandbox()
        for tier in SandboxTier:
            flags = sb._build_docker_flags(tier, workspace=None)
            assert "--rm" in flags

    def test_all_tiers_have_user(self):
        sb = ExecutionSandbox()
        for tier in SandboxTier:
            flags = sb._build_docker_flags(tier, workspace=None)
            assert "--user" in flags


class TestSandboxResourceLimits:
    def test_strict_memory_256m(self):
        sb = ExecutionSandbox()
        flags = sb._build_docker_flags(SandboxTier.STRICT, workspace=None)
        idx = flags.index("--memory")
        assert flags[idx + 1] == "256m"

    def test_strict_cpus_half(self):
        sb = ExecutionSandbox()
        flags = sb._build_docker_flags(SandboxTier.STRICT, workspace=None)
        idx = flags.index("--cpus")
        assert flags[idx + 1] == "0.5"

    def test_standard_memory_512m(self):
        sb = ExecutionSandbox()
        flags = sb._build_docker_flags(SandboxTier.STANDARD, workspace=None)
        idx = flags.index("--memory")
        assert flags[idx + 1] == "512m"

    def test_standard_cpus_one(self):
        sb = ExecutionSandbox()
        flags = sb._build_docker_flags(SandboxTier.STANDARD, workspace=None)
        idx = flags.index("--cpus")
        assert flags[idx + 1] == "1.0"

    def test_privileged_memory_1g(self):
        sb = ExecutionSandbox()
        flags = sb._build_docker_flags(SandboxTier.PRIVILEGED, workspace=None)
        idx = flags.index("--memory")
        assert flags[idx + 1] == "1024m"


class TestSandboxWorkspaceMount:
    def test_strict_mounts_readonly(self):
        sb = ExecutionSandbox()
        flags = sb._build_docker_flags(
            SandboxTier.STRICT, workspace="/tmp/work"
        )
        mount_flags = [f for f in flags if "/tmp/work" in f]
        assert any(":ro" in f for f in mount_flags)

    def test_standard_mounts_readwrite(self):
        sb = ExecutionSandbox()
        flags = sb._build_docker_flags(
            SandboxTier.STANDARD, workspace="/tmp/work"
        )
        mount_flags = [f for f in flags if "/tmp/work" in f]
        assert mount_flags  # mounted
        assert not any(":ro" in f for f in mount_flags)  # not read-only


class TestSandboxRun:
    async def test_run_returns_result(self):
        sb = ExecutionSandbox()
        captured_cmd = []

        async def fake_exec(*args, **kwargs):
            captured_cmd.extend(args)
            proc = AsyncMock()
            proc.communicate.return_value = (b'hello\n', b'')
            proc.returncode = 0
            proc.kill = AsyncMock()
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await sb.run(
                ["echo", "hello"],
                tier=SandboxTier.STRICT,
                timeout=10,
            )

        assert isinstance(result, SandboxResult)
        assert result.exit_code == 0
        assert "hello" in result.stdout

    async def test_run_timeout(self):
        sb = ExecutionSandbox()

        async def fake_exec(*args, **kwargs):
            proc = AsyncMock()
            # Simulate a command that hangs — communicate never returns before timeout
            async def slow_communicate():
                await asyncio.sleep(999)
                return (b'', b'')
            proc.communicate = slow_communicate
            proc.kill = AsyncMock()
            proc.returncode = -9
            return proc

        # Patch module-level asyncio reference, NOT the global asyncio.wait_for
        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await sb.run(
                ["sleep", "999"],
                tier=SandboxTier.STRICT,
                timeout=0.1,
            )

        assert result.timed_out is True
        assert result.exit_code == -1

    async def test_run_redacts_output_secrets(self):
        sb = ExecutionSandbox()

        async def fake_exec(*args, **kwargs):
            proc = AsyncMock()
            proc.communicate.return_value = (
                b'key=ghp_ABCDEFghijklmnopqrstuvwxyz012345xx\n', b''
            )
            proc.returncode = 0
            proc.kill = AsyncMock()
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await sb.run(
                ["cat", "secret"],
                tier=SandboxTier.STRICT,
                timeout=10,
            )

        assert "ghp_" not in result.stdout
        assert "REDACTED" in result.stdout
```

Add `import asyncio` at the top of the test file.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/core/test_execution_sandbox.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement ExecutionSandbox**

```python
# src/core/execution_sandbox.py
"""统一执行沙盒 — 三档位 Docker 隔离。"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from enum import Enum

from src.core.credential_sanitizer import redact_secrets, sanitize_env, truncate_head_tail

logger = logging.getLogger("lapwing.core.execution_sandbox")

_DEFAULT_IMAGE = "lapwing-sandbox:latest"
_BRIDGE_NETWORK = "lapwing-sandbox"
_MAX_OUTPUT = 4000


class SandboxTier(Enum):
    STRICT = "strict"
    STANDARD = "standard"
    PRIVILEGED = "privileged"


_TIER_DEFAULTS: dict[SandboxTier, dict] = {
    SandboxTier.STRICT: {
        "memory": "256m",
        "cpus": "0.5",
        "network": "none",
        "workspace_ro": True,
    },
    SandboxTier.STANDARD: {
        "memory": "512m",
        "cpus": "1.0",
        "network": _BRIDGE_NETWORK,
        "workspace_ro": False,
    },
    SandboxTier.PRIVILEGED: {
        "memory": "1024m",
        "cpus": "2.0",
        "network": "host",
        "workspace_ro": False,
    },
}


@dataclass
class SandboxResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False


class ExecutionSandbox:
    """Unified Docker execution sandbox with three security tiers."""

    def __init__(self, docker_image: str = _DEFAULT_IMAGE):
        self._image = docker_image

    def _build_docker_flags(
        self,
        tier: SandboxTier,
        workspace: str | None,
    ) -> list[str]:
        cfg = _TIER_DEFAULTS[tier]
        flags = [
            "--rm",
            "--cap-drop=ALL",
            "--user", "sandboxuser",
            "--memory", cfg["memory"],
            "--cpus", cfg["cpus"],
        ]

        network = cfg["network"]
        if network == "host":
            flags.append("--network=host")
        else:
            flags.extend(["--network", network])

        if workspace:
            mount = f"{workspace}:/workspace"
            if cfg["workspace_ro"]:
                mount += ":ro"
            flags.extend(["-v", mount])
            flags.extend(["-w", "/workspace"])

        if tier != SandboxTier.PRIVILEGED:
            flags.extend(["--read-only"])
            flags.extend(["--tmpfs", "/tmp:rw,size=64m"])

        return flags

    async def run(
        self,
        command: list[str],
        *,
        tier: SandboxTier,
        timeout: int = 30,
        workspace: str | None = None,
        env: dict[str, str] | None = None,
        max_output: int = _MAX_OUTPUT,
    ) -> SandboxResult:
        """Run a command in a Docker container with the given tier."""
        allow_network = tier != SandboxTier.STRICT
        clean_env = sanitize_env(env, allow_network=allow_network) if env else None

        docker_flags = self._build_docker_flags(tier, workspace)

        docker_cmd = ["docker", "run"] + docker_flags
        if clean_env:
            for k, v in clean_env.items():
                docker_cmd.extend(["-e", f"{k}={v}"])
        docker_cmd.append(self._image)
        docker_cmd.extend(command)

        try:
            proc = await asyncio.create_subprocess_exec(
                *docker_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                raw_out, raw_err = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return SandboxResult(
                    stdout="", stderr="", exit_code=-1, timed_out=True,
                )

            stdout = redact_secrets(truncate_head_tail(
                raw_out.decode("utf-8", errors="replace"), max_output,
            ))
            stderr = redact_secrets(truncate_head_tail(
                raw_err.decode("utf-8", errors="replace"), max_output,
            ))
            exit_code = proc.returncode if proc.returncode is not None else -1
            return SandboxResult(
                stdout=stdout, stderr=stderr, exit_code=exit_code,
            )
        except FileNotFoundError:
            return SandboxResult(
                stdout="", stderr="Docker 未安装或不可用", exit_code=-1,
            )
        except Exception as e:
            logger.error("沙盒执行异常: %s", e)
            return SandboxResult(stdout="", stderr=str(e), exit_code=-1)

    async def run_local(
        self,
        command: list[str],
        *,
        timeout: int = 30,
        cwd: str | None = None,
        max_output: int = _MAX_OUTPUT,
    ) -> SandboxResult:
        """Run a command locally with sanitized env and process-group isolation."""
        clean_env = sanitize_env(dict(os.environ))

        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=clean_env,
                start_new_session=True,
            )
            try:
                raw_out, raw_err = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout,
                )
            except asyncio.TimeoutError:
                import os as _os
                import signal
                try:
                    _os.killpg(_os.getpgid(proc.pid), signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    proc.kill()
                await proc.communicate()
                return SandboxResult(
                    stdout="", stderr="", exit_code=-1, timed_out=True,
                )

            stdout = redact_secrets(truncate_head_tail(
                raw_out.decode("utf-8", errors="replace"), max_output,
            ))
            stderr = redact_secrets(truncate_head_tail(
                raw_err.decode("utf-8", errors="replace"), max_output,
            ))
            exit_code = proc.returncode if proc.returncode is not None else -1
            return SandboxResult(
                stdout=stdout, stderr=stderr, exit_code=exit_code,
            )
        except Exception as e:
            logger.error("本地执行异常: %s", e)
            return SandboxResult(stdout="", stderr=str(e), exit_code=-1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/core/test_execution_sandbox.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/execution_sandbox.py tests/core/test_execution_sandbox.py
git commit -m "feat(sandbox): add unified ExecutionSandbox with three-tier Docker isolation"
```

---

### Task 13: Sandbox config section

**Files:**
- Modify: `src/config/settings.py`
- Modify: `config/settings.py`
- Modify: `config.toml`
- Modify: `config.example.toml`

- [ ] **Step 1: Add SandboxConfig models to src/config/settings.py**

After `SkillConfig` class (line 444):

```python
class SandboxTierConfig(BaseModel):
    memory_mb: int = 256
    cpus: float = 0.5
    timeout: int = 30


class SandboxConfig(BaseModel):
    docker_image: str = "lapwing-sandbox:latest"
    network: str = "lapwing-sandbox"
    strict: SandboxTierConfig = Field(default_factory=lambda: SandboxTierConfig(
        memory_mb=256, cpus=0.5, timeout=30,
    ))
    standard: SandboxTierConfig = Field(default_factory=lambda: SandboxTierConfig(
        memory_mb=512, cpus=1.0, timeout=60,
    ))
    privileged: SandboxTierConfig = Field(default_factory=lambda: SandboxTierConfig(
        memory_mb=1024, cpus=2.0, timeout=300,
    ))
```

Add to `LapwingSettings` (after `skill` field):

```python
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
```

Add to `_ENV_MAP`:

```python
    # ── sandbox ──
    "SANDBOX_DOCKER_IMAGE": ["sandbox", "docker_image"],
    "SANDBOX_NETWORK": ["sandbox", "network"],
    "SANDBOX_STRICT_MEMORY_MB": ["sandbox", "strict", "memory_mb"],
    "SANDBOX_STRICT_CPUS": ["sandbox", "strict", "cpus"],
    "SANDBOX_STRICT_TIMEOUT": ["sandbox", "strict", "timeout"],
    "SANDBOX_STANDARD_MEMORY_MB": ["sandbox", "standard", "memory_mb"],
    "SANDBOX_STANDARD_CPUS": ["sandbox", "standard", "cpus"],
    "SANDBOX_STANDARD_TIMEOUT": ["sandbox", "standard", "timeout"],
    "SANDBOX_PRIVILEGED_MEMORY_MB": ["sandbox", "privileged", "memory_mb"],
    "SANDBOX_PRIVILEGED_CPUS": ["sandbox", "privileged", "cpus"],
    "SANDBOX_PRIVILEGED_TIMEOUT": ["sandbox", "privileged", "timeout"],
```

- [ ] **Step 2: Expose sandbox settings in config/settings.py**

Append after `SKILL_SANDBOX_TIMEOUT` (line 118):

```python
# ── Sandbox (unified) ───────────────────────
SANDBOX_DOCKER_IMAGE: str = _s.sandbox.docker_image
SANDBOX_NETWORK: str = _s.sandbox.network
SANDBOX_STRICT_MEMORY_MB: int = _s.sandbox.strict.memory_mb
SANDBOX_STRICT_CPUS: float = _s.sandbox.strict.cpus
SANDBOX_STRICT_TIMEOUT: int = _s.sandbox.strict.timeout
SANDBOX_STANDARD_MEMORY_MB: int = _s.sandbox.standard.memory_mb
SANDBOX_STANDARD_CPUS: float = _s.sandbox.standard.cpus
SANDBOX_STANDARD_TIMEOUT: int = _s.sandbox.standard.timeout
SANDBOX_PRIVILEGED_MEMORY_MB: int = _s.sandbox.privileged.memory_mb
SANDBOX_PRIVILEGED_CPUS: float = _s.sandbox.privileged.cpus
SANDBOX_PRIVILEGED_TIMEOUT: int = _s.sandbox.privileged.timeout
```

- [ ] **Step 3: Add [sandbox] section to config.toml and config.example.toml**

Append to both files:

```toml
[sandbox]
docker_image = "lapwing-sandbox:latest"
network = "lapwing-sandbox"

[sandbox.strict]
memory_mb = 256
cpus = 0.5
timeout = 30

[sandbox.standard]
memory_mb = 512
cpus = 1.0
timeout = 60

[sandbox.privileged]
memory_mb = 1024
cpus = 2.0
timeout = 300
```

- [ ] **Step 4: Wire ExecutionSandbox to read from config**

In `src/core/execution_sandbox.py`, replace the hardcoded `_TIER_DEFAULTS` with a factory that reads from settings:

```python
def _load_tier_defaults() -> dict[SandboxTier, dict]:
    try:
        from config.settings import (
            SANDBOX_NETWORK,
            SANDBOX_STRICT_MEMORY_MB, SANDBOX_STRICT_CPUS,
            SANDBOX_STANDARD_MEMORY_MB, SANDBOX_STANDARD_CPUS,
            SANDBOX_PRIVILEGED_MEMORY_MB, SANDBOX_PRIVILEGED_CPUS,
        )
        return {
            SandboxTier.STRICT: {
                "memory": f"{SANDBOX_STRICT_MEMORY_MB}m",
                "cpus": str(SANDBOX_STRICT_CPUS),
                "network": "none",
                "workspace_ro": True,
            },
            SandboxTier.STANDARD: {
                "memory": f"{SANDBOX_STANDARD_MEMORY_MB}m",
                "cpus": str(SANDBOX_STANDARD_CPUS),
                "network": SANDBOX_NETWORK,
                "workspace_ro": False,
            },
            SandboxTier.PRIVILEGED: {
                "memory": f"{SANDBOX_PRIVILEGED_MEMORY_MB}m",
                "cpus": str(SANDBOX_PRIVILEGED_CPUS),
                "network": "host",
                "workspace_ro": False,
            },
        }
    except ImportError:
        return _TIER_DEFAULTS
```

Update `_build_docker_flags` to call `_load_tier_defaults()`.

- [ ] **Step 5: Add config unit test**

```python
# Append to tests/core/test_execution_sandbox.py

class TestSandboxConfig:
    def test_default_config_loads(self):
        """SandboxConfig default values match design spec."""
        from src.config.settings import SandboxConfig
        cfg = SandboxConfig()
        assert cfg.strict.memory_mb == 256
        assert cfg.strict.cpus == 0.5
        assert cfg.standard.memory_mb == 512
        assert cfg.standard.cpus == 1.0
        assert cfg.privileged.memory_mb == 1024
        assert cfg.privileged.cpus == 2.0
        assert cfg.network == "lapwing-sandbox"

    def test_settings_expose_sandbox_values(self):
        """config/settings.py exposes the new SANDBOX_* constants."""
        from config.settings import (
            SANDBOX_DOCKER_IMAGE, SANDBOX_NETWORK,
            SANDBOX_STRICT_MEMORY_MB, SANDBOX_STANDARD_MEMORY_MB,
        )
        assert SANDBOX_DOCKER_IMAGE == "lapwing-sandbox:latest"
        assert SANDBOX_NETWORK == "lapwing-sandbox"
        assert SANDBOX_STRICT_MEMORY_MB == 256
        assert SANDBOX_STANDARD_MEMORY_MB == 512
```

- [ ] **Step 6: Run all tests**

Run: `pytest tests/ -x -q --timeout=60`
Expected: All pass (import smoke test should pick up new config)

- [ ] **Step 7: Commit**

```bash
git add src/config/settings.py config/settings.py config.toml config.example.toml src/core/execution_sandbox.py tests/core/test_execution_sandbox.py
git commit -m "feat(sandbox): add [sandbox] config section with per-tier resource limits"
```

---

### Task 14: Migrate SkillExecutor to use ExecutionSandbox

**Files:**
- Modify: `src/skills/skill_executor.py`
- Test: `tests/skills/test_skill_executor.py`

- [ ] **Step 1: Refactor SkillExecutor to delegate to ExecutionSandbox**

Replace `_run_in_sandbox()` to use `ExecutionSandbox.run()`:

```python
# Add import
from src.core.execution_sandbox import ExecutionSandbox, SandboxTier

class SkillExecutor:
    def __init__(self, skill_store, sandbox_image: str = "lapwing-sandbox"):
        self._store = skill_store
        self._sandbox_image = sandbox_image
        self._sandbox = ExecutionSandbox(docker_image=sandbox_image)

    async def _run_in_sandbox(
        self, code, arguments, dependencies, timeout,
    ) -> SkillResult:
        tmp_dir = tempfile.mkdtemp(prefix="lapwing_skill_")
        try:
            skill_path = Path(tmp_dir) / "skill.py"
            skill_path.write_text(code, encoding="utf-8")
            runner_code = self._build_runner(arguments, dependencies)
            runner_path = Path(tmp_dir) / "runner.py"
            runner_path.write_text(runner_code, encoding="utf-8")

            result = await self._sandbox.run(
                ["python3", "/workspace/runner.py"],
                tier=SandboxTier.STRICT,
                timeout=timeout,
                workspace=tmp_dir,
            )
            return SkillResult(
                success=(result.exit_code == 0),
                output=result.stdout,
                error=result.stderr,
                exit_code=result.exit_code,
                timed_out=result.timed_out,
            )
        except Exception as e:
            logger.error("沙盒执行异常: %s", e)
            return SkillResult(success=False, output="", error=str(e), exit_code=-1)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    async def _run_on_host(
        self, code, arguments, dependencies, timeout,
    ) -> SkillResult:
        tmp_dir = tempfile.mkdtemp(prefix="lapwing_skill_host_")
        try:
            skill_path = Path(tmp_dir) / "skill.py"
            skill_path.write_text(code, encoding="utf-8")
            runner_code = self._build_runner(arguments, [])
            runner_path = Path(tmp_dir) / "runner.py"
            runner_path.write_text(runner_code, encoding="utf-8")

            result = await self._sandbox.run_local(
                [sys.executable, str(runner_path)],
                timeout=timeout,
                cwd=tmp_dir,
            )
            return SkillResult(
                success=(result.exit_code == 0),
                output=result.stdout,
                error=result.stderr,
                exit_code=result.exit_code,
                timed_out=result.timed_out,
            )
        except Exception as e:
            logger.error("主机执行异常: %s", e)
            return SkillResult(success=False, output="", error=str(e), exit_code=-1)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
```

- [ ] **Step 2: Run skill executor tests**

Run: `pytest tests/skills/test_skill_executor.py -v`
Expected: All pass

- [ ] **Step 3: Commit**

```bash
git add src/skills/skill_executor.py
git commit -m "refactor(sandbox): migrate SkillExecutor to unified ExecutionSandbox"
```

---

### Task 15: Migrate ShellExecutor to use ExecutionSandbox

**Files:**
- Modify: `src/tools/shell_executor.py`
- Test: `tests/tools/test_shell_executor.py`

- [ ] **Step 1: Refactor ShellExecutor Docker backend to use ExecutionSandbox**

Replace `_execute_docker()` to delegate:

```python
# Add imports at top
from src.core.execution_sandbox import ExecutionSandbox, SandboxTier

_sandbox = ExecutionSandbox(docker_image=_DOCKER_IMAGE)

async def _execute_docker(command: str) -> ShellResult:
    """在 Docker 容器中执行命令（沙箱隔离）。"""
    result = await _sandbox.run(
        ["bash", "-c", command],
        tier=SandboxTier.STANDARD,
        timeout=SHELL_TIMEOUT,
        workspace=_DOCKER_WORKSPACE,
    )
    stdout, stdout_truncated = result.stdout, len(result.stdout) >= SHELL_MAX_OUTPUT_CHARS
    stderr, stderr_truncated = result.stderr, len(result.stderr) >= SHELL_MAX_OUTPUT_CHARS
    shell_result = ShellResult(
        stdout=stdout,
        stderr=stderr,
        return_code=result.exit_code,
        timed_out=result.timed_out,
        reason="" if result.exit_code == 0 else (
            f"Docker 命令执行超时（{SHELL_TIMEOUT}s）。" if result.timed_out
            else f"Docker 命令以退出码 {result.exit_code} 结束。"
        ),
        cwd="/workspace",
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
    )
    await _log_execution(f"[docker] {command}", shell_result)
    return shell_result
```

- [ ] **Step 2: Run shell executor tests**

Run: `pytest tests/tools/test_shell_executor.py -v`
Expected: All pass

- [ ] **Step 3: Commit**

```bash
git add src/tools/shell_executor.py
git commit -m "refactor(sandbox): migrate ShellExecutor Docker backend to unified ExecutionSandbox"
```

---

### Task 16: Migrate CodeRunner to use ExecutionSandbox

**Files:**
- Modify: `src/tools/code_runner.py`
- Test: `tests/tools/test_code_runner.py`

- [ ] **Step 1: Refactor CodeRunner to use ExecutionSandbox.run_local()**

```python
"""Python 代码执行沙箱 — 在临时目录中安全运行用户代码。"""

import logging
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from src.core.execution_sandbox import ExecutionSandbox

logger = logging.getLogger("lapwing.tools.code_runner")

_MAX_OUTPUT = 2000
_sandbox = ExecutionSandbox()


@dataclass
class CodeResult:
    """代码执行结果。"""
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False


async def run_python(code: str, timeout: int = 10) -> CodeResult:
    """在隔离的临时目录中执行 Python 代码。"""
    tmp_dir = tempfile.mkdtemp(prefix="lapwing_coder_")
    script_path = Path(tmp_dir) / "script.py"
    try:
        script_path.write_text(code, encoding="utf-8")

        result = await _sandbox.run_local(
            [sys.executable, str(script_path)],
            timeout=timeout,
            cwd=tmp_dir,
            max_output=_MAX_OUTPUT,
        )

        logger.info(f"[code_runner] 执行完成 exit_code={result.exit_code}")
        return CodeResult(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            timed_out=result.timed_out,
        )
    except Exception as e:
        logger.error(f"[code_runner] 执行异常: {e}")
        return CodeResult(stdout="", stderr=str(e), exit_code=-1)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
```

- [ ] **Step 2: Run code runner tests**

Run: `pytest tests/tools/test_code_runner.py -v`
Expected: All pass

- [ ] **Step 3: Commit**

```bash
git add src/tools/code_runner.py
git commit -m "refactor(sandbox): migrate CodeRunner to unified ExecutionSandbox"
```

---

### Task 17: Final integration test + cleanup

**Files:**
- Test: all executor test suites + import smoke test

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: All ~1257+ tests pass

- [ ] **Step 2: Remove unused imports from migrated files**

Check `src/tools/shell_executor.py`, `src/skills/skill_executor.py`, and `src/tools/code_runner.py` for any imports that are no longer needed after the refactor (e.g., `asyncio.create_subprocess_exec` references in shell_executor's `_execute_docker`).

- [ ] **Step 3: Final commit**

```bash
git add -u
git commit -m "chore(sandbox): remove unused imports after ExecutionSandbox migration"
```

---

## Summary

| Phase | Tasks | What ships |
|-------|-------|-----------|
| **1: Urgent fixes** | Tasks 1-4 | Resource limits on Docker, safety checks restored, bridge network |
| **2: Credential + output** | Tasks 5-11 | Env whitelist, secret redaction, head+tail truncation, Dockerfile hardening |
| **3: Unified sandbox** | Tasks 12-17 | `ExecutionSandbox` module, all executors migrated, config unified |

Each phase produces independently deployable, tested code. Run `pytest tests/ -x -q` after each phase as a gate.

## Deferred / Out of scope

These items from the design spec are intentionally not in this plan:

| Item | Why deferred |
|------|-------------|
| **Host skill execution has no FS isolation** | Stable skills run locally with sanitized env (Phase 2). Full Docker-ization of stable skills is a separate project requiring dependency pre-build strategy — see design spec §7 "两阶段执行". |
| **Dependency installation fails silently under `--network none`** | Requires the two-phase execution model (setup with network → run without). Deferred to Phase 4 per design spec §7. Short-term mitigation: pre-install common packages in Dockerfile (Task 10). |
| **CodeRunner full Docker-ization** | CodeRunner gets env sanitization + process isolation (Phase 2-3), but not Docker containers. Adding Docker would break the 10-second latency target for quick code snippets. Revisit when Agent Team usage increases. |
| **Two-phase execution model** | Design spec §7 — complex, requires image build pipeline. Deferred to Phase 4. |
| **Coder Agent RPC tool bridge** | Design spec §13 — depends on Agent Team stabilization. |
