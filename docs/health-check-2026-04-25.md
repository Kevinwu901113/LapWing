# Lapwing 项目体检报告

**日期**: 2026-04-25
**审计方**: Codex (全仓审计) + Claude Opus (交叉验证)
**代码版本**: master @ 15449fe

---

## 基础验证

| 检查项 | 结果 |
|--------|------|
| `python3 -m compileall -q src tests` | 通过 |
| `pytest tests/ -x -q` | 1708 passed, 10 skipped |
| `cd desktop-v2 && npm run build` | 通过 (主 chunk 599 kB 偏大) |
| 工作区状态 | 有未提交改动: task_runtime.py (修复重复发送), durable_scheduler.py (late-bound 属性) |

---

## 最高优先级 — 运行时真实 Bug

### 1. httpx proxy 参数名错误 — **已确认**

**文件**: `src/research/fetcher.py:178`
**现象**: 传 `proxies={"all://": url}` 给 `httpx.AsyncClient`，但 httpx 0.28+ 已改为 `proxy` (单数，接字符串)。requirements.txt 写 `>=0.27`，如果实际安装了 0.28.1 则直接报 `TypeError`。
**影响**: 所有通过 SmartFetcher 的网页抓取（体育、天气、research）降级到浏览器再超时。日志里的 `unexpected keyword argument 'proxies'` 就是这个。
**测试固化了 bug**: `tests/research/test_fetcher.py:200` 断言 `proxies` 存在于 call kwargs。

```python
# 当前 (broken on httpx 0.28+)
client_kwargs["proxies"] = {"all://": proxy_url}

# 应改为
client_kwargs["proxy"] = proxy_url
```

**修复优先级**: P0 — 影响面最大的运行时 bug。

### 2. send_message(target="kevin_desktop") 导入路径错误 — **已确认**

**文件**: `src/tools/personal_tools.py:90`
**现象**: `from src.adapters.desktop import DesktopAdapter` — 实际文件是 `desktop_adapter.py`，类名是 `DesktopChannelAdapter`。
**影响**: 即使桌面有连接，`send_message` 到 `kevin_desktop` 也会因 ImportError 被 except 吞掉，然后报"Desktop 未连接"。
**测试掩盖**: `tests/tools/test_personal_tools.py:108` 用 fake module 绕过了真实导入。

**修复优先级**: P0 — 桌面主动推送完全不工作。

### 3. CorrectionManager 三处断裂 — **已确认**

| 子问题 | 位置 | 现象 |
|--------|------|------|
| `format_for_prompt()` 不存在 | `state_view_builder.py:406` 调用，`correction_manager.py` 无此方法 | `AttributeError` 被 `except` 吞掉，纠正记录永远不进 prompt |
| callback 签名不匹配 | `container.py:314` 定义 `(rule: str, entry: dict)` | CorrectionManager 实际传 `(rule_key, count, all_details)` 即 `(str, int, str)`，`entry.get()` 对 int 报 `TypeError` |
| 仅内存存储 | `correction_manager.py:39` | 重启后纠正记录全部丢失 |

**影响**: CorrectionManager 整条链路是断的：纠正不进 prompt、阈值回调会崩、重启归零。
**修复优先级**: P1 — 行为纠正闭环不工作。

### 4. 桌面 WebSocket 认证弱于 HTTP — **已确认**

**文件**: `src/api/routes/chat_ws.py:73`

```python
# WebSocket: 只检查 token 非空
token = ws.query_params.get("token", "")
if not DESKTOP_DEFAULT_OWNER and not token:
    await ws.close(...)
```

vs HTTP (`server.py:159`): 调 `auth_manager.validate_api_session(session_token)` 做真实验证。

**缓解因素**: WS 绑定 localhost + `DESKTOP_DEFAULT_OWNER=true` 时直接跳过检查，所以当前部署模式下风险有限。但如果 API 暴露到网络（反代、端口转发），任意非空 token 即可获得 OWNER 权限。
**修复优先级**: P1 — 统一成同一个 token 验证器。

---

## 中优先级 — 功能缺陷与配置漂移

### 5. 搜索/研究链路对实时查询不可靠 — **已确认，属产品层面**

- `src/research/backends/bocha.py:82`: 所有 Bocha 结果 `score=1.0`（注释说 Bocha 不返回分数）。副作用是 Bocha 结果在合并排序中永远排最前，压过 Tavily 带真实分数的结果。
- `src/research/engine.py:80`: 合并后只取 top 3 抓取，容易混进无关页面。
- 体育比分、天气等实时查询不该走通用搜索 → 抓取链路，应有专门 intent 或 API。

**修复优先级**: P2 — 用户体验问题，不是崩溃。

### 6. 循环检测在 config 里关闭 — **已确认**

**文件**: `config.toml:86` → `loop_detection.enabled = false`
**影响**: `task_runtime.py` 的重复警告和全局断路器直接返回。日志里已有多次 `think_inner timed out after 120s`。
**建议**: 先打开观察模式（只告警不阻断），积累数据后再启用阻断。

