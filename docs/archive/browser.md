# Lapwing 浏览器子系统 — 完整实现蓝图

> 目标：让 Lapwing 拥有真正的浏览器操作能力——导航、点击、填表、登录、截图——像一个真人坐在电脑前操作浏览器。
>
> 本蓝图按模块组织，每个模块自包含，可直接交给 Claude Code 实现。模块间依赖关系在文末标注。

---

## 0. 技术选型决策与约束

### 0.1 为什么不直接用 browser-use 的 Agent

browser-use 自带一套 agent loop（感知→LLM决策→行动→反馈），和 Lapwing 的 `brain.py → task_runtime.py → tool loop` 架构冲突。如果直接用 browser-use 的 Agent：

- Lapwing 的 brain 变成只能"派任务然后等结果"，过程中无法保持人格
- 两套 LLM 调用链路并存（browser-use 自己调 LLM + Lapwing 的 router 调 LLM），凭据管理混乱
- tool loop 中的 voice reminder 刷新、内部独白过滤、[SPLIT] 分条发送全部失效
- EventBus 事件无法细粒度发布（前端看不到每一步操作）

### 0.2 选定方案：Playwright 直连 + 自研 DOM 处理

- 底层用 **Playwright (Python async)** 控制 Chromium
- 自己实现 DOM 清洗和可交互元素提取（参考 browser-use 的思路但不依赖它）
- 浏览器操作封装为 Lapwing 的标准 tool，跑在 Lapwing 的 tool loop 里
- 每一步浏览器操作都经过 brain 的 system prompt + voice reminder，人格不断裂

### 0.3 双模型策略：MiniMax M2.7 + MiMo-V2-Omni

**主对话模型**：MiniMax M2.7（Anthropic 协议）。纯文本，负责所有对话、tool calling 决策、人格表达。97% skill adherence，角色一致性和情感智能最强。

**浏览器视觉模型**：gpt-5.4（Codex 调用方式，Responses API）。原生 computer use——专门训练过"看截图→输出操作指令"。支持超 1000 万像素图片不压缩。走 Codex 订阅额度，无额外费用。

**工作流程**：

```
用户: "帮我看看小红书上有什么好吃的"
  ↓
Brain (MiniMax M2.7) → tool call: browser_open("https://www.xiaohongshu.com")
  ↓
BrowserManager:
  1. Playwright 打开页面
  2. DOMProcessor 提取可交互元素列表
  3. 判断页面是否图片密集（img 元素占比 > 阈值）
  4. 如果是 → 截图 + 发给 gpt-5.4 的 browser_vision slot
  5. gpt-5.4 返回视觉描述文本
  6. 合并 DOM 元素列表 + 视觉描述 → PageState
  ↓
tool result (纯文本) 返回给 Brain (MiniMax M2.7)
  ↓
Brain 基于 PageState 决定下一步操作或回复用户
```

**为什么两个模型分工**：MiniMax M2.7 在人格表达上专门优化过，是 Lapwing "她是一个人"体验的核心。gpt-5.4 的优势在视觉和 computer use，用在浏览器视觉 slot 是最对的位置。各司其职。

**成本**：浏览器视觉调用走 Codex 订阅额度，不产生额外 API 费用。

**可插拔性**：`browser_vision` slot 在 `model_routing.json` 中配置。将来如需切换视觉模型，改配置即可，代码不动。关闭视觉设 `BROWSER_VISION_ENABLED=false`，退回纯 DOM 方案。

### 0.4 资源评估

- Xeon E-2174G + 32GB RAM，无 GPU
- headless Chromium 内存占用：空标签页 ~80MB，普通页面 ~150-300MB，重度页面（Gmail 等）~500MB
- 估算常态内存占用：1 个持久化浏览器上下文 + 2-3 个标签页 ≈ 400-800MB
- 32GB 中扣除系统 + Lapwing 主进程 + SQLite/ChromaDB，可用内存约 25GB，完全够用

---

## 1. 模块：BrowserManager

**文件**：`src/core/browser_manager.py`
**预估行数**：~350 行
**职责**：浏览器生命周期管理、持久化状态、标签页管理

### 1.1 类定义

```python
class BrowserManager:
    """
    管理 Lapwing 的持久化浏览器实例。

    设计原则：
    - 单例模式，随 AppContainer 启动和关闭
    - 使用 Playwright 的 persistent context（userDataDir）保持登录态
    - 所有方法 async，与 Lapwing 的 asyncio 事件循环一致
    - 标签页有上限，超出时自动关闭最久未使用的
    """
```

### 1.2 核心接口

```python
class BrowserManager:
    async def start(self) -> None
        """
        启动 Playwright 和持久化浏览器上下文。
        - 启动 Playwright async API
        - 用 chromium.launch_persistent_context(user_data_dir=...) 创建持久化上下文
        - 配置 viewport、locale、timezone、user_agent
        - 恢复上次关闭时的标签页列表（可选，从 state file 读取）
        """

    async def stop(self) -> None
        """
        优雅关闭。
        - 保存当前标签页列表到 state file
        - 导出 storage_state 到 data/browser/storage_state.json（冗余备份）
        - 关闭所有 page
        - 关闭 browser context
        - 停止 Playwright
        """

    async def navigate(self, url: str, tab_id: str | None = None) -> PageState
        """
        导航到 URL。
        - 如果 tab_id 为 None，新建标签页
        - 如果 tab_id 存在，在对应标签页导航
        - 等待 load 或 networkidle（可配置）
        - 返回 PageState
        """

    async def get_page_state(self, tab_id: str | None = None) -> PageState
        """
        获取当前页面状态（不执行操作）。
        - 提取可交互元素
        - 生成页面文本摘要
        - 返回 PageState
        """

    async def click(self, element_ref: str, tab_id: str | None = None) -> PageState
        """
        点击元素。
        - element_ref 可以是 "[3]" 这样的元素编号，也可以是 CSS selector
        - 点击后等待导航或 DOM 变化稳定
        - 返回操作后的 PageState
        """

    async def type_text(self, element_ref: str, text: str, *, 
                        clear_first: bool = True,
                        press_enter: bool = False,
                        tab_id: str | None = None) -> PageState
        """
        在元素中输入文字。
        - clear_first=True 时先清空再输入
        - press_enter=True 时输入后按回车
        - 返回操作后的 PageState
        """

    async def select_option(self, element_ref: str, value: str,
                           tab_id: str | None = None) -> PageState
        """
        在 <select> 元素中选择选项。
        """

    async def scroll(self, direction: str = "down", amount: int = 3,
                    tab_id: str | None = None) -> PageState
        """
        滚动页面。direction: up/down/left/right。amount: 滚动屏数。
        """

    async def go_back(self, tab_id: str | None = None) -> PageState
    async def go_forward(self, tab_id: str | None = None) -> PageState

    async def screenshot(self, tab_id: str | None = None, 
                        full_page: bool = False) -> str
        """
        截图。返回截图文件的绝对路径。
        - 保存到 data/browser/screenshots/{timestamp}.png
        - 自动清理超过 N 天的截图
        """

    async def get_page_text(self, selector: str | None = None,
                           tab_id: str | None = None) -> str
        """
        提取页面或特定元素的纯文本。
        - 无 selector 时提取整个页面正文（去除 nav/footer/sidebar）
        - 有 selector 时提取匹配元素的文本
        - 长文本自动截断（可配置上限，默认 8000 字符）
        """

    async def list_tabs(self) -> list[TabInfo]
        """返回所有打开的标签页信息。"""

    async def switch_tab(self, tab_id: str) -> PageState
    async def close_tab(self, tab_id: str) -> None
    async def new_tab(self, url: str | None = None) -> TabInfo

    async def execute_js(self, expression: str, 
                        tab_id: str | None = None) -> str
        """
        执行 JavaScript 表达式。受 BrowserGuard 限制。
        返回序列化后的结果字符串。
        """

    async def wait_for(self, condition: str, timeout_ms: int = 10000,
                      tab_id: str | None = None) -> bool
        """
        等待条件满足。
        condition 类型：
        - "navigation" — 等待导航完成
        - "selector:xxx" — 等待元素出现
        - "idle" — 等待网络空闲
        """

    # ── 内部方法 ──

    def _resolve_tab(self, tab_id: str | None) -> Page
        """解析 tab_id 到 Playwright Page 对象。None = 当前活跃标签页。"""

    async def _resolve_element(self, page: Page, element_ref: str) -> Locator
        """
        将 element_ref 解析为 Playwright Locator。
        - "[3]" → 从最近一次 PageState 的元素列表中查找编号 3
        - "css:xxx" → CSS 选择器
        - "text:xxx" → 文本内容匹配
        - "xpath:xxx" → XPath
        """

    async def _ensure_tab_limit(self) -> None
        """如果标签页超过上限（BROWSER_MAX_TABS），关闭最久未使用的。"""

    def _generate_tab_id(self) -> str
        """生成短唯一 tab ID（如 "tab_a3f2"）。"""
```

