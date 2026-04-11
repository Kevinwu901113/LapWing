# Lapwing 桌面端实现蓝图

> 本文档是 Lapwing Windows 桌面应用的完整实现蓝图。
> 每个模块自包含，可独立交给 Claude Code 执行。
> 输入依据：`lapwing_desktop_requirements.md`
> 服务器端参考：`Lapwing_代码实现文档_完整版.md`

---

## 总览

### 项目位置

在 Lapwing 主仓库中新建 `desktop-v2/` 目录，与现有 `desktop/`（旧 Tauri 前端）并行存在，互不干扰。

```
lapwing/
├── src/                  # 服务器端 Python（不动）
├── desktop/              # 旧 Tauri 前端（不动，不删）
├── desktop-v2/           # ← 新桌面端
│   ├── src-tauri/        # Rust 侧（Tauri + 系统感知）
│   │   ├── src/
│   │   │   ├── main.rs
│   │   │   ├── lib.rs
│   │   │   ├── tray.rs           # 系统托盘
│   │   │   ├── hotkey.rs         # 全局快捷键
│   │   │   ├── autostart.rs      # 开机自启
│   │   │   ├── sensing/          # 系统感知模块
│   │   │   │   ├── mod.rs
│   │   │   │   ├── window_monitor.rs    # 前台窗口监控
│   │   │   │   ├── process_detector.rs  # 进程检测/游戏检测
│   │   │   │   ├── session_events.rs    # 锁屏/解锁/开关机
│   │   │   │   ├── clipboard.rs         # 剪贴板监听
│   │   │   │   ├── file_watcher.rs      # 文件系统变化
│   │   │   │   ├── aggregator.rs        # 数据聚合器
│   │   │   │   └── db.rs               # 本地 SQLite
│   │   │   ├── silence.rs        # 游戏静默模式
│   │   │   ├── updater.rs        # 自动更新
│   │   │   └── commands.rs       # Tauri command 桥接层
│   │   ├── Cargo.toml
│   │   ├── tauri.conf.json
│   │   └── icons/
│   ├── src/              # React 前端
│   │   ├── main.tsx
│   │   ├── App.tsx
│   │   ├── router.tsx
│   │   ├── theme/                # 主题系统（风格 E）
│   │   │   ├── tokens.css        # CSS 变量 / Tailwind 配置
│   │   │   └── globals.css
│   │   ├── stores/               # Zustand stores
│   │   │   ├── chat.ts
│   │   │   ├── server.ts
│   │   │   ├── sensing.ts
│   │   │   ├── tasks.ts
│   │   │   ├── memory.ts
│   │   │   └── notifications.ts
│   │   ├── hooks/                # React hooks
│   │   │   ├── useWebSocket.ts
│   │   │   ├── useSSE.ts
│   │   │   ├── useServerStatus.ts
│   │   │   ├── useSensing.ts
│   │   │   └── useNotification.ts
│   │   ├── pages/                # 8 个页面
│   │   │   ├── ChatPage.tsx
│   │   │   ├── TaskCenterPage.tsx
│   │   │   ├── DashboardPage.tsx
│   │   │   ├── SensingPage.tsx
│   │   │   ├── MemoryPage.tsx
│   │   │   ├── PersonaPage.tsx
│   │   │   ├── ModelRoutingPage.tsx
│   │   │   └── SettingsPage.tsx
│   │   ├── components/           # UI 组件
│   │   │   ├── layout/
│   │   │   │   ├── AppShell.tsx
│   │   │   │   ├── Sidebar.tsx
│   │   │   │   └── StatusBar.tsx
│   │   │   ├── chat/
│   │   │   │   ├── MessageBubble.tsx
│   │   │   │   ├── MessageInput.tsx
│   │   │   │   ├── MessageList.tsx
│   │   │   │   └── ToolCallIndicator.tsx
│   │   │   ├── dashboard/
│   │   │   │   ├── MetricCard.tsx
│   │   │   │   ├── ResourceRing.tsx
│   │   │   │   ├── ChannelStatus.tsx
│   │   │   │   ├── HeartbeatStatus.tsx
│   │   │   │   ├── CalendarView.tsx
│   │   │   │   └── ReminderList.tsx
│   │   │   ├── tasks/
│   │   │   │   ├── TaskFlowCard.tsx
│   │   │   │   ├── AgentExecution.tsx
│   │   │   │   └── ToolCallDetail.tsx
│   │   │   ├── sensing/
│   │   │   │   ├── AppTimeline.tsx
│   │   │   │   ├── UsageStats.tsx
│   │   │   │   └── SilenceIndicator.tsx
│   │   │   ├── memory/
│   │   │   │   ├── MemoryItem.tsx
│   │   │   │   ├── MemoryEditor.tsx
│   │   │   │   └── InterestGraph.tsx
│   │   │   ├── persona/
│   │   │   │   ├── FileEditor.tsx
│   │   │   │   ├── EvolutionTimeline.tsx
│   │   │   │   └── SkillManager.tsx
│   │   │   ├── model-routing/
│   │   │   │   ├── ProviderCard.tsx
│   │   │   │   ├── SlotCard.tsx
│   │   │   │   └── ModelTester.tsx
│   │   │   └── shared/
│   │   │       ├── SearchInput.tsx
│   │   │       ├── Pagination.tsx
│   │   │       ├── TabGroup.tsx
│   │   │       └── ConfirmDialog.tsx
│   │   ├── lib/                  # 工具库
│   │   │   ├── api.ts            # 服务器 REST API 客户端
│   │   │   ├── ws.ts             # WebSocket 管理
│   │   │   ├── sse.ts            # SSE 管理
│   │   │   ├── sensing.ts        # Tauri command 调用封装
│   │   │   └── sound.ts          # 提示音
│   │   └── types/                # TypeScript 类型
│   │       ├── api.ts
│   │       ├── chat.ts
│   │       ├── sensing.ts
│   │       └── tasks.ts
│   ├── package.json
│   ├── tsconfig.json
│   ├── tailwind.config.ts
│   ├── vite.config.ts
│   └── components.json          # shadcn/ui 配置
```

### 服务器端需要的改动

桌面端主要消费现有 API，但需要以下新增端点：

| 新端点 | 方法 | 用途 | 模块 |
|--------|------|------|------|
| `/api/sensing/context` | POST | 接收桌面端推送的环境摘要 | M-SERVER-1 |
| `/api/chat/history` | GET | 获取统一对话历史（跨通道） | M-SERVER-2 |
| `/api/model-routing/test` | POST | 发送测试消息到指定模型 | M-SERVER-3 |
| `/api/skills` | GET | 获取技能列表（插件+经验） | M-SERVER-4 |
| `/api/skills/{id}/toggle` | POST | 启用/禁用技能 | M-SERVER-4 |
| `/api/memory/edit` | POST | 编辑记忆条目内容 | M-SERVER-5 |
| `/api/knowledge/notes/{topic}` | PUT | 编辑知识笔记 | M-SERVER-5 |

