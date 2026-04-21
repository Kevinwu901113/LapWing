# Lapwing 健康报告修复 — 变更报告

**日期**: 2026-04-21
**分支**: feat/unified-sandbox
**测试结果**: 1400 passed, 2 warnings (5m53s)

---

## FIX-1: 时区统一

**变更文件**:
- `src/core/time_utils.py` — 新增 `now()` 函数，使用 `zoneinfo.ZoneInfo` 返回时区感知的当前时间
- `src/core/maintenance_timer.py` — `datetime.now().hour` → `now().hour`
- `src/core/inner_tick_scheduler.py` — `datetime.now()` → `_now()` (from time_utils)
- `src/core/browser_manager.py` — 截图时间戳 `datetime.now()` → `_tz_now()`
- `src/core/vital_guard.py` — `auto_backup()` 中 `datetime.now()` → `now()`
- `src/tools/file_editor.py` — `_backup_file()` 中 `datetime.now()` → `now()`
- `src/core/task_runtime.py` — 工具结果文件名时间戳 → `_tz_now()`

**测试更新**: 无需（现有 time_utils 测试已覆盖）

---

## FIX-2: 权限表对齐

**变更文件**:
- `src/core/authority_gate.py` — OPERATION_AUTH 字典完整重写，与实际注册工具名对齐
- `tests/core/test_authority_gate.py` — `"memory_note"` → `"write_note"`

**测试更新**: 已更新（高风险工具列表中的旧名称修正）

---

## FIX-3: MiniMax temperature 钳位

**变更文件**:
- `src/core/llm_router.py` — 新增 `_clamp_provider_params()` 静态方法；在 `complete()` 和 `complete_with_tools()` 的 Anthropic 兼容路径中应用

**测试更新**: 无需（现有 llm_router 测试覆盖参数传递）

---

## FIX-4: QQ 图片下载限流

**变更文件**:
- `src/adapters/qq_adapter.py` — `_download_image_as_base64()` 改为流式下载，增加 `_MAX_IMAGE_BYTES = 10MB` 限制

**测试更新**: 无需

---

## FIX-5: 符号链接保护

**变更文件**:
- `src/core/vital_guard.py` — `_is_locked()` 和 `_resolve_paths()` 中 `Path.resolve()` → `os.path.realpath()`，fail-closed
- `src/tools/file_editor.py` — `_resolve_path()` 中 `Path.resolve()` → `os.path.realpath()`

**测试更新**: 无需

---

## FIX-6: 异步重试装饰器

**变更文件**:
- `src/utils/retry.py` (新文件) — `@async_retry` 装饰器，指数退避 + 抖动
- `src/research/backends/tavily.py` — `search()` 增加重试
- `src/research/backends/bocha.py` — `search()` 增加重试
- `src/core/minimax_vlm.py` — API 调用增加重试

**测试更新**: 无需

---

## FIX-7: 静默异常日志

**变更文件**:
- `src/memory/note_store.py` — 4 处 `except Exception: pass` → `logger.warning(..., exc_info=True)`
- `src/app/container.py` — 2 处 `except Exception: pass` → `logger.debug(...)`

**测试更新**: 无需

---

## FIX-8: 安全配置加固

**变更文件**:
- `src/config/settings.py` — `DesktopConfig.default_owner` 默认值 `True` → `False`

**测试更新**: 无需

---

## FIX-9: README 清理

**变更文件**:
- `README.md` — 更新架构图（PromptBuilder→StateViewBuilder，移除 Telegram，更新调度器名称，更新模块列表）

**测试更新**: 不适用

---

## FIX-10: env.example 文档

**变更文件**:
- `config/.env.example` — 增加安全配置段（SHELL_ALLOW_SUDO, DESKTOP_DEFAULT_OWNER）及完整的环境变量文档

**测试更新**: 不适用

---

## FIX-11: 浏览器 URL 安全

**变更文件**:
- `src/utils/url_safety.py` (新文件) — `check_url_safety()` (协议 + IP字面量 + DNS解析) 和 `safe_fetch()`
- `src/core/browser_manager.py` — `navigate()` 入口添加 URL 安全检查
- `src/tools/personal_tools.py` — `_check_browse_safety()` 委托给 `url_safety.check_url_safety`
- `tests/core/test_browser_manager.py` — `browser_mgr` fixture 中 mock `check_url_safety` 以允许本地测试服务器

**测试更新**: 已更新（mock URL 安全检查以兼容 localhost 测试服务器）

---

## FIX-12: SSRF DNS 解析

**变更文件**:
- 已包含在 FIX-11 的 `src/utils/url_safety.py` 中（`check_url_safety` 包含 DNS 解析 fail-closed 逻辑）

**测试更新**: 同 FIX-11

---

## FIX-13: 技能覆写保护

**变更文件**:
- `src/skills/skill_store.py` — `create()` 增加 `overwrite` 和 `force` 参数；无标志时拒绝覆写，stable/mature 需要 `force`
- `tests/skills/test_skill_store.py` — 拆分原 `test_create_duplicate_id_overwrites` 为两个测试：`test_create_duplicate_id_rejected_without_overwrite`（验证拒绝）和 `test_create_duplicate_id_overwrites_with_flag`（验证带标志可覆写）

**测试更新**: 已更新

---

## FIX-14: 沙箱依赖安装

**变更文件**:
- `src/skills/skill_executor.py` — STRICT 层 + 有依赖时提前拒绝；`_build_runner()` 中 `subprocess.check_call(..., stdout=DEVNULL, stderr=DEVNULL)` → `subprocess.run(..., capture_output=True, text=True)` + stderr 日志

**测试更新**: 无需

---

## FIX-15: Docker 网络预检

**变更文件**:
- `src/core/execution_sandbox.py` — 新增 `ensure_sandbox_network()` 静态方法，启动时检查/创建 Docker 网络

**测试更新**: 无需

---

## FIX-16: Docker max_output 传递

**变更文件**:
- `src/tools/shell_executor.py` — `_execute_docker()` 中 `_sandbox.run()` 增加 `max_output=max(SHELL_MAX_OUTPUT_CHARS, 1)` 参数

**测试更新**: 无需

---

## FIX-17: 安全文档修正

**变更文件**:
- `src/skills/skill_security.py` — 模块文档更新为多行说明，明确启发式检测定位
- `src/tools/skill_tools.py` — install_skill 描述由 "拒绝包含危险代码的技能" 更正为 "基础安全扫描（启发式检测，由沙箱提供实际隔离）"

**测试更新**: 无需

---

## 测试结果

```
1400 passed, 2 warnings in 353.15s (0:05:53)
```

2 个 warning 均为 `PytestWarning`（`test_personal_tools.py` 中非异步函数标记了 `@pytest.mark.asyncio`），不影响正确性。

**无失败项。**