### 1.3 数据模型

```python
@dataclass
class InteractiveElement:
    """页面上一个可交互元素。"""
    index: int                  # 元素编号，如 3
    tag: str                    # 标签类型，如 "button", "input", "a", "select"
    element_type: str | None    # input 的 type，如 "text", "password", "submit"
    text: str                   # 可见文本或 placeholder
    name: str | None            # name 属性
    aria_label: str | None      # aria-label
    href: str | None            # 链接地址（仅 a 标签）
    value: str | None           # 当前值（仅 input/select）
    is_visible: bool            # 是否在视口中可见
    selector: str               # 内部用的唯一选择器

@dataclass
class PageState:
    """一个页面的完整状态描述。给 LLM 看的。"""
    url: str
    title: str
    elements: list[InteractiveElement]
    text_summary: str           # 页面正文摘要（截断后）
    visual_description: str | None  # 视觉模型生成的页面描述（仅图片密集页面）
    scroll_position: str        # 如 "top", "middle", "bottom"
    has_more_below: bool        # 下方是否还有内容
    tab_id: str
    timestamp: str              # ISO 格式
    is_image_heavy: bool        # 页面是否以图片为主

    def to_llm_text(self, max_elements: int = 40) -> str:
        """
        格式化为 LLM 可读的文本描述。
        
        无视觉描述时（纯 DOM 模式）：
        ---
        [页面] GitHub 登录
        URL: https://github.com/login | 位置: 顶部

        可交互元素：
        [1] 输入框 "Username or email address"
        [2] 输入框 (password) "Password"
        [3] 按钮 "Sign in"

        页面内容：
        Sign in to GitHub · Sign in to your account...
        ---
        
        有视觉描述时（图片密集页面）：
        ---
        [页面] 小红书 - 探索
        URL: https://www.xiaohongshu.com/explore | 位置: 顶部

        可交互元素：
        [1] 搜索框 "搜索小红书"
        [2] 标签 "推荐"
        [3] 标签 "美食"

        页面视觉内容：
        瀑布流展示了 6 张美食帖子。第一张是红烧肉特写，
        标题"在家做出饭店味道的红烧肉"，2.3万赞...

        页面文字内容：
        小红书 - 你的生活指南...
        ---
        
        注意：视觉描述放在文字内容之前，因为对于图片密集页面
        视觉信息比 DOM 文本更重要。
        """

@dataclass
class TabInfo:
    """标签页信息。"""
    tab_id: str
    url: str
    title: str
    is_active: bool
    last_accessed: datetime
```

### 1.4 DOM 处理器

```python
class DOMProcessor:
    """
    将 Playwright 页面的 DOM 清洗为结构化的可交互元素列表。
    
    参考 browser-use 的思路，但独立实现：
    - 遍历所有可交互元素（a, button, input, select, textarea, [role=button], [onclick]）
    - 过滤不可见元素（display:none, visibility:hidden, 零尺寸）
    - 为每个元素分配递增编号
    - 提取人类可读的描述文本（优先级：aria-label > innerText > placeholder > name > title）
    - 生成内部唯一选择器（用于后续操作时精确定位）
    """
```

**实现要点**：

- 在 Playwright page 上执行一段注入的 JavaScript 来遍历 DOM，而不是在 Python 端逐个查询（性能考虑）
- JS 脚本返回序列化的元素数组，Python 端反序列化为 `InteractiveElement` 列表
- 对于 `<select>` 元素，额外提取所有 `<option>` 的文本和 value
- 对于 iframe 内的元素：Phase 1 不处理，Phase 2 再支持（标注 TODO）
- 元素编号在每次 `get_page_state()` 时重新分配，编号映射缓存在 `BrowserManager._element_map` 中供 `_resolve_element` 使用

**页面文本摘要**：

- 用 Playwright 的 `page.inner_text('body')` 获取全文
- 去除连续空白行
- 截断到 `BROWSER_PAGE_TEXT_MAX_CHARS`（默认 4000）
- 如果页面有明显的 `<main>` 或 `<article>` 标签，优先提取这些区域

### 1.5 持久化

**目录结构**：
```
data/browser/
├── profile/              # Playwright userDataDir（cookie、localStorage、cache）
├── screenshots/          # 截图（自动过期清理）
├── state.json            # 上次关闭时的标签页列表（用于恢复）
└── storage_state.json    # storage_state 冗余备份
```

**state.json 格式**：
```json
{
  "tabs": [
    {"tab_id": "tab_a3f2", "url": "https://github.com", "title": "GitHub"},
    {"tab_id": "tab_b7c1", "url": "https://mail.google.com", "title": "Gmail"}
  ],
  "active_tab": "tab_a3f2",
  "saved_at": "2026-04-09T15:30:00+08:00"
}
```

### 1.6 配置项（新增到 settings.py）