现有 chat_id 统一方案：桌面端和 QQ 私聊使用同一个 chat_id（基于 OWNER user_id），在 DesktopAdapter 中复用 QQ 私聊的 chat_id 而不是生成独立的 `desktop_xxx`。

---

## M01：项目脚手架

### 目标
初始化 Tauri v2 + React + TypeScript + Vite 项目，配置所有依赖。

### 步骤

1. **在 `lapwing/desktop-v2/` 下创建 Tauri v2 项目**
```bash
cd lapwing
npm create tauri-app@latest desktop-v2 -- --template react-ts
cd desktop-v2
```

2. **安装前端依赖**
```bash
npm install zustand react-router-dom recharts @codemirror/state @codemirror/view @codemirror/lang-markdown
npm install -D tailwindcss @tailwindcss/vite
npx shadcn@latest init
```

shadcn/ui 初始化配置：
- Style: New York
- Base color: Slate
- CSS variables: Yes

安装需要的 shadcn 组件：
```bash
npx shadcn@latest add button input textarea select tabs dialog dropdown-menu toast scroll-area badge separator tooltip popover command switch slider
```

3. **Tailwind 配置** (`tailwind.config.ts`)

扩展默认主题，注入风格 E 的色彩系统：

```typescript
import type { Config } from "tailwindcss";

export default {
  darkMode: "class",
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // 风格 E 深空色系
        void: {
          DEFAULT: "#0c0c0c",
          50: "#1a1a1a",
          100: "#141414",
          200: "#111111",
          300: "#0c0c0c",
        },
        lapwing: {
          // Lapwing 蓝白渐变色
          light: "#e0eaff",
          DEFAULT: "#a8c4f0",
          dark: "#7ba4e0",
          muted: "rgba(100,160,240,0.1)",
          border: "rgba(100,160,240,0.12)",
        },
        surface: {
          DEFAULT: "rgba(255,255,255,0.04)",
          hover: "rgba(255,255,255,0.06)",
          active: "rgba(255,255,255,0.08)",
          border: "rgba(255,255,255,0.06)",
        },
        text: {
          primary: "#c8cdd8",
          secondary: "rgba(255,255,255,0.35)",
          muted: "rgba(255,255,255,0.2)",
          accent: "#e4e8f0",
        },
      },
      fontFamily: {
        sans: ['"Inter"', '"Microsoft YaHei"', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', '"Cascadia Code"', 'monospace'],
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
} satisfies Config;
```

4. **Tauri 配置** (`src-tauri/tauri.conf.json`)

```json
{
  "productName": "Lapwing",
  "version": "0.1.0",
  "identifier": "com.lapwing.desktop",
  "build": {
    "frontendDist": "../dist",
    "devUrl": "http://localhost:1420",
    "beforeDevCommand": "npm run dev",
    "beforeBuildCommand": "npm run build"
  },
  "app": {
    "withGlobalTauri": true,
    "windows": [
      {
        "title": "Lapwing",
        "width": 1200,
        "height": 800,
        "minWidth": 900,
        "minHeight": 600,
        "decorations": true,
        "transparent": false,
        "visible": true,
        "center": true
      }
    ],
    "security": {
      "csp": null
    }
  },
  "plugins": {
    "updater": {
      "endpoints": ["https://releases.lapwing.app/{{target}}/{{arch}}/{{current_version}}"],
      "pubkey": ""
    }
  }
}
```

5. **Rust 依赖** (`src-tauri/Cargo.toml`) 追加：

```toml
[dependencies]
tauri = { version = "2", features = ["tray-icon", "devtools"] }
tauri-plugin-autostart = "2"
tauri-plugin-updater = "2"
tauri-plugin-notification = "2"
serde = { version = "1", features = ["derive"] }
serde_json = "1"
tokio = { version = "1", features = ["full"] }
rusqlite = { version = "0.31", features = ["bundled"] }
windows = { version = "0.58", features = [
  "Win32_UI_WindowsAndMessaging",
  "Win32_System_Threading",
  "Win32_System_ProcessStatus",
  "Win32_Foundation",
  "Win32_UI_Input_KeyboardAndMouse",
  "Win32_System_DataExchange",
  "Win32_System_RemoteDesktop",
  "Win32_Storage_FileSystem",
  "Win32_Security",
] }
notify = "6"           # 文件系统监听
chrono = "0.4"
parking_lot = "0.12"
log = "0.4"
reqwest = { version = "0.12", features = ["json"] }
```

6. **验证**：`cd desktop-v2 && npm run tauri dev` 能启动空白窗口。

---

## M02：Rust 系统层 — 系统托盘 + 快捷键 + 开机自启

### 目标
窗口关闭→托盘常驻，全局快捷键呼出/隐藏，开机自启。

### M02-A：系统托盘 (`src-tauri/src/tray.rs`)

```rust
// 功能：
// 1. 创建系统托盘图标（Lapwing 图标）
// 2. 右键菜单：显示/隐藏、退出
// 3. 左键单击：切换窗口显示/隐藏
// 4. 窗口关闭事件拦截：hide 而非 close
// 5. 托盘闪烁：收到新消息时图标闪烁（交替显示正常图标和带红点图标）
// 6. 停止闪烁：窗口获得焦点时停止

// 关键 API：
// - tauri::tray::TrayIconBuilder
// - tauri::menu::Menu / MenuItem
// - app.on_window_event(|event| match event.event() { WindowEvent::CloseRequested => ... })
```

### M02-B：全局快捷键 (`src-tauri/src/hotkey.rs`)

```rust
// 功能：
// 1. 注册全局快捷键（默认 Ctrl+Shift+L）
// 2. 按下时切换窗口显示/隐藏
// 3. 如果窗口已显示但不在前台 → 带到前台
// 4. 快捷键可在设置中修改（存储到本地配置文件）

// 实现方式：
// - Windows API: RegisterHotKey / UnregisterHotKey
// - 在独立线程中运行消息循环监听 WM_HOTKEY
// - 通过 Tauri AppHandle 控制窗口
```

### M02-C：开机自启 (`src-tauri/src/autostart.rs`)

```rust
// 使用 tauri-plugin-autostart
// 功能：
// 1. 设置/取消开机自启
// 2. 默认启用
// 3. 前端设置页面可切换

// main.rs 中注册：
// app.plugin(tauri_plugin_autostart::init(
//     MacosLauncher::LaunchAgent,
//     Some(vec!["--minimized"]),
// ))
```