**修复优先级**: P2 — 有真实 timeout 发生，但关闭是刻意的。

### 7. Shell 安全判断可能误伤本项目路径 — **已确认，有条件触发**

**文件**: `src/tools/shell_executor.py:44,127-136`
**现象**: `_CURRENT_USER = getpass.getuser()` — 如果服务以 root 或其他用户运行，`/home/kevin/lapwing` 会被 `_other_home_prefixes()` 判定为"其他用户目录"而拒绝。
**当前风险**: 如果部署模式不变（以 kevin 运行），不会触发。但 systemd/Docker 部署可能以不同用户跑。

**修复优先级**: P2。

### 8. Shell Docker 配置 TOML/env 漂移 — **已确认**

`config.toml` 配了 `docker_image` 和 `docker_workspace`，但 `shell_executor.py` 只读环境变量，`settings.py` 也没有承接这些字段。配置看起来生效实际不生效。

**修复优先级**: P2。

### 9. Ambient 知识类型不一致 — **已确认**

- `src/research/types.py:31` — `confidence: Literal["high", "medium", "low"]` (字符串)
- `src/ambient/models.py:46` — `confidence: float`
- `src/tools/ambient_tools.py:107` 把字符串塞进 float 字段，测试用数值 mock 掩盖。

**修复优先级**: P2 — 运行时可能默默丢失精度或报错。

### 10. 后端静态前端路径过期 — **已确认**

**文件**: `src/api/server.py:21`

```python
_DIST_DIR = Path(__file__).parent.parent.parent / "desktop" / "dist"
```

应该指向 `desktop-v2/dist`。如果依赖后端托管桌面 SPA，会 404 或托管旧产物。

**修复优先级**: P2。

---

## 低优先级 — 技术债与文档

### 11. tell_user 旧架构注释残留 — **已确认**

**文件**: `src/core/task_runtime.py:447-451`

```python
# Step 5 cleanup: removed observation-only hallucination
# patch (...). Replaced by
# the structural fix — tell_user is the only user-facing
# path, commit_promise tracks intent.
```

当前架构是 direct output，tell_user 已不存在。注释会误导维护者。

### 12. Tauri 端有功能 stub

- `desktop-v2/src-tauri/src/lib.rs:39` server URL 写死
- 窗口监控 `window_monitor.rs` 是 TODO loop
- 游戏检测 `process_detector.rs` 永远 false
- sensing/silence 体验是空壳

### 13. 默认聊天工具面过大

当前 runtime profile 把 shell、browser、research、ambient、reminder、commitment 等大批工具一起暴露给普通聊天。结合日志里的超时和错误，建议按 intent 动态裁剪工具集。

### 14. BrowserGuard 已被置空

`src/app/container.py:956` 显式 `self._browser_guard = None`，注释写 Phase 1 移除。`browser_manager.py` 注释仍写"强制检查"。如果重新启用浏览器需要补回。

---

## 工作区已在修的改动

| 文件 | 改动内容 | 状态 |
|------|----------|------|
| `src/core/task_runtime.py` | 移除"最终回答也走 interim 发送"的路径，修复同一条消息发两遍 | 未提交 |
| `src/core/durable_scheduler.py` | 增加 late-bound `send_fn` / `urgency_callback` / `brain` 属性 | 未提交 |
| `tests/core/test_task_runtime.py` | 对应的防重复发送测试 | 未提交 |

---

## 交叉验证结论

Codex 的审计**质量很高**。14 个问题中：

- **11 个完全确认** — 问题真实存在，描述准确
- **1 个描述有偏差** — Shell 的 `pwd.getpwall()` 是合法 Python 调用 (Codex 没说它不合法，但 "误伤" 的前提是部署用户不是 kevin，当前部署下不触发)
- **2 个属产品层面** — 搜索可靠性 (#5) 和工具面 (#13) 是设计选择而非 bug

Codex 给的修复顺序也合理。我唯一的调整是：把 **send_message 桌面导入 (#2)** 提到和 httpx (#1) 并列 P0，因为它直接阻断了桌面主动推送功能。

---

## 建议修复顺序

| 优先级 | 修复项 | 预计工作量 |
|--------|--------|-----------|
| P0 | httpx `proxies` → `proxy` + 测试 | 30 min |
| P0 | `personal_tools.py` 导入路径 + 类名 | 15 min |
| P1 | CorrectionManager: 补 `format_for_prompt`、修 callback 签名、可选持久化 | 1-2 h |
| P1 | 统一 WS/HTTP token 验证 | 1 h |
| P2 | 打开 loop detection 观察模式 | 15 min |
| P2 | 修静态前端路径 `desktop` → `desktop-v2` | 5 min |
| P2 | Ambient confidence 类型对齐 | 30 min |
| P2 | Shell workspace owner 配置化 | 30 min |
| P2 | TOML/env Docker 配置统一 | 30 min |
| P3 | 清理 tell_user 注释、Tauri stub、工具面裁剪 | 按需 |