```python
# ── 浏览器 ──
BROWSER_ENABLED: bool = False                    # 总开关（默认关闭）
BROWSER_HEADLESS: bool = True                    # 无头模式
BROWSER_USER_DATA_DIR: str = "data/browser/profile"
BROWSER_MAX_TABS: int = 8                        # 最大标签页数
BROWSER_PAGE_TEXT_MAX_CHARS: int = 4000          # 页面文本摘要上限
BROWSER_NAVIGATION_TIMEOUT_MS: int = 30000       # 导航超时
BROWSER_ACTION_TIMEOUT_MS: int = 10000           # 操作超时
BROWSER_SCREENSHOT_DIR: str = "data/browser/screenshots"
BROWSER_SCREENSHOT_RETAIN_DAYS: int = 7          # 截图保留天数
BROWSER_VIEWPORT_WIDTH: int = 1280
BROWSER_VIEWPORT_HEIGHT: int = 720
BROWSER_LOCALE: str = "zh-CN"
BROWSER_TIMEZONE: str = "Asia/Taipei"
BROWSER_MAX_ELEMENT_COUNT: int = 50              # 最多返回多少个可交互元素
BROWSER_WAIT_AFTER_ACTION_MS: int = 1000         # 操作后等待 DOM 稳定的时间
```

### 1.7 错误处理

定义浏览器专用异常层级：

```python
class BrowserError(Exception):
    """浏览器操作的基础异常。"""

class BrowserNotStartedError(BrowserError):
    """浏览器未启动。"""

class BrowserNavigationError(BrowserError):
    """导航失败（超时、DNS 错误、SSL 错误等）。"""

class BrowserElementNotFoundError(BrowserError):
    """找不到指定元素。"""

class BrowserTabNotFoundError(BrowserError):
    """标签页不存在。"""

class BrowserTimeoutError(BrowserError):
    """操作超时。"""

class BrowserGuardBlockError(BrowserError):
    """被安全守卫拦截。"""
```

所有异常在 tool executor 层捕获，转换为人类可读的 tool result 返回给 LLM。LLM 看到的不是 stack trace，而是类似"打不开这个网页，连接超时了"。

### 1.8 线程安全与并发

- `BrowserManager` 内部用 `asyncio.Lock` 保护所有浏览器操作，防止 tool loop 并发操作同一个 page
- 原因：Playwright 的 Page 对象不是线程安全的，同一 page 上的操作必须串行
- 不同 tab 之间理论上可以并发，但 Phase 1 不做——串行更简单更安全

---

## 2. 模块：BrowserTools

**文件**：`src/tools/browser_tools.py`
**预估行数**：~400 行
**职责**：将 BrowserManager 封装为 ToolRegistry 可注册的标准工具

### 2.1 工具清单

| 工具名 | 能力标签 | 风险 | 参数 | 返回值 |
|--------|---------|------|------|--------|
| `browser_open` | browser | medium | `url: str` | PageState 的 LLM 文本 |
| `browser_click` | browser | medium | `element: str, tab_id?: str` | 操作后 PageState |
| `browser_type` | browser | medium | `element: str, text: str, press_enter?: bool, tab_id?: str` | 操作后 PageState |
| `browser_select` | browser | medium | `element: str, value: str, tab_id?: str` | 操作后 PageState |
| `browser_scroll` | browser | low | `direction?: str, amount?: int, tab_id?: str` | 新的 PageState |
| `browser_screenshot` | browser | low | `tab_id?: str, full_page?: bool` | 截图路径 + 简短描述 |
| `browser_get_text` | browser | low | `selector?: str, tab_id?: str` | 页面文本 |
| `browser_back` | browser | low | `tab_id?: str` | PageState |
| `browser_tabs` | browser | low | _(无参数)_ | 标签页列表 |
| `browser_switch_tab` | browser | low | `tab_id: str` | PageState |
| `browser_close_tab` | browser | low | `tab_id: str` | 确认消息 |
| `browser_wait` | browser | low | `condition: str, timeout_ms?: int, tab_id?: str` | 等待结果 |
| `browser_login` | browser | high | `service: str` | 操作结果 |

### 2.2 注册方式

在 `src/tools/registry.py` 的 `build_default_tool_registry()` 中，新增条件注册块：

```python
if settings.BROWSER_ENABLED:
    from src.tools.browser_tools import register_browser_tools
    register_browser_tools(registry, browser_manager)
```

`register_browser_tools(registry, browser_manager)` 函数一次性注册所有浏览器工具。每个工具的 executor 是一个闭包，捕获 `browser_manager` 引用。

### 2.3 tool result 格式设计

tool result 需要同时满足两个需求：(1) LLM 能据此做出下一步决策 (2) 不会太长撑爆上下文

**标准格式**：

```
[页面] GitHub 登录
URL: https://github.com/login | 位置: 顶部

可交互元素：
[1] 输入框 "Username or email address"
[2] 输入框 (password) "Password"
[3] 按钮 "Sign in"
[4] 链接 "Forgot password?" → /password_reset
[5] 链接 "Create an account" → /signup

页面内容（前 500 字）：
Sign in to GitHub to continue to your repositories...
```

**错误格式**：

```
操作失败：找不到编号 [7] 的元素。当前页面有 5 个可交互元素（编号 1-5）。
```

### 2.4 与现有工具的关系

`browser_open` / `browser_get_text` 和现有的 `web_fetch` 功能有重叠。区分策略：

- `web_fetch`：快速、轻量、无状态，适合抓取静态内容。保留不动。
- `browser_open`：重、有状态、可交互，适合需要 JS 渲染或用户交互的页面。

在 tool description 中明确引导 LLM 选择：

```
browser_open: 打开一个网页，可以看到完整的页面内容和所有可交互元素（按钮、输入框等）。
             适合需要操作页面（点击、填表、登录）或查看 JavaScript 动态渲染内容的场景。
             注意：比 web_fetch 慢，不需要交互时优先用 web_fetch。

web_fetch:   快速获取网页的文本内容。不能看到按钮或执行操作。
             适合只需要阅读内容的场景。
```

### 2.5 AuthorityGate 集成

所有浏览器工具注册时 `risk_level` 为 medium，在 `AuthorityGate` 的权限矩阵中：

- `browser_*` 全系列 → 仅 OWNER
- 理由：浏览器操作能访问任意网站、提交表单、触发支付，属于高敏感操作

### 2.6 RuntimeProfile 集成

在 `src/core/runtime_profiles.py` 中：

- `chat_shell` profile 的 capabilities 增加 `"browser"`
- 新增 `browser_research` profile：capabilities = `{"browser", "web"}`，用于心跳自主浏览

### 2.7 tool description 中的关键约束（给 LLM 看）

在 `browser_open` 的 tool schema description 中嵌入：

```
重要提示：
1. 使用 [编号] 引用元素，如 browser_click(element="[3]")
2. 每次操作后会返回新的页面状态，元素编号会重新分配
3. 不要在一次 tool call 中尝试完成所有操作，一步一步来
4. 如果页面有很多内容看不完，用 browser_scroll 翻页
5. 密码等敏感信息用 browser_login 工具，不要直接在 browser_type 中输入
```

---

## 3. 模块：CredentialVault

**文件**：`src/core/credential_vault.py`
**预估行数**：~200 行
**职责**：加密存储网站凭据，供 `browser_login` 工具使用