### M02-D：启动参数处理

```rust
// --minimized：开机自启时最小化到托盘，不显示窗口
// 检查 std::env::args() 包含 "--minimized" 时：
// - 创建窗口但不显示（visible: false）
// - 只显示托盘图标
// - 连接服务器
// - 推送"Kevin 开机了"事件
```

---

## M03：Rust 系统层 — 环境感知

### 目标
实现 7 项系统感知能力，数据存本地 SQLite，定期聚合摘要推送服务器。

### M03-A：本地数据库 (`src-tauri/src/sensing/db.rs`)

```sql
-- 感知数据库 schema（存储在 %APPDATA%/Lapwing/sensing.db）

CREATE TABLE window_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,          -- ISO 8601
    process_name TEXT NOT NULL,       -- e.g. "Code.exe"
    window_title TEXT NOT NULL,       -- e.g. "brain.py - lapwing - Visual Studio Code"
    duration_seconds INTEGER DEFAULT 0 -- 该窗口持续时长（由 aggregator 回填）
);

CREATE TABLE app_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,                -- YYYY-MM-DD
    process_name TEXT NOT NULL,
    app_display_name TEXT,             -- 人类可读名称
    total_seconds INTEGER DEFAULT 0,
    category TEXT,                     -- coding / browser / gaming / communication / other
    UNIQUE(date, process_name)
);

CREATE TABLE session_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,          -- boot / shutdown / lock / unlock / game_start / game_end
    detail TEXT                        -- 附加信息（如游戏名称）
);

CREATE TABLE clipboard_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    content_type TEXT NOT NULL,        -- text / image / file
    content_preview TEXT,              -- 前 200 字符预览
    char_count INTEGER
);

CREATE TABLE file_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,          -- create / modify / delete / rename
    path TEXT NOT NULL,
    file_name TEXT NOT NULL
);

-- 索引
CREATE INDEX idx_window_events_ts ON window_events(timestamp);
CREATE INDEX idx_app_usage_date ON app_usage(date);
CREATE INDEX idx_session_events_ts ON session_events(timestamp);
```

### M03-B：前台窗口监控 (`src-tauri/src/sensing/window_monitor.rs`)

```rust
// 功能：
// 1. 每秒调用 GetForegroundWindow + GetWindowText + 进程名
// 2. 与上一次对比，窗口变化时写入 window_events 表
// 3. 旧窗口的 duration_seconds 回填
// 4. 更新 app_usage 表的当日累计

// Windows API：
// - GetForegroundWindow → HWND
// - GetWindowTextW → 窗口标题
// - GetWindowThreadProcessId → PID
// - OpenProcess + QueryFullProcessImageNameW → 进程路径和名称

// 进程名到 app_display_name 的映射：
// - 内置映射表：Code.exe → "VS Code", chrome.exe → "Chrome", ...
// - 未知进程使用进程名去掉 .exe

// 分类规则（category）：
// - coding: Code.exe, idea64.exe, pycharm64.exe, ...
// - browser: chrome.exe, firefox.exe, msedge.exe, ...
// - gaming: 由 process_detector 标记
// - communication: WeChat.exe, QQ.exe, Telegram.exe, Discord.exe, ...
// - other: 其他
```

### M03-C：游戏进程检测 (`src-tauri/src/sensing/process_detector.rs`)

```rust
// 功能：
// 1. 维护已知游戏进程列表（内置 + 用户自定义）
// 2. 检测全屏独占模式
// 3. 状态变化时触发事件

// 内置游戏进程列表：
const KNOWN_GAMES: &[&str] = &[
    "cs2.exe", "csgo.exe",
    "valorant.exe", "VALORANT-Win64-Shipping.exe",
    "dota2.exe",
    "LeagueClient.exe", "League of Legends.exe",
    "GenshinImpact.exe", "YuanShen.exe",
    "steamwebhelper.exe", // 不算游戏，但 Steam overlay
    // ... 更多
];

// 反作弊相关进程（检测到时标记为游戏模式）：
const ANTICHEAT_PROCESSES: &[&str] = &[
    "vgc.exe",              // Vanguard
    "EasyAntiCheat.exe",
    "BEService.exe",        // BattlEye
    "5EClient.exe",         // 5E 对战平台
    "PerfectWorld.exe",     // 完美世界
];

// 用户自定义列表存储在 %APPDATA%/Lapwing/game_list.json

// 全屏独占检测：
// - SHQueryUserNotificationState → QUNS_BUSY 表示全屏
// - 或检查前台窗口尺寸是否等于屏幕尺寸
```

### M03-D：静默模式控制 (`src-tauri/src/silence.rs`)

```rust
// 功能：
// 1. 接收 process_detector 的游戏状态变化事件
// 2. 进入静默模式时：
//    a. 暂停 window_monitor 采集
//    b. 暂停 clipboard 监听
//    c. 暂停 file_watcher
//    d. 隐藏窗口到托盘（如果当前可见）
//    e. 通知服务器 {"event": "game_start", "game": "cs2.exe"}
//    f. 记录 session_events
// 3. 退出静默模式时：
//    a. 恢复所有采集
//    b. 通知服务器 {"event": "game_end", "game": "cs2.exe", "duration_minutes": 120}
//    c. 服务器侧 Lapwing 可据此主动发消息

// 状态机：
// Normal → Gaming（检测到游戏进程）
// Gaming → Normal（游戏进程退出）
// Normal → Silenced（用户手动免打扰 — 未来功能）
```

### M03-E：锁屏/解锁/开关机 (`src-tauri/src/sensing/session_events.rs`)

```rust
// 功能：
// 1. 监听 Windows 会话事件
// 2. 记录到 session_events 表
// 3. 推送到服务器

// Windows API：
// - WTSRegisterSessionNotification(hwnd, NOTIFY_FOR_THIS_SESSION)
// - 消息循环中处理 WM_WTSSESSION_CHANGE：
//   - WTS_SESSION_LOCK → 记录 "lock"
//   - WTS_SESSION_UNLOCK → 记录 "unlock"
// - WM_QUERYENDSESSION → 记录 "shutdown"
// - 程序启动时 → 记录 "boot"

// 推送逻辑：
// - boot: 立即推送服务器（触发 Lapwing "早上好"之类的主动消息）
// - unlock: 如果距上次 lock > 5 分钟，推送（短暂锁屏不推送）
// - shutdown: 尽力推送（WM_QUERYENDSESSION 有时间限制）
```

### M03-F：剪贴板监听 (`src-tauri/src/sensing/clipboard.rs`)

