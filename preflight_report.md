# Lapwing 预飞检查报告

**日期**: 2026-04-21
**结论**: ✅ 可以启动

---

## 第一部分：静态检查

### 1.1 导入完整性

| 检查项 | 结果 |
|--------|------|
| Python 语法检查（全 src/） | ✅ PASS — 零语法错误 |
| 运行时模块导入（pkgutil.walk_packages） | ✅ PASS — 0 个导入失败 |

### 1.2 退役代码残留扫描

| 检查项 | 结果 |
|--------|------|
| TacticalRules / QualityChecker / EvolutionEngine / ProactiveFilter / EventLogger / ConversationMemory | ✅ PASS — 无残留 |
| Telegram 相关 | ✅ PASS — 无残留 |
| 旧四层记忆引用 | ⚠️ WARN — `inner_tick_scheduler.py:95-122` 引用 `working_memory.md` |

**说明**: `working_memory` 此处指 `data/consciousness/working_memory.md` 文件路径，是意识循环的工作文件，**非**旧四层记忆系统的 `working_memory` 模块。无需修复。

### 1.3 配置一致性

| 检查项 | 结果 |
|--------|------|
| config.toml | ⚠️ 不存在 — 配置已迁移至 `src/config/` Pydantic 模型 + `.env`，TOML 未启用 |
| config/.env | ✅ PASS — 存在（1312 bytes） |
| os.getenv 直接调用 | ⚠️ WARN — 8 处直接调用（详见下方） |

**os.getenv 直接调用清单**（均为合理场景，无需立即修复）:

- `execution_sandbox.py:222` — 沙箱 env 清洗，需要原始 os.environ
- `model_config.py:371,373` — 运行时模型切换回退逻辑
- `credential_vault.py:59` — 密钥环境变量读取
- `shell_executor.py:34-36` — Shell 后端配置
- `auth/resolver.py:19` — 凭证解析器

### 1.4 数据库 schema 一致性

**lapwing.db 中的表**:
- `trajectory` ✅
- `commitments` ✅
- `reminders_v2` ✅
- `sqlite_sequence` ✅

**mutation_log.db 中的表**:
- `mutations` ✅
- `sqlite_sequence` ✅

**代码引用的 `tasks` 表**: 由 `task_model.py:56` 的 `CREATE TABLE IF NOT EXISTS` 自动创建，首次使用时建表。✅ 正确。

**events.db**: 不存在。✅ 正确 — EventLogger 已删除。

### 1.5 文件系统完整性

| 目录 | 状态 |
|------|------|
| data/identity/ | ✅ soul.md, constitution.md, snapshots |
| data/memory/episodic/ | ✅ |
| data/memory/semantic/ | ✅ |
| data/consciousness/ | ✅ |
| data/logs/ | ✅ |
| data/config/ | ✅ |
| data/browser/ | ✅ |
| data/skills/ | ✅（空目录，正确——尚无 skill） |
| data/chroma/ | ✅ |
| data/chroma_memory/ | ✅ |
| prompts/*.md | ✅（11 文件） |

**voice.md 位置**: 存放于 `prompts/lapwing_voice.md`，由 `IdentityFileManager` 管理。`data/identity/` 下无副本。✅ 设计正确。

---

## 第二部分：启动验证（沙箱 / 环境）

| 检查项 | 结果 |
|--------|------|
| Docker 沙箱 `lapwing-sandbox` 镜像 | ✅ PASS — `docker run --rm lapwing-sandbox echo "sandbox OK"` 成功 |
| PID 文件 | ✅ 存在（data/lapwing.pid），启动时会检查 |
| vitals.json | ✅ 存在 |

> 注：实际冷启动测试、API 端点冒烟测试、组件注册验证需在生产环境执行，本次为离线预飞检查。

---

## 第三部分：全量测试

```
1367 passed in 344.59s (0:05:44)
```

✅ **全绿**，零失败、零跳过。

---

## 第四部分：安全快检

| 检查项 | 结果 |
|--------|------|
| API key 明文扫描 | ✅ PASS — 无明文密钥（`credential_sanitizer.py` 中的 `sk-` 是 regex 模式定义，非泄漏） |
| VitalGuard realpath 防护 | ✅ PASS — `os.path.realpath` 在 L65、L143 用于路径解析 |
| url_safety SSRF 防护 | ✅ PASS — `check_url_safety()` 被 `browser_manager.py` 和 `personal_tools.py` 正确调用 |
| 沙箱安全默认值 | ✅ PASS — `skill_executor.py` 和 `code_runner.py` 中 SandboxTier 引用正确 |

**注**: `validate_url` 函数不存在（实际导出为 `check_url_safety`），但代码中无任何调用 `validate_url` 的地方。✅ 无影响。

---

## 第五部分：总结

### 结论

✅ **可以启动**。1367 项测试全通过，无语法错误，无导入失败，无退役代码残留，无安全隐患，数据库 schema 一致，Docker 沙箱可用。

### 已知低优先级事项（无需阻塞启动）

1. **os.getenv 直接调用**: 8 处直接使用 `os.getenv` 而非 `get_settings()`，均为合理场景（沙箱 env 清洗、凭证解析等），可在后续迭代中统一。
2. **config.toml 未启用**: CLAUDE.md 提及 TOML 迁移，实际配置仍走 Pydantic + .env。文档与实现已一致（settings.py 向后兼容层文档说明了这一点）。

### 启动命令

```bash
bash scripts/deploy.sh
```

**不要直接运行** `nohup python main.py &`。