### 3.1 设计原则

- LLM 永远不接触明文密码。模型只说 `browser_login(service="github")`，CredentialVault 负责取出凭据并通过 BrowserManager 自动填入
- 凭据用 Fernet 对称加密存储，密钥从环境变量 `CREDENTIAL_VAULT_KEY` 加载
- CLI 命令管理凭据（增删改查），不通过 API 暴露
- 与 `AuthManager` 的区别：AuthManager 管 LLM provider 的凭据，CredentialVault 管网站的登录凭据

### 3.2 接口

```python
class CredentialVault:
    def __init__(self, vault_path: str, key_env: str = "CREDENTIAL_VAULT_KEY")
    
    def get(self, service: str) -> Credential | None
        """获取服务凭据。返回解密后的 Credential。"""
    
    def set(self, service: str, credential: Credential) -> None
        """保存或更新凭据。"""
    
    def delete(self, service: str) -> bool
    def list_services(self) -> list[str]
        """返回所有已存储的服务名。不返回密码。"""

@dataclass
class Credential:
    service: str            # 服务名，如 "github"
    username: str           # 用户名/邮箱
    password: str           # 密码（明文，仅在内存中）
    login_url: str          # 登录页面 URL
    extra: dict | None      # 额外字段（如 2FA 秘钥）
    notes: str | None       # 登录特殊说明
```

### 3.3 存储格式

```
data/credentials/
└── vault.enc             # Fernet 加密的 JSON
```

加密前的 JSON 结构：
```json
{
  "version": 1,
  "services": {
    "github": {
      "username": "kevin@example.com",
      "password": "xxx",
      "login_url": "https://github.com/login",
      "extra": null,
      "notes": "2FA 用 authenticator app"
    }
  }
}
```

### 3.4 CLI 命令

在 `main.py` 的 CLI 中新增 `credential` 子命令组：

```bash
python main.py credential list
python main.py credential set github --username kevin@xxx --login-url https://github.com/login
# 交互式输入密码（不在命令行明文显示）
python main.py credential delete github
python main.py credential generate-key
# 生成新的 Fernet key 并打印，用户需将其设置为环境变量
```

### 3.5 `browser_login` 的执行流程

```
LLM 调用 browser_login(service="github")
  ↓
BrowserTools 的 executor:
  1. 从 CredentialVault 获取 credential
  2. 如果不存在 → 返回错误 "没有保存 github 的登录信息"
  3. BrowserManager.navigate(credential.login_url)
  4. BrowserManager.get_page_state()
  5. 在页面中查找用户名/密码输入框（通过 type="text/email" + type="password" 匹配）
  6. BrowserManager.type_text(username_element, credential.username)
  7. BrowserManager.type_text(password_element, credential.password)
  8. 查找提交按钮并点击
  9. 等待导航
  10. 检查是否出现 2FA 页面
      - 如果是 → 返回 "需要验证码，请告诉我验证码" + 设置 pending_2fa 状态
      - 如果否 → 返回登录后的 PageState
```

### 3.6 2FA 处理

使用现有的 `PendingShellConfirmation` 机制的变体：

```python
@dataclass
class Pending2FA:
    service: str
    tab_id: str
    element_ref: str     # 2FA 输入框的元素引用
    created_at: datetime
```

用户下一条消息如果包含 6 位数字，`brain._prepare_think()` 中检测到 `pending_2fa`，自动调用 `browser_type` 填入验证码并提交。

---

## 4. 模块：BrowserGuard

**文件**：`src/guards/browser_guard.py`
**预估行数**：~200 行
**职责**：浏览器操作的安全守卫

### 4.1 检查类型

```python
class BrowserGuard:
    def check_url(self, url: str) -> GuardResult
        """
        检查 URL 是否允许访问。
        - BLOCK: 在黑名单中的域名
        - PASS: 在白名单中或不在黑名单中
        
        黑名单默认包括：
        - 已知恶意域名（加载自配置文件）
        - data: 和 javascript: 协议
        - 内网地址（127.0.0.1, 10.*, 192.168.* 等），防止 SSRF
        """

    def check_action(self, action: str, page_state: PageState, 
                     params: dict) -> GuardResult
        """
        检查浏览器操作是否需要用户确认。
        
        REQUIRE_CONSENT 场景：
        - 表单提交到非白名单域名
        - 点击包含 "delete", "删除", "remove" 等文字的按钮
        - 点击包含 "pay", "支付", "purchase", "buy" 等文字的按钮
        - 点击包含 "confirm order", "确认订单" 等文字的按钮
        - 页面标题/URL 包含 "checkout", "payment" 等关键词
        
        PASS 场景：
        - 其他所有操作
        """

    def check_js(self, expression: str) -> GuardResult
        """
        检查 JavaScript 执行是否安全。
        - BLOCK: 包含 eval, Function, document.cookie 赋值, 
                 window.location 赋值, XMLHttpRequest, fetch 到外部域名
        - PASS: 纯读取操作、DOM 查询等
        """

@dataclass 
class GuardResult:
    action: str    # "pass" | "block" | "require_consent"
    reason: str | None
```

### 4.2 配置

```python
# settings.py 新增
BROWSER_URL_BLACKLIST: list[str] = []            # 额外黑名单域名
BROWSER_URL_WHITELIST: list[str] = []            # 白名单域名（这些域名的表单提交不需要确认）
BROWSER_BLOCK_INTERNAL_NETWORK: bool = True      # 阻止访问内网地址
BROWSER_SENSITIVE_ACTION_WORDS: list[str] = [    # 触发确认的按钮文字
    "delete", "remove", "pay", "purchase", "buy", "submit order",
    "删除", "移除", "支付", "购买", "确认订单", "提交订单"
]
```

### 4.3 与 VitalGuard 的关系

- VitalGuard 保护的是 Lapwing 自身的文件和进程
- BrowserGuard 保护的是外部网站操作
- 两者独立，不互相调用
- 在 `TaskRuntime.execute_tool()` 的安全链中，BrowserGuard 的检查和 VitalGuard 并列

### 4.4 集成到 TaskRuntime 安全链

现有安全链：`AuthorityGate → VitalGuard → ShellPolicy`

新增后：`AuthorityGate → VitalGuard / BrowserGuard → ShellPolicy`

判断逻辑：
- 如果工具的 capability 包含 `"browser"` → 走 BrowserGuard
- 如果工具的 capability 包含 `"shell"` → 走 VitalGuard + ShellPolicy
- 两者不会同时触发

---

## 5. 模块：EventBus 集成

**修改文件**：`src/core/desktop_event_bus.py`（已有）
**新增事件类型**：

### 5.1 新增浏览器相关事件