```rust
// 功能：
// 1. 监听剪贴板内容变化
// 2. 仅记录文本类型（不记录图片/文件的内容，只记录类型）
// 3. 文本内容截取前 200 字符存入 clipboard_history
// 4. 不在游戏静默模式下运行

// Windows API：
// - AddClipboardFormatListener(hwnd)
// - WM_CLIPBOARDUPDATE 消息处理
// - GetClipboardData(CF_UNICODETEXT)

// 隐私考量：
// - 密码类内容过滤（检测来自密码管理器的进程）
// - 剪贴板历史保留最近 500 条，超出自动清理
// - 不推送剪贴板原始内容到服务器，仅在环境摘要中提及"用户最近复制了一段代码"之类的概述
```

### M03-G：文件系统监听 (`src-tauri/src/sensing/file_watcher.rs`)

```rust
// 功能：
// 1. 监听用户指定目录的文件变化（默认：桌面、文档、下载）
// 2. 记录文件创建/修改/删除/重命名事件
// 3. 不监听系统目录、临时文件、隐藏文件
// 4. 不在游戏静默模式下运行

// 使用 notify crate：
// - RecommendedWatcher 跨平台文件监听
// - 事件类型映射：Create / Modify / Remove / Rename

// 过滤规则：
// - 忽略 .tmp, .swp, ~$, .git/, node_modules/, __pycache__/
// - 忽略系统生成的 thumbs.db, desktop.ini
// - 仅记录用户可感知的文件变化

// 监听目录配置存储在 %APPDATA%/Lapwing/config.json
```

### M03-H：数据聚合器 (`src-tauri/src/sensing/aggregator.rs`)

```rust
// 功能：
// 1. 每 3 分钟运行一次
// 2. 从本地 SQLite 读取最近 3 分钟的原始数据
// 3. 聚合为环境摘要文本（< 100 tokens）
// 4. 通过 HTTP POST 推送到服务器 /api/sensing/context

// 摘要格式示例：
// "Kevin 过去 3 分钟在 VS Code 中编辑 brain.py（已持续 47 分钟）。
//  今日应用使用：VS Code 2h15m, Chrome 45m, 微信 12m。
//  剪贴板最近复制了一段 Python 代码。"

// 或游戏模式时：
// "Kevin 正在玩 CS2（已持续 1h30m），免打扰模式中。"

// 聚合逻辑：
// 1. 当前前台应用 + 窗口标题 + 该应用今日累计时长
// 2. 今日 top 5 应用使用时长
// 3. 最近一次有意义的剪贴板事件（如果有）
// 4. 最近一次有意义的文件事件（如果有）
// 5. 当前状态（正常 / 游戏中 / 锁屏 / 刚开机）

// HTTP 推送：
// POST /api/sensing/context
// Body: { "summary": "...", "state": "normal|gaming|locked", "timestamp": "..." }
```

---

## M04：Rust 系统层 — Tauri Command 桥接

### 目标
暴露 Rust 侧功能给前端 React 调用。

### `src-tauri/src/commands.rs`

```rust
// 所有 #[tauri::command] 定义在此

// === 感知数据查询（前端环境感知页面使用）===

#[tauri::command]
async fn get_app_usage_today() -> Result<Vec<AppUsageRecord>, String>
// 返回今日各应用使用时长，按时长降序

#[tauri::command]
async fn get_app_timeline(date: String) -> Result<Vec<WindowEvent>, String>
// 返回指定日期的窗口切换时间线

#[tauri::command]
async fn get_session_events(limit: u32) -> Result<Vec<SessionEvent>, String>
// 返回最近的会话事件（开关机、锁屏等）

#[tauri::command]
async fn get_silence_state() -> Result<SilenceState, String>
// 返回当前静默模式状态

#[tauri::command]
async fn add_game_process(process_name: String) -> Result<(), String>
// 添加自定义游戏进程到免打扰列表

#[tauri::command]
async fn remove_game_process(process_name: String) -> Result<(), String>
// 从免打扰列表移除

#[tauri::command]
async fn get_game_list() -> Result<Vec<String>, String>
// 获取完整的游戏进程列表（内置+自定义）

// === 配置 ===

#[tauri::command]
async fn get_local_config() -> Result<LocalConfig, String>
// 读取本地配置（服务器地址、快捷键、监听目录等）

#[tauri::command]
async fn update_local_config(config: LocalConfig) -> Result<(), String>
// 更新本地配置

#[tauri::command]
async fn get_hotkey() -> Result<String, String>
// 获取当前快捷键

#[tauri::command]
async fn set_hotkey(keys: String) -> Result<(), String>
// 设置新快捷键（自动注销旧的，注册新的）

// === 声音 ===

#[tauri::command]
async fn play_notification_sound() -> Result<(), String>
// 播放通知提示音（从本地音频文件）
```

### 本地配置文件 (`%APPDATA%/Lapwing/config.json`)

```json
{
  "server_url": "http://your-pve-server:8000",
  "hotkey": "Ctrl+Shift+L",
  "autostart": true,
  "watch_directories": [
    "C:\\Users\\Kevin\\Desktop",
    "C:\\Users\\Kevin\\Documents",
    "C:\\Users\\Kevin\\Downloads"
  ],
  "notification_sound": "default.wav",
  "aggregator_interval_seconds": 180,
  "clipboard_max_entries": 500,
  "silence_restore_delay_seconds": 5
}
```

---

## M05：前端 — 主题系统 + 布局框架

### 目标
实现风格 E 的完整主题系统和 AppShell 布局。

### M05-A：全局样式 (`src/theme/globals.css`)

```css
/* 强制深色模式 */
:root {
  color-scheme: dark;
}

body {
  background: #0c0c0c;
  color: #c8cdd8;
  font-family: 'Inter', 'Microsoft YaHei', system-ui, sans-serif;
  margin: 0;
  overflow: hidden; /* 防止全局滚动，各页面自己管理 */
  -webkit-font-smoothing: antialiased;
}

/* shadcn/ui 深色覆盖 — 用 Tailwind 配置中的色值 */
/* 详细的 CSS 变量覆盖在 tailwind.config.ts 的 theme.extend 中 */

/* 滚动条样式 */
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: rgba(255,255,255,0.2); }

/* 选中文字颜色 */
::selection { background: rgba(168,196,240,0.3); }
```

### M05-B：AppShell (`src/components/layout/AppShell.tsx`)