| 事件 | 触发时机 | Payload |
|------|----------|---------|
| `browser.navigating` | 开始导航 | `{tab_id, url}` |
| `browser.navigated` | 导航完成 | `{tab_id, url, title}` |
| `browser.action` | 执行点击/输入等操作 | `{tab_id, action, element_text, url}` |
| `browser.screenshot` | 截图完成 | `{tab_id, path}` |
| `browser.tab_opened` | 新标签页 | `{tab_id, url}` |
| `browser.tab_closed` | 关闭标签页 | `{tab_id}` |
| `browser.error` | 操作失败 | `{tab_id, error, action}` |
| `browser.login_started` | 开始登录流程 | `{service}` |
| `browser.login_success` | 登录成功 | `{service}` |
| `browser.login_2fa` | 需要 2FA | `{service}` |
| `browser.consent_required` | 需要用户确认 | `{action, reason, element_text}` |

### 5.2 前端消费

这些事件通过 SSE `/api/events/stream` 推送到前端。前端的 AgentPanel 可以显示：

- "正在打开 github.com..."
- "正在点击 Sign in 按钮..."
- "正在登录 GitHub..."
- "⚠️ 需要确认：点击'删除仓库'按钮"

### 5.3 Telegram/QQ 通道的浏览器操作反馈

当用户在 Telegram/QQ 让 Lapwing 做浏览器操作时，通过 `think_conversational` 的 `on_interim_text` 机制，模型可以在 tool loop 中间发送状态更新（如"我在看这个页面..."）。

截图通过 `send_image` 工具发送给用户（使用 `ChannelManager`）。

---

## 6. 模块：AppContainer 集成

**修改文件**：`src/app/container.py`

### 6.1 构造阶段新增

```python
# 在 __init__ 中
if settings.BROWSER_ENABLED:
    self._credential_vault = CredentialVault(
        vault_path="data/credentials/vault.enc"
    )
    self._browser_guard = BrowserGuard()
    self._browser_manager = BrowserManager(
        user_data_dir=settings.BROWSER_USER_DATA_DIR,
        headless=settings.BROWSER_HEADLESS,
        # ... 其他配置
    )
```

### 6.2 准备阶段新增

```python
# 在 prepare() 中
if settings.BROWSER_ENABLED:
    await self._browser_manager.start()
```

### 6.3 依赖注入

```python
# 在 _configure_brain_dependencies() 中
if settings.BROWSER_ENABLED:
    brain.browser_manager = self._browser_manager
```

### 6.4 工具注册

```python
# 在构建 ToolRegistry 时
if settings.BROWSER_ENABLED:
    register_browser_tools(
        registry=self._tool_registry,
        browser_manager=self._browser_manager,
        credential_vault=self._credential_vault,
        browser_guard=self._browser_guard,
        event_bus=self._event_bus,
    )
```

### 6.5 关闭阶段

```python
# 在 shutdown() 中，位于 heartbeat 停止之后、其他组件关闭之前
if settings.BROWSER_ENABLED and self._browser_manager:
    await self._browser_manager.stop()
```

### 6.6 Heartbeat 集成

在 `_build_heartbeat()` 中，如果 `BROWSER_ENABLED`：

- `AutonomousBrowsingAction` 的实现从 `web_fetch` 升级为可选使用 `BrowserManager`
- 新增配置 `BROWSE_USE_BROWSER: bool = False`，控制心跳自主浏览是否使用真实浏览器
- Phase 1 默认不开启（心跳仍然用 web_fetch），Phase 2 再切换

---

## 7. 模块：Prompt 适配

**新增文件**：无（修改现有 prompt 文件）

### 7.1 `capabilities.md` 新增浏览器能力描述

在 `prompts/lapwing_capabilities.md` 末尾追加：

```markdown
## 浏览器

你可以打开真实的浏览器来操作网页。不是抓取 HTML，是真的能看到页面、点按钮、填表格、登录网站。

使用场景：
- 需要登录才能看到的内容
- 需要点击、填表才能完成的操作（比如注册、下单、设置）
- JavaScript 动态渲染的页面（web_fetch 抓不到内容的）
- 需要多步交互的流程

不需要浏览器的场景：
- 只是查个信息 → 用 web_search + web_fetch，更快
- 只是读文章内容 → 用 web_fetch

操作方式：
1. browser_open 打开页面，你会看到所有可以点击/输入的元素列表
2. 用 [编号] 引用元素，如 browser_click(element="[3]") 
3. 每次操作后都会返回新的页面状态，一步一步来
4. 遇到密码输入用 browser_login，不要直接输入密码

注意：每次操作后元素编号会重新分配，不要记忆旧编号。
```

### 7.2 `examples.md` 新增浏览器操作示例

追加 2 组对话示例：

```markdown
### 场景：帮忙查登录才能看的内容

Kuan: 帮我看看 GitHub 上那个 private repo 的 issue 列表
Lapwing: 好，我打开看看
[SPLIT]
嗯，需要登录，我先登一下
[SPLIT]
登进去了。你那个 repo 现在有 3 个 open issue：
- #12 "fix memory leak in heartbeat" — 你昨天开的
- #10 "add QQ group filter" — 上周的，还没关
- #8 "voice reminder depth injection" — 这个标了 bug

要我帮你看哪个的详情吗？

### 场景：帮忙操作网页

Kuan: 帮我去 Cloudflare 的 dashboard 看看域名 DNS 配置对不对
Lapwing: 我去看一眼
[SPLIT]
打开了，你的 lapw1ng.com 有这些 DNS 记录：
- A 记录 → 1.2.3.4
- CNAME www → lapw1ng.com
- MX → mail.xxx.com

看着没什么问题，你是觉得哪里不对吗？
```

### 7.3 voice reminder 中的浏览器操作注意事项

在 `_PERSONA_ANCHOR` 中不需要加浏览器相关内容——voice reminder 关注的是说话方式，不是工具使用方式。

但在 `_refresh_voice_reminder`（tool loop 中间的 voice 刷新）中，如果当前正在进行浏览器操作，追加一句：

```
（你正在浏览网页。跟 Kuan 说话时正常说，不要描述你的操作步骤。）
```

---

## 8. 模块：API 端点

**修改文件**：`src/api/server.py`

### 8.1 新增 REST 端点

| 方法 | 路径 | 用途 |
|------|------|------|
| GET | `/api/browser/status` | 浏览器状态（是否运行、标签页数、内存占用） |
| GET | `/api/browser/tabs` | 当前打开的标签页列表 |
| GET | `/api/browser/screenshot/{tab_id}` | 获取指定标签页的截图 |
| POST | `/api/browser/navigate` | 手动导航（调试用） |
| POST | `/api/browser/close-tab/{tab_id}` | 手动关闭标签页 |

### 8.2 SSE 事件

所有 `browser.*` 事件通过现有 SSE `/api/events/stream` 推送，无需新增端点。

### 8.3 WebSocket `/ws/chat` 扩展

现有消息格式不变。浏览器操作的截图通过 RichMessage 的 IMAGE segment 发送，前端 ChatBubble 已经支持图片渲染。

---

## 9. 模块：前端适配（可选，Phase 2）

**仅做设计，不在 Phase 1 实现。**

### 9.1 BrowserPanel 组件

在 ChatPage 的侧边栏，新增 BrowserPanel（可折叠），功能：