```
┌──────────────────────────────────────────────────┐
│ AppShell                                          │
│ ┌──────────┬────────────────────────────────────┐ │
│ │ Sidebar  │  Page Content (via <Outlet />)     │ │
│ │ 240px    │                                    │ │
│ │          │                                    │ │
│ │ - Avatar │                                    │ │
│ │ - Status │                                    │ │
│ │ - Nav    │                                    │ │
│ │ - ...    │                                    │ │
│ │          │                                    │ │
│ │ StatusBar│                                    │ │
│ └──────────┴────────────────────────────────────┘ │
└──────────────────────────────────────────────────┘
```

### M05-C：Sidebar (`src/components/layout/Sidebar.tsx`)

结构（参照风格 E mockup）：

1. **头部**：Lapwing 头像（蓝白渐变圆形 + "L"字）+ 名字 + 在线状态指示灯
2. **实时状态卡片**：当前感知状态的摘要（"Kevin 在 VS Code 中工作，已持续 47 分钟"），或游戏模式（"Kevin 在玩 CS2"）。数据来自 Zustand sensing store。
3. **导航列表**：8 个页面，图标 + 文字。当前页高亮（`surface-hover` 背景）。图标使用 Lucide React。
   - 对话 (MessageSquare)
   - 任务中心 (Activity)
   - 仪表盘 (LayoutDashboard)
   - 环境感知 (Eye)
   - 记忆 (Brain)
   - 人格 (Pen)
   - 模型路由 (GitBranch)
   - 设置 (Settings)
4. **底部状态栏**：CPU / RAM / 运行时长，单行显示，12px 文字。数据来自 `/api/system/stats` 轮询（30 秒间隔）。

### M05-D：路由 (`src/router.tsx`)

```tsx
const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <Navigate to="/chat" replace /> },
      { path: "chat", element: <ChatPage /> },
      { path: "tasks", element: <TaskCenterPage /> },
      { path: "dashboard", element: <DashboardPage /> },
      { path: "sensing", element: <SensingPage /> },
      { path: "memory", element: <MemoryPage /> },
      { path: "persona", element: <PersonaPage /> },
      { path: "model-routing", element: <ModelRoutingPage /> },
      { path: "settings", element: <SettingsPage /> },
    ],
  },
]);
```

---

## M06：前端 — 聊天页面

### 目标
主力聊天入口，消息质量要做到最好。

### M06-A：WebSocket 连接 (`src/hooks/useWebSocket.ts`)

```typescript
// 功能：
// 1. 连接 ws://{server}/ws/chat
// 2. 自动重连（指数退避：1s, 2s, 4s, 8s, 最大 30s）
// 3. 连接状态管理（connecting / connected / disconnected / error）
// 4. 发送消息时带统一的 chat_id（与 QQ 私聊相同）
// 5. 接收消息写入 Zustand chat store
// 6. 心跳 ping（每 30 秒，防止连接超时断开）

// chat_id 策略：
// 从服务器 /api/status 获取 OWNER 的 QQ user_id 作为 chat_id
// 这样桌面端和 QQ 共享同一对话流
```

### M06-B：聊天历史加载

```typescript
// 页面打开时：
// 1. GET /api/chat/history?chat_id={id}&limit=50 获取最近 50 条历史
// 2. 渲染到 MessageList
// 3. 滚动到底部
// 4. 向上滚动时加载更多（分页）

// 注：这是新增 API，需要服务器端实现（M-SERVER-2）
// 它返回的是跨通道的统一历史——包含 QQ 上发的和桌面端发的
```

### M06-C：MessageList (`src/components/chat/MessageList.tsx`)

```
- 虚拟滚动（消息量大时性能保障）
- Lapwing 消息：左侧头像（蓝白渐变）+ 气泡（surface 背景 + surface-border 边框）
- 用户消息：右对齐 + 气泡（lapwing-muted 背景 + lapwing-border 边框）
- 时间戳：消息间隔 > 5 分钟时显示时间分隔线
- [SPLIT] 处理：一条 assistant 回复如果包含 [SPLIT]，拆分为多个连续气泡
- 工具调用指示器：Lapwing 在调用工具时显示 ToolCallIndicator（工具名 + 转圈动画）
- 图片消息：内联显示，点击放大
```

### M06-D：MessageInput (`src/components/chat/MessageInput.tsx`)

```
- 多行输入框，Shift+Enter 换行，Enter 发送
- 发送按钮（蓝白渐变圆角矩形）
- 发送时通过 WebSocket 发出 {"type": "message", "content": "...", "chat_id": "..."}
- 输入时显示 typing 状态（可选，通过 WebSocket 发 typing 事件）
- 粘贴图片支持（转为 base64 发送 — 远期）
```

### M06-E：通知整合

```
- WebSocket 收到新消息时：
  1. 如果窗口在前台 → 直接显示，不通知
  2. 如果窗口在后台 → 播放提示音 + 托盘图标闪烁
  3. 如果窗口隐藏 → 同上
- 点击托盘图标 / 使用快捷键呼出窗口后 → 停止闪烁
```

---

## M07：前端 — 任务中心页面

### 目标
独立页面观察 Agent 执行过程。

### 数据来源
- SSE `/api/events/stream`：接收 `task.*` 事件
- GET `/api/tasks`：获取当前任务列表
- GET `/api/task-flows`：获取任务流列表

### 页面布局

```
┌──────────────────────────────────────────┐
│ 任务中心                                  │
│ ┌──────────────────┬────────────────────┐│
│ │ 任务列表         │ 执行详情           ││
│ │                  │                    ││
│ │ [TaskFlowCard]   │ Agent 执行时间线   ││
│ │ [TaskFlowCard]   │ - 步骤 1: web_search ││
│ │ [TaskFlowCard]   │   ├ query: "..."   ││
│ │                  │   └ result: "..."  ││
│ │                  │ - 步骤 2: web_fetch ││
│ │                  │   └ ...            ││
│ │                  │                    ││
│ │                  │ [取消任务] 按钮     ││
│ └──────────────────┴────────────────────┘│
└──────────────────────────────────────────┘
```

### 组件

- **TaskFlowCard**：显示任务标题、状态（pending/running/completed/failed）、步骤进度条、耗时
- **AgentExecution**：垂直时间线，每个节点是一次工具调用。展开可看到 tool name、arguments、result
- **ToolCallDetail**：单个工具调用的详情卡片。shell 命令显示 stdout/stderr，web_search 显示结果列表

### SSE 事件处理

```typescript
// 监听 task.* 事件，实时更新 Zustand tasks store
// task.started → 新增任务到列表
// task.executing → 更新当前步骤 + 工具名
// task.tool_execution_start → 展示工具执行中状态
// task.tool_execution_end → 展示结果
// task.completed → 标记完成
// task.failed → 标记失败 + 显示原因
```

---

## M08：前端 — 仪表盘页面

### 目标
一眼看到 Lapwing 和系统的全部状态。

### 数据来源
- GET `/api/status`：基础状态
- GET `/api/system/stats`：系统资源
- GET `/api/system/api-usage`：API 统计
- GET `/api/heartbeat/status`：心跳状态
- GET `/api/memory/health`：记忆健康
- GET `/api/reminders`：活跃提醒
- GET `/api/config/platforms`：通道状态
- GET `/api/learnings`：最近学习
- SSE `/api/events/stream`：实时事件

### 页面布局（响应式网格）

```
┌─────────────────────────────────────────────────┐
│ 仪表盘                                          │
│                                                  │
│ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐│
│ │运行时长  │ │对话轮数  │ │主动消息  │ │API 调用  ││
│ │ 3h22m   │ │ 47      │ │ 3       │ │ 128     ││
│ └─────────┘ └─────────┘ └─────────┘ └─────────┘│
│                                                  │
│ ┌──────────────────┐ ┌──────────────────────────┐│
│ │ 系统资源         │ │ 通道状态                  ││
│ │ CPU [===  ] 12%  │ │ ● 桌面端  已连接          ││
│ │ RAM [====== ] 58%│ │ ● QQ     已连接           ││
│ │ Disk [===  ] 35% │ │ ○ Telegram 已下线         ││
│ └──────────────────┘ └──────────────────────────┘│
│                                                  │
│ ┌──────────────────┐ ┌──────────────────────────┐│
│ │ 心跳引擎         │ │ 记忆系统                  ││
│ │ 上次: 主动消息   │ │ 记忆条目: 156             ││
│ │ 下次: 42min 后   │ │ 日志: 30 天              ││
│ │ 今日自省: ✓      │ │ 知识笔记: 12             ││
│ └──────────────────┘ └──────────────────────────┘│
│                                                  │
│ ┌──────────────────┐ ┌──────────────────────────┐│
│ │ 日历             │ │ 提醒列表                  ││
│ │ [月历视图]       │ │ ○ 下午3点 交文档初稿      ││
│ │                  │ │ ○ 下午4点 导师线上会议     ││
│ │                  │ │ ○ 每日 22:00 复盘         ││
│ └──────────────────┘ └──────────────────────────┘│
│                                                  │
│ ┌──────────────────────────────────────────────┐ │
│ │ 最近活动                                     │ │
│ │ 09:15 web_search "dodgers schedule"          │ │
│ │ 09:12 主动消息：提醒文档初稿                  │ │
│ │ 03:00 自省完成                                │ │
│ └──────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────┘
```

### 组件

- **MetricCard**：数字指标卡片（运行时长、对话轮数等）。surface 背景，大号数字 + 小号标签。
- **ResourceRing**：环形进度条显示 CPU/RAM/Disk。使用 Recharts 的 RadialBarChart 或自定义 SVG。
- **ChannelStatus**：通道连接状态列表。绿点=连接、灰点=断开。断开时显示"重连"按钮。
- **HeartbeatStatus**：心跳引擎状态卡片。上次 action、下次调度时间、今日执行统计。
- **CalendarView**：简易月历。有提醒的日期标记小点。点击日期显示当日提醒。
- **ReminderList**：活跃提醒列表。显示时间、内容、类型标签。支持删除。
- **ActivityFeed**：最近活动时间线。从 SSE 事件流获取。

### 轮询策略

```typescript
// 系统资源：每 30 秒轮询
// 基础状态：每 60 秒轮询
// API 统计：每 60 秒轮询
// 提醒列表：每 60 秒轮询
// 实时事件：SSE 持续连接
```

---

## M09：前端 — 环境感知页面

### 目标
展示应用使用统计和感知时间线。

### 数据来源
纯本地数据，通过 Tauri command 读取。

### 页面布局

```
┌──────────────────────────────────────────┐
│ 环境感知                      [日期选择] │
│                                          │
│ ┌──────────────────────────────────────┐ │
│ │ 当前状态                             │ │
│ │ ● 正常模式 · VS Code · brain.py     │ │
│ └──────────────────────────────────────┘ │
│                                          │
│ ┌──────────────────────────────────────┐ │
│ │ 今日应用使用（横向条形图）           │ │
│ │ VS Code    ████████████████  2h15m   │ │
│ │ Chrome     ████████  1h02m           │ │
│ │ 微信       ███  22m                  │ │
│ │ ...                                  │ │
│ └──────────────────────────────────────┘ │
│                                          │
│ ┌──────────────────────────────────────┐ │
│ │ 时间线（横向，类似 Wakatime）        │ │
│ │ 09:00 ██VS Code███ █Chrome█ ██VS██  │ │
│ │ 12:00 ███微信████ ████Chrome█████    │ │
│ │ ...                                  │ │
│ └──────────────────────────────────────┘ │
│                                          │
│ ┌──────────────────────────────────────┐ │
│ │ 会话事件                             │ │
│ │ 09:00 开机                           │ │
│ │ 12:30 锁屏                           │ │
│ │ 12:45 解锁                           │ │
│ │ 14:00 CS2 开始 → 免打扰              │ │
│ │ 16:00 CS2 结束（2h）                 │ │
│ └──────────────────────────────────────┘ │
│                                          │
│ ┌──────────────────────────────────────┐ │
│ │ 免打扰设置                           │ │
│ │ 游戏进程列表 [+ 添加] [编辑]         │ │
│ └──────────────────────────────────────┘ │
└──────────────────────────────────────────┘
```

### 组件

- **AppTimeline**：WakaTime 风格的横向时间线。每行是一个小时，彩色条表示不同应用。使用自定义 SVG 或 Recharts BarChart。
- **UsageStats**：横向条形图，按使用时长降序排列应用。
- **SilenceIndicator**：免打扰模式指示器 + 游戏进程管理。

---

## M10：前端 — 记忆页面

### 目标
统一管理所有记忆类内容。

### 数据来源
- GET `/api/memory`：记忆条目
- GET `/api/memory/summaries`：对话摘要
- GET `/api/interests`：兴趣图谱
- GET `/api/knowledge/notes`：知识笔记
- GET `/api/learnings`：自省日志
- 新增 POST `/api/memory/edit`：编辑记忆
- 新增 PUT `/api/knowledge/notes/{topic}`：编辑知识笔记

### 页面布局

顶部 Tab 切换：