- 当前标签页列表（可切换、可关闭）
- 当前页面的实时截图缩略图（每次 PageState 更新时刷新）
- 操作历史时间线（从 browser.* 事件渲染）

### 9.2 Settings 页面

Settings → 浏览器 Tab：

- 总开关
- 最大标签页数
- 截图保留天数
- URL 黑名单/白名单编辑

---

## 10. 模块：测试

**新增文件**：
- `tests/core/test_browser_manager.py`
- `tests/core/test_credential_vault.py`
- `tests/tools/test_browser_tools.py`
- `tests/guards/test_browser_guard.py`

### 10.1 测试策略

BrowserManager 的测试使用 **Playwright 的 mock server** 或 **本地 HTTP 服务器**：

```python
# conftest.py
@pytest.fixture
async def mock_server():
    """启动一个本地 HTTP 服务器，提供测试页面。"""
    # 提供几个固定页面：
    # /login — 带用户名/密码表单的登录页
    # /dashboard — 登录后的仪表盘
    # /form — 复杂表单（select, checkbox, radio）
    # /dynamic — JS 动态渲染的内容
    # /slow — 5 秒后才加载完成
```

### 10.2 测试用例清单

**BrowserManager**：
- `test_start_stop` — 启动和关闭不报错
- `test_navigate_success` — 正常导航返回 PageState
- `test_navigate_timeout` — 导航超时抛 BrowserNavigationError
- `test_navigate_invalid_url` — 无效 URL 的错误处理
- `test_click_element_by_index` — 用 [编号] 点击元素
- `test_click_element_not_found` — 元素不存在的错误处理
- `test_type_text` — 输入文字
- `test_type_text_with_enter` — 输入后按回车
- `test_type_text_password_field` — 密码字段输入
- `test_select_option` — 下拉选择
- `test_scroll_down_up` — 滚动
- `test_screenshot` — 截图保存
- `test_tab_management` — 新建/切换/关闭标签页
- `test_tab_limit` — 超过上限自动关闭最旧的
- `test_persistent_context` — 重启后 cookie 保持
- `test_page_state_element_extraction` — DOM 元素提取的正确性
- `test_page_state_text_truncation` — 文本截断
- `test_concurrent_operations` — asyncio.Lock 防止并发

**CredentialVault**：
- `test_set_get_credential` — 存取
- `test_encryption` — 文件确实是加密的
- `test_delete` — 删除
- `test_missing_key` — 没有加密密钥时的错误处理
- `test_corrupted_vault` — 文件损坏时的恢复

**BrowserGuard**：
- `test_block_internal_network` — 拦截内网地址
- `test_block_javascript_protocol` — 拦截 javascript: URL
- `test_sensitive_button_consent` — 敏感按钮需要确认
- `test_whitelist_bypass` — 白名单域名不需要确认
- `test_js_execution_block` — 危险 JS 被拦截

**BrowserTools**（集成测试）：
- `test_full_login_flow` — 完整登录流程
- `test_form_filling` — 表单填写
- `test_multi_step_navigation` — 多步导航
- `test_error_messages_human_readable` — 错误消息是人话不是 stack trace

---

## 11. 依赖管理

### 11.1 新增 Python 依赖

```
playwright>=1.49.0
cryptography>=44.0.0      # Fernet 加密（CredentialVault）
```

### 11.2 系统依赖

```bash
# 在 VM 上安装 Playwright 浏览器
pip install playwright
playwright install chromium
playwright install-deps chromium   # 安装系统级依赖（字体等）
```

### 11.3 注意事项

- Playwright 安装 Chromium 大约需要 ~300MB 磁盘空间
- `playwright install-deps` 需要 sudo 权限（安装系统库）
- 部署脚本 `scripts/` 中应新增 `setup_browser.sh`

---

## 12. 文件目录变更汇总

### 新增文件

```
src/
├── core/
│   ├── browser_manager.py       # 浏览器生命周期管理 + 视觉理解（~450行）
│   └── credential_vault.py      # 凭据加密存储（~200行）
├── tools/
│   └── browser_tools.py         # 浏览器工具注册（~400行）
├── guards/
│   └── browser_guard.py         # 浏览器安全守卫（~200行）

prompts/
└── browser_vision_describe.md   # 视觉描述 prompt 模板

tests/
├── core/
│   ├── test_browser_manager.py  # BrowserManager 测试
│   ├── test_browser_vision.py   # 视觉理解测试
│   └── test_credential_vault.py # CredentialVault 测试
├── tools/
│   └── test_browser_tools.py    # 浏览器工具集成测试
├── guards/
│   └── test_browser_guard.py    # BrowserGuard 测试

scripts/
└── setup_browser.sh             # Playwright + Chromium 安装脚本

data/
├── browser/                     # 运行时数据（不在仓库中）
│   ├── profile/                 # Playwright userDataDir
│   ├── screenshots/             # 截图
│   ├── state.json               # 标签页恢复
│   └── storage_state.json       # 冗余备份
├── credentials/                 # 凭据（不在仓库中）
│   └── vault.enc                # 加密的凭据文件
```

### 修改文件

```
config/settings.py              # 新增 BROWSER_*、BROWSER_VISION_*、CREDENTIAL_* 配置项
src/app/container.py            # 新增 BrowserManager、CredentialVault、BrowserGuard 初始化
src/tools/registry.py           # 条件注册浏览器工具
src/core/runtime_profiles.py    # 增加 browser capability
src/core/task_runtime.py        # 安全链增加 BrowserGuard 分支 + 历史 PageState 压缩
src/core/llm_router.py          # 新增 browser_vision slot 支持（slot 定义 + 图片消息构建）
src/api/server.py               # 新增 /api/browser/* 端点
prompts/lapwing_capabilities.md # 新增浏览器能力描述
prompts/lapwing_examples.md     # 新增浏览器操作对话示例
main.py                         # 新增 credential CLI 子命令
data/config/model_routing.json  # 新增 browser_vision slot（codex provider，gpt-5.4）
```

---

## 13. 模块依赖关系

```
credential_vault.py (独立，无依赖)
        │
browser_guard.py (独立，无依赖)
        │
browser_manager.py (依赖: playwright, llm_router[browser_vision slot])
        │
browser_tools.py (依赖: browser_manager, credential_vault, browser_guard, event_bus)
        │
container.py (组装所有模块)
        │
task_runtime.py (安全链新增 BrowserGuard + 历史 PageState 压缩)
        │
llm_router.py (新增 browser_vision slot)
        │
settings.py + prompts/ + model_routing.json (配置、提示词、路由)
```

### 建议实现顺序

1. `settings.py` — 先加所有配置项（含 BROWSER_VISION_* ）
2. `credential_vault.py` + 测试 — 独立模块，先验证
3. `browser_guard.py` + 测试 — 独立模块
4. `browser_manager.py`（不含视觉）+ 测试 — 核心，纯 DOM 方案先跑通
5. `browser_tools.py` + 测试 — 依赖 1-4
6. `container.py` + `task_runtime.py` 修改 — 集成 + PageState 压缩
7. `registry.py` + `runtime_profiles.py` — 注册
8. `prompts/` — Prompt 适配（capabilities.md + examples.md + browser_vision_describe.md）
9. `server.py` — API 端点
10. `main.py` — CLI 命令（credential 子命令）
11. `model_routing.json` — 新增 `browser_vision` slot 配置（mimo provider）
12. `browser_manager.py` 增加 `_visual_describe()` + `_should_use_vision()` — 视觉理解
13. `test_browser_vision.py` — 视觉模块测试
14. 端到端验收 — 跑通所有 19 章验收标准中的场景

---

## 14. 后续规划（不在本次实现范围）

### Phase 2：iframe 支持

- `DOMProcessor` 递归进入 iframe 提取元素
- 元素编号加前缀区分主框架和 iframe（如 `[f1:3]` 表示第 1 个 iframe 的第 3 个元素）

### Phase 2：心跳真实浏览

- `AutonomousBrowsingAction` 使用 BrowserManager 替代 web_fetch
- Lapwing 可以真正"浏览" HackerNews、Reddit、小红书，看到图片和 JS 渲染内容
- 浏览过程中截图 + 视觉描述保存到知识笔记

### Phase 2：前端 BrowserPanel

- 实时截图缩略图
- 操作历史时间线
- 手动操作按钮（在前端直接点击页面元素）

### Phase 3：网页表单自动化

- 识别常见表单模式（注册、搜索、联系表单）
- 自动推断字段含义并填充
- 经验技能系统记录成功的表单填写模式

### Phase 3：下载管理

- 拦截浏览器下载事件
- 保存到指定目录（`data/browser/downloads/`）
- 支持的文件类型白名单

### Phase 3：Cookie 和 Session 健康检查

- 定期检查持久化的登录态是否过期
- 心跳 action 自动刷新快过期的 session
- 过期时通知用户重新登录

---

## 15. 模块：视觉理解（browser_vision slot）

**本模块在 Phase 1 即实现**，因为小红书等图片密集网站是核心使用场景。

### 15.1 LLMRouter 新增 Slot

在 `model_routing.json` 和 LLMRouter 的 slot 体系中新增 `browser_vision`，支持 primary + fallback 配置：

```json
{
  "slots": {
    "browser_vision": {
      "provider_id": "codex",
      "model": "gpt-5.4",
      "purpose": "浏览器页面视觉理解（原生 computer use）"
    }
  },
  "providers": {
    "codex": {
      "base_url": "https://chatgpt.com/backend-api/codex/responses",
      "api_type": "codex_runtime",
      "credential": {"kind": "oauth", "profile": "openai-codex"}
    }
  }
}
```

**gpt-5.4 的图片消息格式（Responses API）**：

```python
# Responses API 的图片输入格式
{
    "type": "input_image",
    "image_url": "data:image/png;base64,{base64_data}",
    "detail": "auto"  # 或 "original" 用于高精度场景
}
```

注意：这和 OpenAI Chat Completions API 的 `{"type": "image_url", "image_url": {"url": "..."}}` 格式不同。LLMRouter 需要根据 api_type 选择正确的图片消息格式。如果 LLMRouter 的 Codex Runtime 通路目前不支持图片消息构建，需要在实现时补充。

### 15.2 BrowserManager 内部方法：`_visual_describe()`

```python
async def _visual_describe(self, page: Page, tab_id: str) -> str | None:
    """
    对当前页面截图并调用视觉模型生成描述。
    
    触发条件（由 _should_use_vision() 判断）：
    1. BROWSER_VISION_ENABLED=true（总开关）
    2. 页面被判定为图片密集（is_image_heavy=true）
    3. 或者用户在消息中明确问了"页面上有什么"、"看到了什么"
    
    流程：
    1. page.screenshot() 截图为 PNG bytes
    2. 将 PNG 转为 base64
    3. 构建 Responses API 格式的 vision 消息：
       [
         {"type": "input_image", "image_url": "data:image/png;base64,...", "detail": "auto"},
         {"type": "input_text", "text": "描述这个网页页面上的主要视觉内容..."}
       ]
    4. 通过 LLMRouter.complete(slot="browser_vision", ...) 调用
    5. 返回视觉描述文本
    
    成本控制：
    - 截图分辨率降到 1280x720（减少图片 token）
    - 提示词要求"简洁描述，不超过 300 字"
    - 结果缓存 30 秒（同一页面短时间内多次操作不重复调用）
    """
```

### 15.3 图片密集度判断：`_should_use_vision()`

```python
def _should_use_vision(self, page_metrics: dict) -> bool:
    """
    判断当前页面是否需要视觉理解。
    
    page_metrics 由 DOMProcessor 在提取元素时顺带计算：
    - img_count: 页面中 <img> 元素数量
    - img_with_alt_count: 有 alt 文本的 <img> 数量
    - text_node_char_count: 所有文本节点的总字符数
    - canvas_count: <canvas> 元素数量
    
    判定逻辑：
    - img_count >= 5 且 img_with_alt_count / img_count < 0.3 → 图片多且没 alt，需要视觉
    - text_node_char_count < 500 且 img_count >= 3 → 文字少图片多，需要视觉
    - canvas_count > 0 → 有 canvas 渲染内容，需要视觉
    - 其他 → 不需要，纯 DOM 足够
    """
```

### 15.4 视觉描述的 Prompt 设计

```
prompts/browser_vision_describe.md
```

```markdown
你正在帮助一个人浏览网页。请用中文简洁描述这个页面上的主要视觉内容。

重点描述：
- 图片内容（什么图、关于什么主题）
- 图片旁边的文字标题、数据（点赞数、价格等）
- 页面的整体布局（瀑布流、列表、卡片等）
- 任何纯文字无法表达的视觉信息

不要描述：
- 页面的 HTML 结构
- 导航栏、页脚等通用元素
- 你不确定的内容

限制在 300 字以内。
```

### 15.5 配置项

```python
# settings.py 新增
BROWSER_VISION_ENABLED: bool = True              # 视觉理解总开关
BROWSER_VISION_SLOT: str = "browser_vision"       # LLMRouter slot 名
BROWSER_VISION_MAX_DESCRIPTION_CHARS: int = 500   # 视觉描述最大字符数
BROWSER_VISION_CACHE_TTL_SECONDS: int = 30        # 缓存有效期
BROWSER_VISION_SCREENSHOT_WIDTH: int = 1280       # 截图宽度（降低成本）
BROWSER_VISION_SCREENSHOT_HEIGHT: int = 720       # 截图高度
BROWSER_VISION_IMG_THRESHOLD: int = 5             # 图片数量阈值
BROWSER_VISION_ALT_RATIO_THRESHOLD: float = 0.3   # alt 覆盖率阈值
```

### 15.6 测试

新增测试文件：`tests/core/test_browser_vision.py`