| Tab | 内容 | CRUD |
|-----|------|------|
| 对话记忆 | KEVIN.md + SELF.md + 对话摘要 | 查看、编辑、删除 |
| 用户画像 | SQLite facts | 查看、删除 |
| 自省日志 | journal/*.md | 查看、删除 |
| 知识笔记 | knowledge/*.md | 查看、编辑、删除 |
| 兴趣图谱 | interests.md + SQLite interests | 查看 |

每个 Tab 通用功能：搜索栏 + 分页 + 列表/卡片切换。

### 编辑体验

- 点击记忆条目 → 右侧展开编辑面板（split view）
- Markdown 内容用 CodeMirror 编辑
- 保存时调用对应 API
- 删除需确认对话框

---

## M11：前端 — 人格页面

### 目标
编辑人格文件 + 查看进化历史 + 管理技能。

### 数据来源
- GET `/api/persona/files`：人格文件列表
- POST `/api/persona/files/{name}`：更新文件
- GET `/api/persona/changelog`：进化日志
- POST `/api/reload`：重载 prompt
- POST `/api/evolve`：手动进化
- 新增 GET `/api/skills`：技能列表
- 新增 POST `/api/skills/{id}/toggle`：启用/禁用

### 页面布局

左右分栏：
- 左侧：文件列表（soul.md, voice.md, examples.md, capabilities.md, constitution.md）
- 右侧：CodeMirror 编辑器（Markdown 语法高亮）
- 底部工具栏：保存 + 重载 prompt + 手动进化

Tab 2：进化历史
- 时间线视图，每个节点是一次进化
- 点击展开 diff 对比（旧内容 vs 新内容，红绿高亮）

Tab 3：技能管理
- 插件技能列表（名称、描述、状态开关）
- 经验技能列表（名称、使用次数、成功率、最后使用时间）

---

## M12：前端 — 模型路由页面

### 数据来源
- GET/PUT/POST/DELETE `/api/model-routing/*`：现有完整 API
- 新增 POST `/api/model-routing/test`：测试消息

### 页面布局

Tab 1：Provider 管理
- 卡片列表，每个 Provider 一张卡（显示名称、base_url、api_type、状态）
- 新增 / 编辑 / 删除 Provider（对话框表单）
- API Key 输入（密码遮罩）

Tab 2：Slot 分配
- 7 个语义 Slot 的卡片（main_conversation, persona_expression, ...）
- 每个 Slot 下拉选择 Provider + Model
- 保存 + 热重载按钮

Tab 3：模型测试
- 输入框（测试消息）
- 下拉选择目标 Slot 或直接指定 Provider + Model
- 发送按钮
- 结果展示区（显示回复内容 + 耗时 + token 数）

---

## M13：前端 — 设置页面

### Tab 结构

1. **通用**
   - 服务器地址（输入框 + 连接测试按钮）
   - 开机自启（开关）
   - 消息提示音（选择 / 测试播放）
   - 数据聚合间隔（滑块，1-10 分钟）

2. **快捷键**
   - 全局呼出快捷键（按键录制器）
   - 显示当前绑定

3. **监听目录**
   - 文件监听的目录列表
   - 添加 / 删除目录

4. **通道**
   - QQ WebSocket URL
   - QQ 群聊设置（冷却时间、关键词）
   - 通道连接状态

5. **Feature Flags**
   - 从 `/api/config/features` 获取
   - 开关列表

6. **日志**
   - 实时日志流（SSE `/api/logs/stream`）
   - 日志级别过滤（DEBUG/INFO/WARNING/ERROR）
   - 搜索

7. **关于**
   - 版本号
   - 检查更新按钮
   - 服务器版本信息

---

## M-SERVER-1：服务器端 — 感知上下文接收

### 目标
接收桌面端推送的环境摘要，注入 SenseContext。

### 实现

在 `src/api/server.py` 中新增端点：

```python
@app.post("/api/sensing/context")
async def receive_sensing_context(body: SensingContextBody):
    """
    接收桌面端推送的环境感知摘要。
    存储在 vitals 或专门的 sensing 模块中，
    供心跳 SenseLayer.build() 读取。
    """
    # body.summary: str — 环境摘要文本
    # body.state: str — "normal" | "gaming" | "locked"
    # body.timestamp: str — ISO 8601
    # body.current_app: Optional[str] — 当前前台应用
    # body.current_title: Optional[str] — 当前窗口标题
    
    # 存储到 vitals 模块的新字段
    vitals.update_desktop_sensing(
        summary=body.summary,
        state=body.state,
        current_app=body.current_app,
    )
    return {"ok": True}
```

在 `src/core/vitals.py` 中新增：

```python
def update_desktop_sensing(self, summary: str, state: str, current_app: str | None):
    self._desktop_sensing = {
        "summary": summary,
        "state": state,
        "current_app": current_app,
        "updated_at": datetime.now(UTC),
    }

def get_desktop_sensing(self) -> dict | None:
    sensing = getattr(self, '_desktop_sensing', None)
    if sensing and (datetime.now(UTC) - sensing["updated_at"]).seconds < 600:
        return sensing
    return None
```

在 `prompt_builder.py` 的 `build_system_prompt()` 中，Layer 0.5（自我感知）后新增 Layer 0.55：

```python
# Layer 0.55: 桌面端环境感知
desktop_sensing = vitals.get_desktop_sensing()
if desktop_sensing:
    sections.append(f"## Kevin 的电脑状态\n{desktop_sensing['summary']}")
```

### 心跳感知整合

在 `SenseLayer.build()` 中将 desktop_sensing 整合进 SenseContext：

```python
desktop = vitals.get_desktop_sensing()
if desktop:
    context.desktop_state = desktop["state"]
    context.desktop_summary = desktop["summary"]
```

心跳 ProactiveMessageAction 和 heartbeat_decision.md 的 prompt 中加入桌面状态信息，让 Lapwing 能基于你的电脑使用状态决定是否主动联系。

### 游戏结束主动回应

桌面端推送 `{"state": "normal", "detail": "game_end:cs2.exe:120min"}` 时，服务器端触发一个特殊的心跳 action（或者直接在接收端点中触发）：

```python
if body.state == "normal" and body.detail and body.detail.startswith("game_end:"):
    game, duration = parse_game_end(body.detail)
    # 触发 Lapwing 主动消息
    await brain.think_conversational(
        system_note=f"Kevin 刚打完 {game}，玩了 {duration} 分钟。自然地回应他。",
        chat_id=owner_chat_id,
        send_fn=channel_manager.send,
    )
```

---

## M-SERVER-2：服务器端 — 统一对话历史

### 目标
提供跨通道的统一对话历史 API。

### 实现

在 `src/api/server.py` 中新增：

```python
@app.get("/api/chat/history")
async def get_chat_history(
    chat_id: str,
    limit: int = 50,
    before: str | None = None,  # ISO 8601，用于分页
):
    """
    获取统一对话历史。
    chat_id 跨通道共享，所以返回的历史包含 QQ 和桌面端的消息。
    """
    messages = await brain.memory.get_messages(
        chat_id=chat_id,
        limit=limit,
        before=before,
    )
    return {"messages": messages, "has_more": len(messages) == limit}
```

需要在 `ConversationMemory` 中新增 `get_messages()` 方法（如果现有 `get()` 不支持分页的话），支持 `before` 参数做游标分页。

### chat_id 统一

修改 `DesktopAdapter`（或 API server 的 `/ws/chat` 处理）：桌面端连接时不再使用 `desktop_xxx` 作为 chat_id，而是使用 `settings.OWNER_IDS` 中的第一个 ID（与 QQ 私聊的 chat_id 一致）。

```python
# 在 /ws/chat 的 WebSocket handler 中
# 原来：chat_id = f"desktop_{connection_id}"
# 改为：chat_id = settings.OWNER_IDS[0] if settings.DESKTOP_DEFAULT_OWNER else f"desktop_{connection_id}"
```

---

## M-SERVER-3：服务器端 — 模型测试

### 实现

```python
@app.post("/api/model-routing/test")
async def test_model(body: ModelTestBody):
    """
    body.message: str — 测试消息
    body.slot: str | None — 指定 slot（如 "main_conversation"）
    body.provider_id: str | None — 直接指定 provider
    body.model: str | None — 直接指定 model
    """
    import time
    start = time.time()
    
    if body.slot:
        reply = await brain.router.complete(
            messages=[{"role": "user", "content": body.message}],
            purpose=body.slot,
        )
    else:
        # 直接指定 provider + model 的逻辑
        reply = await brain.router.complete_with_override(
            messages=[{"role": "user", "content": body.message}],
            provider_id=body.provider_id,
            model=body.model,
        )
    
    elapsed = time.time() - start
    return {
        "reply": reply,
        "elapsed_ms": int(elapsed * 1000),
        "model": body.model or "slot default",
    }
```

---

## M-SERVER-4：服务器端 — 技能管理 API

### 实现

```python
@app.get("/api/skills")
async def get_skills():
    """返回所有技能（插件 + 经验）"""
    plugin_skills = brain.skill_manager.list_skills() if brain.skill_manager else []
    experience_skills = brain.experience_skill_manager.list_skills() if brain.experience_skill_manager else []
    return {
        "plugin_skills": plugin_skills,
        "experience_skills": experience_skills,
    }

@app.post("/api/skills/{skill_id}/toggle")
async def toggle_skill(skill_id: str, body: SkillToggleBody):
    """启用/禁用技能"""
    # body.enabled: bool
    # 根据 skill_id 前缀判断是插件还是经验技能
    # 更新对应 manager 的状态
    return {"ok": True}
```

---

## M-SERVER-5：服务器端 — 记忆编辑 + 知识笔记编辑

### 实现

```python
@app.post("/api/memory/edit")
async def edit_memory(body: MemoryEditBody):
    """
    body.path: str — 文件路径（相对于 data/memory/ 或 data/evolution/）
    body.content: str — 新内容（整个文件覆盖）
    """
    # 安全检查：不允许编辑 data/identity/ 下的文件
    # 写入文件
    return {"ok": True}

@app.put("/api/knowledge/notes/{topic}")
async def edit_knowledge_note(topic: str, body: KnowledgeEditBody):
    """
    body.content: str — 笔记内容
    """
    path = Path(f"data/knowledge/{topic}.md")
    path.write_text(body.content, encoding="utf-8")
    return {"ok": True}
```

---

## 实施顺序建议

虽然每个模块是自包含的，但建议按以下顺序实施以尽快获得可用的应用：

1. **M01**：项目脚手架 — 能跑起来
2. **M05**：主题 + 布局 — 能看到界面
3. **M06 + M-SERVER-2**：聊天页面 — 最核心的功能
4. **M02**：托盘 + 快捷键 + 自启 — 基础系统集成
5. **M08**：仪表盘 — 第二常用的页面
6. **M03 + M04 + M-SERVER-1**：环境感知 — Lapwing 的"眼睛"
7. **M09**：感知页面 — 展示感知数据
8. **M07**：任务中心 — 观察 Agent
9. **M10 + M-SERVER-5**：记忆管理
10. **M11 + M-SERVER-4**：人格 + 技能管理
11. **M12 + M-SERVER-3**：模型路由
12. **M13**：设置页面

---

## 附录：类型定义参考

### TypeScript 核心类型 (`src/types/`)

```typescript
// chat.ts
interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  created_at: string;
  session_id?: string;
  tool_calls?: ToolCall[];
}

interface ToolCall {
  name: string;
  arguments: Record<string, unknown>;
  result?: string;
}

// sensing.ts
interface AppUsageRecord {
  process_name: string;
  app_display_name: string;
  total_seconds: number;
  category: string;
}

interface WindowEvent {
  timestamp: string;
  process_name: string;
  window_title: string;
  duration_seconds: number;
}

interface SessionEvent {
  timestamp: string;
  event_type: "boot" | "shutdown" | "lock" | "unlock" | "game_start" | "game_end";
  detail?: string;
}

interface SilenceState {
  active: boolean;
  game_name?: string;
  started_at?: string;
}

// api.ts
interface ServerStatus {
  uptime: string;
  model: string;
  channels: ChannelInfo[];
  boot_time: string;
}

interface SystemStats {
  cpu_percent: number;
  memory_percent: number;
  disk_percent: number;
  disk_free_gb: number;
}

interface HeartbeatStatus {
  last_fast_tick: string;
  last_slow_tick: string;
  next_fast_tick: string;
  last_action: string;
}

interface ReminderItem {
  id: number;
  content: string;
  recurrence_type: string;
  trigger_at: string;
  status: string;
}

// tasks.ts
interface TaskFlow {
  flow_id: string;
  title: string;
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  steps: TaskStep[];
  created_at: string;
}

interface TaskStep {
  step_id: string;
  description: string;
  status: string;
  tool_name?: string;
  result?: Record<string, unknown>;
}
```

### Rust 核心结构体

```rust
#[derive(Serialize, Deserialize)]
pub struct LocalConfig {
    pub server_url: String,
    pub hotkey: String,
    pub autostart: bool,
    pub watch_directories: Vec<String>,
    pub notification_sound: String,
    pub aggregator_interval_seconds: u64,
    pub clipboard_max_entries: u32,
}

#[derive(Serialize)]
pub struct AppUsageRecord {
    pub process_name: String,
    pub app_display_name: String,
    pub total_seconds: i64,
    pub category: String,
}

#[derive(Serialize)]
pub struct SilenceState {
    pub active: bool,
    pub game_name: Option<String>,
    pub started_at: Option<String>,
}
```