- `test_image_heavy_detection` — 图片密集页面正确识别
- `test_text_heavy_no_vision` — 文字为主页面不调视觉
- `test_vision_describe_called` — 视觉模型被正确调用
- `test_vision_cache` — 30 秒内不重复调用
- `test_vision_disabled` — 关闭开关后不调用
- `test_vision_fallback_on_error` — 视觉模型调用失败时退回纯 DOM

---

## 16. 历史 PageState 压缩

### 16.1 问题

浏览器操作是多轮 tool call 链路。每轮返回一个 PageState（~2000-3000 token），5 轮就是 10-15K token 的 tool result 堆积在上下文里。对于 200K context 的模型问题不大，但如果未来换到小 context 模型，或者操作链路很长（10+ 步），会显著挤压可用上下文。

### 16.2 方案

在 `TaskRuntime` 的 tool loop 中，当 tool result 来自浏览器工具时，对历史 PageState 做压缩：

```python
# task_runtime.py 中 tool loop 的 messages 构建逻辑
def _compress_browser_history(self, messages: list[dict]) -> list[dict]:
    """
    保留最近一次 PageState 的完整内容，
    之前的 PageState 替换为摘要（仅 URL + 操作 + 结果）。
    
    压缩前：
      tool_result: "[页面] Google 搜索\nURL: ...\n可交互元素：[1]...[2]...\n页面内容：..."
      tool_result: "[页面] 搜索结果\nURL: ...\n可交互元素：[1]...[20]...\n页面内容：..."
      tool_result: "[页面] 某篇文章\nURL: ...\n可交互元素：[1]...[15]...\n页面内容：..."
    
    压缩后：
      tool_result: "[已浏览] Google 搜索 → 搜索结果"
      tool_result: "[已浏览] 搜索结果 → 点击第 3 条结果"
      tool_result: "[页面] 某篇文章\nURL: ...\n（完整 PageState）"
    
    触发条件：浏览器 tool result 累计超过 3 条。
    """
```

### 16.3 实现位置

在 `TaskRuntime.complete_chat()` 的 tool loop 中，每次构建下一轮的 messages 时调用。不需要修改 Brain 或 LLMRouter。

---

## 17. 模型兼容性保障

### 17.1 设计原则

蓝图中所有浏览器工具在 ToolRegistry 层注册，使用 Lapwing 内部的 ToolSpec 格式。与底层 LLM 协议（OpenAI Chat Completions / Anthropic / OpenAI Responses API）完全解耦。协议转换由 LLMRouter 负责。

### 17.2 已验证兼容的模型

| 模型 | API 协议 | 视觉 | 主对话 | 浏览器视觉 |
|------|---------|------|--------|-----------|
| MiniMax M2.7 | Anthropic | ❌ | ✅ **当前** | ❌ |
| gpt-5.4 | Responses API | ✅ 原生 computer use | — | ✅ **当前** |
| MiMo-V2-Omni | OpenAI | ✅ | 备选 | 备选 |
| MiMo-V2-Pro | OpenAI | ❌ | 备选 | ❌ |
| GLM-5.1 | OpenAI | ❌ | 备选 | ❌ |
| GLM-5V-Turbo | OpenAI | ✅ GUI Agent 专精 | — | 备选 |
| GLM-5-Turbo | OpenAI | ❌ | 备选 | ❌ |
| Qwen3.5 | OpenAI | ✅ | 备选 | 备选 |
| gpt-5.3-codex | Responses API | ❌ | 备选（需 phase 参数） | ❌ |

### 17.3 切换模型不需要改代码

- 主对话模型切换：修改 `model_routing.json` 中 `main_conversation` slot
- 视觉模型切换：修改 `model_routing.json` 中 `browser_vision` slot
- 关闭视觉：设置 `BROWSER_VISION_ENABLED=false`
- 单模型方案（如全用 Omni）：两个 slot 指向同一个 provider + model

### 17.4 gpt-5.3-codex 特别注意

如果主对话模型切到 gpt-5.3-codex，需要确认 LLMRouter 的 Codex Runtime 通路已正确实现 `phase` 参数。这影响所有多轮 tool call（不限于浏览器），优先级高于浏览器蓝图本身。

---

## 18. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| Chromium 内存泄漏 | 长期运行后 OOM | SystemHealthAction 监控内存，超阈值时重启浏览器 |
| 网站反自动化检测 | 被 ban IP、出 CAPTCHA | Phase 1 不解决；Phase 2 考虑 stealth 插件 |
| LLM 在 tool loop 中死循环点击 | token 浪费、操作混乱 | 现有 LoopDetection 机制覆盖浏览器工具 |
| 凭据泄露 | 安全事故 | Fernet 加密 + 明文永不进入 LLM 上下文 |
| 页面太复杂，元素太多 | LLM 上下文溢出 | BROWSER_MAX_ELEMENT_COUNT 截断 + 历史 PageState 压缩 |
| Playwright 版本升级不兼容 | 构建失败 | 固定大版本号 `>=1.49,<2.0` |
| 持久化 profile 损坏 | 登录态全丢 | storage_state.json 冗余备份 + 可从备份恢复 |
| 意外导航到恶意页面 | XSS/钓鱼 | BrowserGuard URL 检查 + 沙箱环境 |
| Codex 额度耗尽或 OAuth 过期 | 图片密集页面无法视觉理解 | 退回纯 DOM 方案 + 告知用户"这个页面图片多，我只能看到文字部分" |
| 主模型和视觉模型双请求延迟叠加 | 浏览器操作变慢 | 视觉调用异步，非图片密集页面不触发 |
| 换主模型后多轮 tool call 不稳定 | 浏览器操作中断 | tool description 中明确操作规范，与模型能力无关 |

---

## 19. 验收标准

实现完成后，以下场景应能端到端工作：

1. **基础导航**：`"帮我打开 GitHub"` → Lapwing 打开 github.com，返回页面描述
2. **搜索操作**：`"去 Google 搜一下 Playwright Python 教程"` → 打开 Google，输入关键词，回车，返回搜索结果
3. **登录流程**：`"登录我的 GitHub"` → 自动填入凭据，处理登录，返回登录后页面
4. **多步交互**：`"去淘宝看看 xxx 的价格"` → 打开淘宝，搜索，浏览商品，提取价格信息
5. **安全拦截**：`"帮我点那个删除按钮"` → BrowserGuard 拦截，要求用户确认
6. **截图分享**：`"截个图让我看看现在页面什么样"` → 截图并通过 send_image 发送
7. **持久化**：重启 Lapwing 后，之前登录过的网站仍然保持登录态
8. **错误恢复**：网页打不开、元素找不到等情况下，Lapwing 用自然语言告诉用户发生了什么，而不是报错
9. **图片密集页面**：`"帮我看看小红书上有什么好吃的"` → 打开小红书，视觉模型描述图片内容，Lapwing 用自然语言告诉用户看到了什么
10. **视觉不可用时**：Codex 额度耗尽或 OAuth 过期 → 退回纯 DOM，Lapwing 说"这个页面图片比较多，我只能看到文字部分"
11. **模型切换**：修改 `model_routing.json` 中的主对话模型 → 浏览器功能照常工作，无需改代码