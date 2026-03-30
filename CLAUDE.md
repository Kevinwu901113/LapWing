# Lapwing Desktop Panel — 完整前端重构蓝图

> 本文档是桌面前端的完整重构方案，供 Claude Code 实现。
> 参考 AstrBot 的 sidebar + 分页架构，保留 React + Vite + Tauri 技术栈。
> **不改动后端 API（`src/api/server.py`）**，只重构前端。

---

## 0. 问题与目标

### 当前问题
- `App.tsx` 是 600 行的单文件巨石组件，所有面板堆在一个页面
- 没有路由、没有导航、没有组件拆分
- Auth 面板、记忆、兴趣、事件、任务、学习日志全平铺在 grid 中
- 新增功能只能继续往下堆，无法扩展

### 目标
- **Sidebar + 路由页面**架构（参考 AstrBot dashboard 的 FullLayout + 分页模式）
- 拆分为 7 个独立页面组件
- 提取可复用 UI 组件
- 保持现有暖色调毛玻璃美学，但换掉 Space Grotesk 字体
- 后端 API 零改动，所有现有端点保持原样
- `api.ts` 零改动（类型和请求函数全部复用）

---

## 1. 新增依赖

在 `desktop/package.json` 中添加：

```json
{
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.28.0",
    "lucide-react": "^0.460.0"
  }
}
```

只新增两个依赖：`react-router-dom`（路由）和 `lucide-react`（图标）。不引入任何 UI 框架。

---

## 2. 目录结构

```
desktop/src/
├── main.tsx                     # 入口，挂载 RouterProvider
├── api.ts                       # 【不改动】现有 API 客户端
├── router.tsx                   # 路由定义
├── styles/
│   ├── globals.css              # 全局 CSS 变量 + reset + 字体
│   ├── sidebar.css              # 侧栏样式
│   └── pages.css                # 页面通用组件样式
├── components/
│   ├── AppShell.tsx             # 根布局：Sidebar + <Outlet />
│   ├── Sidebar.tsx              # 侧栏导航
│   ├── AuthGuard.tsx            # 鉴权守卫（bootstrap token 流程）
│   ├── StatusDot.tsx            # 在线状态小圆点
│   ├── StatCard.tsx             # 统计卡片
│   ├── DataCard.tsx             # 通用数据展示卡片（标题 + 内容）
│   ├── BarMeter.tsx             # 兴趣权重条
│   ├── EmptyState.tsx           # 空状态占位
│   └── EventBadge.tsx           # 事件类型标签
├── hooks/
│   ├── useSSE.ts                # SSE 连接 hook
│   ├── usePolling.ts            # 定时轮询 hook
│   └── useLatencyTelemetry.ts   # 延迟遥测 hook（从 App.tsx 提取）
├── pages/
│   ├── OverviewPage.tsx         # 总览：状态 + 快捷操作
│   ├── MemoryPage.tsx           # 记忆管理 + 兴趣图谱
│   ├── PersonaPage.tsx          # 人格进化 + 学习日志
│   ├── TasksPage.tsx            # 任务视图 + 任务详情
│   ├── EventsPage.tsx           # 实时事件流
│   ├── AuthPage.tsx             # Auth 状态 + OAuth + Codex
│   └── SettingsPage.tsx         # 系统设置（预留）
└── vite-env.d.ts                # 【不改动】
```

---

## 3. 路由定义

### `router.tsx`

```tsx
import { createBrowserRouter } from "react-router-dom";
import AppShell from "./components/AppShell";
import OverviewPage from "./pages/OverviewPage";
import MemoryPage from "./pages/MemoryPage";
import PersonaPage from "./pages/PersonaPage";
import TasksPage from "./pages/TasksPage";
import EventsPage from "./pages/EventsPage";
import AuthPage from "./pages/AuthPage";
import SettingsPage from "./pages/SettingsPage";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <OverviewPage /> },
      { path: "memory", element: <MemoryPage /> },
      { path: "persona", element: <PersonaPage /> },
      { path: "tasks", element: <TasksPage /> },
      { path: "events", element: <EventsPage /> },
      { path: "auth", element: <AuthPage /> },
      { path: "settings", element: <SettingsPage /> },
    ],
  },
]);
```

### `main.tsx`

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router-dom";
import { router } from "./router";
import "./styles/globals.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
```

---

## 4. 全局样式

### `styles/globals.css`

替换现有的 `styles.css`。

**字体选择**：`"Outfit"` 作为主字体（geometric sans，比 Space Grotesk 更精致），`"Noto Sans SC"` 保持中文显示。

```css
@import url("https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&family=Noto+Sans+SC:wght@400;500;700&display=swap");

:root {
  /* 色彩系统 */
  --color-bg: #f5f1ea;
  --color-surface: rgba(255, 255, 255, 0.72);
  --color-surface-hover: rgba(255, 255, 255, 0.88);
  --color-surface-solid: #ffffff;
  --color-border: rgba(22, 56, 95, 0.08);
  --color-border-active: rgba(22, 56, 95, 0.2);

  --color-text-primary: #1a2332;
  --color-text-secondary: #526075;
  --color-text-muted: #8494a7;

  --color-accent: #2d6be4;
  --color-accent-soft: rgba(45, 107, 228, 0.1);
  --color-success: #2f9e72;
  --color-success-soft: rgba(47, 158, 114, 0.12);
  --color-danger: #c44536;
  --color-danger-soft: rgba(196, 69, 54, 0.1);
  --color-warning: #d4940a;

  /* 侧栏 */
  --sidebar-width: 240px;
  --sidebar-collapsed-width: 68px;
  --sidebar-bg: rgba(26, 35, 50, 0.95);
  --sidebar-text: rgba(255, 255, 255, 0.7);
  --sidebar-text-active: #ffffff;
  --sidebar-item-hover: rgba(255, 255, 255, 0.08);
  --sidebar-item-active: rgba(45, 107, 228, 0.2);

  /* 圆角 */
  --radius-sm: 8px;
  --radius-md: 14px;
  --radius-lg: 20px;
  --radius-pill: 999px;

  /* 阴影 */
  --shadow-card: 0 2px 12px rgba(31, 43, 61, 0.06);
  --shadow-card-hover: 0 8px 32px rgba(31, 43, 61, 0.1);
  --shadow-sidebar: 4px 0 24px rgba(0, 0, 0, 0.08);

  /* 排版 */
  font-family: "Outfit", "Noto Sans SC", sans-serif;
  font-size: 15px;
  line-height: 1.55;
  color: var(--color-text-primary);

  /* 背景 */
  color-scheme: light;
  background:
    radial-gradient(ellipse at 10% 0%, rgba(255, 184, 108, 0.35), transparent 50%),
    radial-gradient(ellipse at 90% 10%, rgba(109, 181, 255, 0.3), transparent 40%),
    var(--color-bg);
  background-attachment: fixed;
}

* {
  box-sizing: border-box;
  margin: 0;
}

body {
  min-height: 100vh;
  min-width: 320px;
  -webkit-font-smoothing: antialiased;
}

button, select, textarea, input {
  font: inherit;
  color: inherit;
}

/* ---------- 通用按钮 ---------- */
.btn {
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  border: none;
  border-radius: var(--radius-pill);
  padding: 0.6rem 1.1rem;
  font-weight: 500;
  font-size: 0.875rem;
  cursor: pointer;
  transition: all 0.18s ease;
  white-space: nowrap;
}

.btn:hover { transform: translateY(-1px); }
.btn:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }

.btn-primary { background: var(--color-accent); color: #fff; }
.btn-primary:hover { background: #2560d0; }

.btn-soft {
  background: var(--color-surface);
  color: var(--color-text-primary);
  border: 1px solid var(--color-border);
}
.btn-soft:hover { background: var(--color-surface-hover); border-color: var(--color-border-active); }

.btn-danger { background: var(--color-danger); color: #fff; }
.btn-danger-soft { background: var(--color-danger-soft); color: var(--color-danger); }

.btn-sm { padding: 0.4rem 0.75rem; font-size: 0.8rem; }
.btn-icon { padding: 0.5rem; border-radius: var(--radius-sm); }

/* ---------- 动画 ---------- */
@keyframes fade-up {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}

@keyframes fade-in {
  from { opacity: 0; }
  to { opacity: 1; }
}

.animate-in {
  animation: fade-up 0.4s ease both;
}

/* 交错动画 */
.stagger-1 { animation-delay: 0.05s; }
.stagger-2 { animation-delay: 0.1s; }
.stagger-3 { animation-delay: 0.15s; }
.stagger-4 { animation-delay: 0.2s; }

/* ---------- 滚动条 ---------- */
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: rgba(22, 56, 95, 0.15); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: rgba(22, 56, 95, 0.25); }
```

---

## 5. 核心组件

### 5.1 `AppShell.tsx` — 根布局

```tsx
import { useState } from "react";
import { Outlet } from "react-router-dom";
import Sidebar from "./Sidebar";
import AuthGuard from "./AuthGuard";

export default function AppShell() {
  const [collapsed, setCollapsed] = useState(false);

  return (
    <AuthGuard>
      <div style={{
        display: "flex",
        minHeight: "100vh",
      }}>
        <Sidebar collapsed={collapsed} onToggle={() => setCollapsed(!collapsed)} />
        <main style={{
          flex: 1,
          marginLeft: collapsed ? "var(--sidebar-collapsed-width)" : "var(--sidebar-width)",
          padding: "1.5rem 2rem 3rem",
          transition: "margin-left 0.25s ease",
          maxWidth: "1100px",
        }}>
          <Outlet />
        </main>
      </div>
    </AuthGuard>
  );
}
```

### 5.2 `Sidebar.tsx` — 侧栏导航

仿 AstrBot 的 `FullLayout.vue` 侧栏结构：logo + nav items + 底部状态。

```tsx
import { NavLink, useLocation } from "react-router-dom";
import {
  LayoutDashboard,
  Brain,
  Sparkles,
  ListTodo,
  Radio,
  Shield,
  Settings,
  PanelLeftClose,
  PanelLeft,
} from "lucide-react";
import StatusDot from "./StatusDot";

type SidebarProps = {
  collapsed: boolean;
  onToggle: () => void;
};

const NAV_ITEMS = [
  { to: "/", icon: LayoutDashboard, label: "总览", end: true },
  { to: "/memory", icon: Brain, label: "记忆" },
  { to: "/persona", icon: Sparkles, label: "人格" },
  { to: "/tasks", icon: ListTodo, label: "任务" },
  { to: "/events", icon: Radio, label: "事件" },
  { to: "/auth", icon: Shield, label: "认证" },
  { to: "/settings", icon: Settings, label: "设置" },
] as const;

export default function Sidebar({ collapsed, onToggle }: SidebarProps) {
  return (
    <aside className={`sidebar ${collapsed ? "sidebar--collapsed" : ""}`}>
      {/* 顶部 Logo 区域 */}
      <div className="sidebar-header">
        {!collapsed && <span className="sidebar-logo">Lapwing</span>}
        <button className="sidebar-toggle btn-icon" onClick={onToggle}>
          {collapsed ? <PanelLeft size={18} /> : <PanelLeftClose size={18} />}
        </button>
      </div>

      {/* 导航项 */}
      <nav className="sidebar-nav">
        {NAV_ITEMS.map(({ to, icon: Icon, label, ...rest }) => (
          <NavLink
            key={to}
            to={to}
            end={"end" in rest}
            className={({ isActive }) =>
              `sidebar-item ${isActive ? "sidebar-item--active" : ""}`
            }
            title={collapsed ? label : undefined}
          >
            <Icon size={20} strokeWidth={1.8} />
            {!collapsed && <span>{label}</span>}
          </NavLink>
        ))}
      </nav>

      {/* 底部状态 */}
      <div className="sidebar-footer">
        {!collapsed && (
          <div className="sidebar-status">
            <StatusDot online={true} />
            <span>后端在线</span>
          </div>
        )}
      </div>
    </aside>
  );
}
```

### 5.3 `AuthGuard.tsx` — 鉴权守卫

从现有 `App.tsx` 提取 bootstrap token 逻辑，独立为守卫组件。包裹 `AppShell`，只在鉴权成功后渲染子组件。

```tsx
import { type FormEvent, useEffect, useState, type ReactNode } from "react";
import { createApiSession, getAuthStatus } from "../api";

declare global {
  interface Window {
    __TAURI__?: {
      invoke?: (command: string, args?: Record<string, unknown>) => Promise<unknown>;
    };
  }
}

async function readBootstrapToken(): Promise<string> {
  const invoke = window.__TAURI__?.invoke;
  if (!invoke) throw new Error("Tauri runtime unavailable");
  const token = await invoke("read_bootstrap_token");
  if (typeof token !== "string" || !token.trim()) throw new Error("Failed to read bootstrap token");
  return token;
}

export default function AuthGuard({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  const [manualMode, setManualMode] = useState(false);
  const [manualToken, setManualToken] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // 先尝试现有 session
        await getAuthStatus();
        if (!cancelled) setReady(true);
      } catch {
        // 尝试 Tauri 自动获取 token
        try {
          const token = await readBootstrapToken();
          await createApiSession(token);
          if (!cancelled) setReady(true);
        } catch {
          // 非 Tauri 环境，进入手动输入模式
          if (!cancelled) setManualMode(true);
        }
      }
    })();
    return () => { cancelled = true; };
  }, []);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!manualToken.trim()) return;
    setSubmitting(true);
    try {
      await createApiSession(manualToken.trim());
      setReady(true);
      setManualMode(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  if (ready) return <>{children}</>;

  return (
    <div className="auth-guard-page">
      <div className="auth-guard-card animate-in">
        <p className="auth-guard-eyebrow">Lapwing Desktop</p>
        {manualMode ? (
          <>
            <h1>输入 Bootstrap Token</h1>
            <p className="auth-guard-hint">
              在远端主机查看 <code>~/.lapwing/auth/api-bootstrap-token</code>
            </p>
            <form onSubmit={handleSubmit} className="auth-guard-form">
              <textarea
                value={manualToken}
                onChange={(e) => setManualToken(e.target.value)}
                placeholder="粘贴 bootstrap token"
                rows={3}
              />
              <button type="submit" className="btn btn-primary" disabled={submitting}>
                {submitting ? "验证中…" : "建立会话"}
              </button>
            </form>
          </>
        ) : error ? (
          <>
            <h1>鉴权失败</h1>
            <p className="auth-guard-hint">{error}</p>
          </>
        ) : (
          <>
            <h1>正在连接…</h1>
            <p className="auth-guard-hint">读取 bootstrap token 并建立本地会话</p>
          </>
        )}
      </div>
    </div>
  );
}
```

### 5.4 小组件

#### `StatusDot.tsx`
```tsx
export default function StatusDot({ online }: { online: boolean }) {
  return (
    <span
      className={`status-dot ${online ? "status-dot--online" : "status-dot--offline"}`}
    />
  );
}
```

#### `StatCard.tsx`
```tsx
type StatCardProps = {
  label: string;
  value: string | number;
  sub?: string;
};

export default function StatCard({ label, value, sub }: StatCardProps) {
  return (
    <div className="stat-card">
      <span className="stat-card-label">{label}</span>
      <strong className="stat-card-value">{value}</strong>
      {sub && <span className="stat-card-sub">{sub}</span>}
    </div>
  );
}
```

#### `DataCard.tsx`
```tsx
import type { ReactNode } from "react";

type DataCardProps = {
  title: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
};

export default function DataCard({ title, actions, children, className = "" }: DataCardProps) {
  return (
    <section className={`data-card animate-in ${className}`}>
      <div className="data-card-head">
        <h2>{title}</h2>
        {actions && <div className="data-card-actions">{actions}</div>}
      </div>
      <div className="data-card-body">{children}</div>
    </section>
  );
}
```

#### `EmptyState.tsx`
```tsx
export default function EmptyState({ message }: { message: string }) {
  return <p className="empty-state">{message}</p>;
}
```

#### `BarMeter.tsx`
```tsx
type BarMeterProps = {
  label: string;
  value: number;
  max?: number;
  suffix?: string;
};

export default function BarMeter({ label, value, max = 10, suffix }: BarMeterProps) {
  const pct = Math.min((value / max) * 100, 100);
  return (
    <div className="bar-meter">
      <div className="bar-meter-row">
        <span>{label}</span>
        <strong>{value.toFixed(1)}{suffix}</strong>
      </div>
      <div className="bar-meter-track">
        <div className="bar-meter-fill" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
```

#### `EventBadge.tsx`
```tsx
export default function EventBadge({ type }: { type: string }) {
  return <span className="event-badge">{type}</span>;
}
```

---

## 6. 页面组件

### 6.1 `OverviewPage.tsx` — 总览

从现有 App.tsx 提取「状态」面板 + hero 操作按钮。这是首页。

```tsx
import { useEffect, useState } from "react";
import { RefreshCw, Zap } from "lucide-react";
import {
  getStatus, getChats, reloadPersona, evolvePrompt,
  type StatusResponse, type ChatSummary,
} from "../api";
import StatCard from "../components/StatCard";
import DataCard from "../components/DataCard";
import StatusDot from "../components/StatusDot";

function formatDate(v: string | null) {
  return v ? new Date(v).toLocaleString("zh-CN") : "暂无";
}

export default function OverviewPage() {
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [busy, setBusy] = useState<"reload" | "evolve" | null>(null);

  useEffect(() => {
    void Promise.all([getStatus(), getChats()]).then(([s, c]) => {
      setStatus(s);
      setChats(c);
    });
  }, []);

  async function handleReload() {
    setBusy("reload");
    try { await reloadPersona(); } finally { setBusy(null); }
  }

  async function handleEvolve() {
    setBusy("evolve");
    try { await evolvePrompt(); } finally { setBusy(null); }
  }

  return (
    <div className="page">
      {/* 页头 */}
      <header className="page-header animate-in">
        <div>
          <h1 className="page-title">总览</h1>
          <p className="page-subtitle">Lapwing 运行状态一览</p>
        </div>
        <div className="page-header-actions">
          <button className="btn btn-primary" onClick={handleReload} disabled={busy !== null}>
            <RefreshCw size={16} />
            {busy === "reload" ? "重载中…" : "重载人格"}
          </button>
          <button className="btn btn-soft" onClick={handleEvolve} disabled={busy !== null}>
            <Zap size={16} />
            {busy === "evolve" ? "进化中…" : "触发进化"}
          </button>
        </div>
      </header>

      {/* 状态卡片组 */}
      <div className="stat-grid animate-in stagger-1">
        <StatCard label="Chat 数量" value={status?.chat_count ?? 0} />
        <StatCard label="最后活跃" value={formatDate(status?.last_interaction ?? null)} />
        <StatCard label="服务启动" value={formatDate(status?.started_at ?? null)} />
        <StatCard
          label="后端状态"
          value={status?.online ? "在线" : "离线"}
        />
      </div>

      {/* 最近 Chat 列表 */}
      <DataCard title="最近对话" className="stagger-2">
        {chats.length === 0 ? (
          <p className="empty-state">暂无对话记录。</p>
        ) : (
          <div className="list-stack">
            {chats.slice(0, 8).map((chat) => (
              <div key={chat.chat_id} className="list-row">
                <span className="list-row-key">{chat.chat_id}</span>
                <span className="list-row-muted">{formatDate(chat.last_interaction)}</span>
              </div>
            ))}
          </div>
        )}
      </DataCard>
    </div>
  );
}
```

### 6.2 `MemoryPage.tsx` — 记忆 + 兴趣

合并现有「记忆管理」和「兴趣图谱」面板。需要 chat 选择器。

```tsx
import { useEffect, useState } from "react";
import { Trash2 } from "lucide-react";
import {
  getChats, getInterests, getMemory, deleteMemory,
  type ChatSummary, type InterestItem, type MemoryItem,
} from "../api";
import DataCard from "../components/DataCard";
import BarMeter from "../components/BarMeter";
import EmptyState from "../components/EmptyState";

function formatDate(v: string | null) {
  return v ? new Date(v).toLocaleString("zh-CN") : "暂无";
}

export default function MemoryPage() {
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [chatId, setChatId] = useState("");
  const [interests, setInterests] = useState<InterestItem[]>([]);
  const [memory, setMemory] = useState<MemoryItem[]>([]);

  useEffect(() => {
    void getChats().then((c) => {
      setChats(c);
      if (c.length > 0 && !chatId) setChatId(c[0].chat_id);
    });
  }, []);

  useEffect(() => {
    if (!chatId) return;
    void Promise.all([getInterests(chatId), getMemory(chatId)]).then(([i, m]) => {
      setInterests(i.items);
      setMemory(m.items);
    });
  }, [chatId]);

  async function handleDelete(factKey: string) {
    await deleteMemory(chatId, factKey);
    const res = await getMemory(chatId);
    setMemory(res.items);
  }

  return (
    <div className="page">
      <header className="page-header animate-in">
        <div>
          <h1 className="page-title">记忆</h1>
          <p className="page-subtitle">管理 Lapwing 的记忆和兴趣图谱</p>
        </div>
        <select
          className="chat-selector"
          value={chatId}
          onChange={(e) => setChatId(e.target.value)}
        >
          {chats.map((c) => (
            <option key={c.chat_id} value={c.chat_id}>{c.chat_id}</option>
          ))}
        </select>
      </header>

      {/* 双列布局 */}
      <div className="two-col">
        {/* 兴趣图谱 */}
        <DataCard title="兴趣图谱" className="stagger-1">
          {interests.length === 0 ? (
            <EmptyState message="暂无兴趣记录。" />
          ) : (
            <div className="list-stack">
              {interests.map((item) => (
                <BarMeter
                  key={item.topic}
                  label={item.topic}
                  value={item.weight}
                  max={8}
                />
              ))}
            </div>
          )}
        </DataCard>

        {/* 记忆列表 */}
        <DataCard title={`记忆 (${memory.length})`} className="stagger-2">
          {memory.length === 0 ? (
            <EmptyState message="当前没有可见记忆。" />
          ) : (
            <div className="list-stack">
              {memory.map((item) => (
                <div key={item.fact_key} className="memory-row">
                  <div className="memory-row-content">
                    <p className="memory-row-key">#{item.index} [{item.fact_key}]</p>
                    <p className="memory-row-value">{item.fact_value}</p>
                    <span className="list-row-muted">
                      更新于 {formatDate(item.updated_at)}
                    </span>
                  </div>
                  <button
                    className="btn btn-danger-soft btn-sm btn-icon"
                    onClick={() => void handleDelete(item.fact_key)}
                    title="删除"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
            </div>
          )}
        </DataCard>
      </div>
    </div>
  );
}
```

### 6.3 `PersonaPage.tsx` — 人格 + 学习日志

合并现有「学习日志」面板 + 进化操作。

```tsx
import { useEffect, useState } from "react";
import { Sparkles, RefreshCw } from "lucide-react";
import {
  getLearnings, evolvePrompt, reloadPersona,
  type LearningItem,
} from "../api";
import DataCard from "../components/DataCard";
import EmptyState from "../components/EmptyState";

function formatDate(v: string) {
  return new Date(v).toLocaleString("zh-CN");
}

export default function PersonaPage() {
  const [learnings, setLearnings] = useState<LearningItem[]>([]);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    void getLearnings().then((r) => setLearnings(r.items));
  }, []);

  async function handleEvolve() {
    setBusy("evolve");
    try { await evolvePrompt(); } finally { setBusy(null); }
  }

  async function handleReload() {
    setBusy("reload");
    try { await reloadPersona(); } finally { setBusy(null); }
  }

  return (
    <div className="page">
      <header className="page-header animate-in">
        <div>
          <h1 className="page-title">人格</h1>
          <p className="page-subtitle">Lapwing 的自省日志和人格进化</p>
        </div>
        <div className="page-header-actions">
          <button className="btn btn-primary" onClick={handleEvolve} disabled={busy !== null}>
            <Sparkles size={16} />
            {busy === "evolve" ? "进化中…" : "触发进化"}
          </button>
          <button className="btn btn-soft" onClick={handleReload} disabled={busy !== null}>
            <RefreshCw size={16} />
            {busy === "reload" ? "重载中…" : "重载人格"}
          </button>
        </div>
      </header>

      <DataCard title="学习日志" className="stagger-1">
        {learnings.length === 0 ? (
          <EmptyState message="data/memory/journal/ 中暂无日志。" />
        ) : (
          <div className="list-stack">
            {learnings.map((item) => (
              <div key={item.filename} className="learning-entry">
                <div className="learning-entry-head">
                  <strong>{item.date}</strong>
                  <span className="list-row-muted">{formatDate(item.updated_at)}</span>
                </div>
                <pre className="learning-entry-body">{item.content}</pre>
              </div>
            ))}
          </div>
        )}
      </DataCard>
    </div>
  );
}
```

### 6.4 `TasksPage.tsx` — 任务视图

```tsx
import { useEffect, useState } from "react";
import {
  getChats, getTasks, getTask,
  type ChatSummary, type TaskSummary, type TaskDetail,
} from "../api";
import DataCard from "../components/DataCard";
import EmptyState from "../components/EmptyState";

function formatDate(v: string | null) {
  return v ? new Date(v).toLocaleString("zh-CN") : "—";
}

export default function TasksPage() {
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [chatId, setChatId] = useState("");
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<TaskDetail | null>(null);

  useEffect(() => {
    void getChats().then((c) => {
      setChats(c);
      if (c.length > 0 && !chatId) setChatId(c[0].chat_id);
    });
  }, []);

  useEffect(() => {
    if (!chatId) return;
    void getTasks(chatId, undefined, 20).then((r) => {
      setTasks(r.items);
      if (r.items.length > 0) setSelectedId(r.items[0].task_id);
    });
  }, [chatId]);

  useEffect(() => {
    if (!selectedId) { setDetail(null); return; }
    void getTask(selectedId).then(setDetail);
  }, [selectedId]);

  return (
    <div className="page">
      <header className="page-header animate-in">
        <div>
          <h1 className="page-title">任务</h1>
          <p className="page-subtitle">Agent 团队的任务执行记录</p>
        </div>
        <select
          className="chat-selector"
          value={chatId}
          onChange={(e) => setChatId(e.target.value)}
        >
          {chats.map((c) => (
            <option key={c.chat_id} value={c.chat_id}>{c.chat_id}</option>
          ))}
        </select>
      </header>

      <div className="two-col">
        <DataCard title={`任务列表 (${tasks.length})`} className="stagger-1">
          {tasks.length === 0 ? (
            <EmptyState message="暂无任务记录。" />
          ) : (
            <div className="list-stack">
              {tasks.map((task) => (
                <div
                  key={task.task_id}
                  className={`task-row ${selectedId === task.task_id ? "task-row--active" : ""}`}
                  onClick={() => setSelectedId(task.task_id)}
                >
                  <p className="task-row-id">{task.task_id}</p>
                  <p className="task-row-text">{task.text || "（无文本）"}</p>
                  <span className="list-row-muted">
                    {task.status} · {formatDate(task.updated_at ?? null)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </DataCard>

        <DataCard title="任务详情" className="stagger-2">
          {!detail ? (
            <EmptyState message="选择左侧任务查看详情。" />
          ) : (
            <div className="task-detail">
              <div className="task-detail-header">
                <strong>{detail.task_id}</strong>
                <span className={`task-status task-status--${detail.status}`}>
                  {detail.status}
                </span>
              </div>
              <pre className="task-detail-events">
                {JSON.stringify(detail.events, null, 2)}
              </pre>
            </div>
          )}
        </DataCard>
      </div>
    </div>
  );
}
```

### 6.5 `EventsPage.tsx` — 实时事件流

```tsx
import { useEffect, useRef, useState } from "react";
import { Radio } from "lucide-react";
import { API_BASE, type DesktopEvent } from "../api";
import DataCard from "../components/DataCard";
import StatusDot from "../components/StatusDot";
import EventBadge from "../components/EventBadge";
import EmptyState from "../components/EmptyState";

function formatDate(v: string) {
  return new Date(v).toLocaleString("zh-CN");
}

export default function EventsPage() {
  const [events, setEvents] = useState<DesktopEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const streamRef = useRef<EventSource | null>(null);

  useEffect(() => {
    const stream = new EventSource(`${API_BASE}/api/events/stream`, {
      withCredentials: API_BASE.length > 0,
    });
    streamRef.current = stream;

    stream.onopen = () => setConnected(true);
    stream.onerror = () => setConnected(false);
    stream.onmessage = (msg) => {
      const event = JSON.parse(msg.data) as DesktopEvent;
      setEvents((prev) => [event, ...prev].slice(0, 50));
    };

    return () => { stream.close(); setConnected(false); };
  }, []);

  return (
    <div className="page">
      <header className="page-header animate-in">
        <div>
          <h1 className="page-title">事件流</h1>
          <p className="page-subtitle">来自后端的实时 SSE 事件</p>
        </div>
        <div className="page-header-actions">
          <div className="connection-pill">
            <StatusDot online={connected} />
            <span>{connected ? "已连接" : "未连接"}</span>
          </div>
        </div>
      </header>

      <DataCard title={`最近事件 (${events.length})`} className="stagger-1">
        {events.length === 0 ? (
          <EmptyState message="等待来自 SSE 的事件…" />
        ) : (
          <div className="list-stack">
            {events.map((event, i) => (
              <div key={`${event.timestamp}-${i}`} className="event-row">
                <EventBadge type={event.type} />
                <p className="event-row-text">{event.payload.text ?? "（无文本）"}</p>
                <span className="list-row-muted">
                  {event.payload.chat_id ?? "unknown"} · {formatDate(event.timestamp)}
                  {event.payload.task_id ? ` · ${event.payload.task_id}` : ""}
                  {event.payload.tool_name ? ` · ${event.payload.tool_name}` : ""}
                </span>
              </div>
            ))}
          </div>
        )}
      </DataCard>
    </div>
  );
}
```

### 6.6 `AuthPage.tsx` — 认证管理

从现有 App.tsx 提取 Auth 面板、OAuth 流程、Codex 导入。

```tsx
import { useEffect, useState } from "react";
import { KeyRound, Download, ExternalLink } from "lucide-react";
import {
  getAuthStatus, importCodexCache, startOpenAICodexOAuth,
  getOAuthLoginSession,
  type AuthStatusResponse, type OAuthLoginSession,
} from "../api";
import DataCard from "../components/DataCard";
import EmptyState from "../components/EmptyState";

function formatDate(v: string | null) {
  return v ? new Date(v).toLocaleString("zh-CN") : "—";
}

export default function AuthPage() {
  const [authStatus, setAuthStatus] = useState<AuthStatusResponse | null>(null);
  const [importing, setImporting] = useState(false);
  const [startingOAuth, setStartingOAuth] = useState(false);
  const [oauthSession, setOAuthSession] = useState<OAuthLoginSession | null>(null);
  const [oauthNotice, setOAuthNotice] = useState("");

  useEffect(() => {
    void getAuthStatus().then(setAuthStatus);
  }, []);

  // OAuth 轮询
  useEffect(() => {
    if (!oauthSession || !["pending", "completing"].includes(oauthSession.status)) return;
    const timer = setInterval(async () => {
      try {
        const next = await getOAuthLoginSession(oauthSession.loginId);
        setOAuthSession(next);
        if (next.status === "completed") {
          setOAuthNotice(next.completionMessage ?? "OpenAI 登录成功。");
          void getAuthStatus().then(setAuthStatus);
        } else if (["failed", "expired"].includes(next.status)) {
          setOAuthNotice(next.error ?? "登录未完成。");
        }
      } catch {}
    }, 1500);
    return () => clearInterval(timer);
  }, [oauthSession]);

  async function handleImport() {
    setImporting(true);
    try {
      await importCodexCache();
      void getAuthStatus().then(setAuthStatus);
    } finally {
      setImporting(false);
    }
  }

  async function handleOAuth() {
    setStartingOAuth(true);
    try {
      const returnTo = ["http:", "https:"].includes(window.location.protocol)
        ? window.location.href : undefined;
      const session = await startOpenAICodexOAuth(returnTo);
      setOAuthSession(session);
      setOAuthNotice("授权页面已就绪，完成后自动刷新。");
      window.open(session.authorizeUrl, "_blank", "noopener,noreferrer");
    } catch (err) {
      setOAuthNotice(err instanceof Error ? err.message : String(err));
    } finally {
      setStartingOAuth(false);
    }
  }

  return (
    <div className="page">
      <header className="page-header animate-in">
        <div>
          <h1 className="page-title">认证</h1>
          <p className="page-subtitle">Auth Profiles、OAuth 和本机 API 安全</p>
        </div>
        <div className="page-header-actions">
          <button className="btn btn-primary" onClick={handleOAuth}
            disabled={startingOAuth || oauthSession?.status === "pending"}>
            <KeyRound size={16} />
            {startingOAuth ? "跳转中…" : "登录 OpenAI"}
          </button>
          <button className="btn btn-soft" onClick={handleImport} disabled={importing}>
            <Download size={16} />
            {importing ? "导入中…" : "导入 Codex auth.json"}
          </button>
        </div>
      </header>

      <div className="two-col">
        {/* 服务状态 */}
        <DataCard title="服务认证" className="stagger-1">
          <div className="list-stack">
            <div className="list-row">
              <span className="list-row-key">Host</span>
              <span>{authStatus?.serviceAuth.host ?? "127.0.0.1"}</span>
            </div>
            <div className="list-row">
              <span className="list-row-key">Cookie</span>
              <span>{authStatus?.serviceAuth.cookieName ?? "lapwing_session"}</span>
            </div>
            <div className="list-row">
              <span className="list-row-key">保护状态</span>
              <span>{authStatus?.serviceAuth.protected ? "✓ 已保护" : "✗ 未保护"}</span>
            </div>
          </div>
          {oauthNotice && <p className="auth-notice">{oauthNotice}</p>}
          {oauthSession?.authorizeUrl && ["pending", "failed", "expired"].includes(oauthSession.status) && (
            <p className="auth-notice">
              浏览器未自动打开？{" "}
              <a href={oauthSession.authorizeUrl} target="_blank" rel="noreferrer"
                className="auth-link">
                点击手动授权 <ExternalLink size={12} />
              </a>
            </p>
          )}
        </DataCard>

        {/* Profiles */}
        <DataCard title="Auth Profiles" className="stagger-2">
          {(authStatus?.profiles ?? []).length === 0 ? (
            <EmptyState message="尚未导入或登录任何 auth profile。" />
          ) : (
            <div className="list-stack">
              {authStatus!.profiles.map((p) => (
                <div key={p.profileId} className="list-row-block">
                  <div className="list-row">
                    <span className="list-row-key">{p.profileId}</span>
                    <span>{p.provider} · {p.type}</span>
                  </div>
                  <span className="list-row-muted">
                    {p.status}
                    {p.reasonCode ? ` · ${p.reasonCode}` : ""}
                    {p.expiresAt ? ` · 到期 ${formatDate(p.expiresAt)}` : ""}
                  </span>
                </div>
              ))}
            </div>
          )}
        </DataCard>
      </div>

      {/* Routes */}
      <DataCard title="路由配置" className="stagger-3">
        {Object.keys(authStatus?.routes ?? {}).length === 0 ? (
          <EmptyState message="暂无路由配置。" />
        ) : (
          <div className="list-stack">
            {Object.entries(authStatus!.routes!).map(([purpose, route]) => (
              <div key={purpose} className="list-row-block">
                <div className="list-row">
                  <span className="list-row-key">{purpose}</span>
                  <span>{route.provider || "auto"} · {route.model}</span>
                </div>
                <span className="list-row-muted">
                  {route.baseUrl}
                  {route.bindingMismatch ? " · ⚠ binding 不一致" : ""}
                </span>
              </div>
            ))}
          </div>
        )}
      </DataCard>
    </div>
  );
}
```

### 6.7 `SettingsPage.tsx` — 预留

```tsx
import DataCard from "../components/DataCard";

export default function SettingsPage() {
  return (
    <div className="page">
      <header className="page-header animate-in">
        <div>
          <h1 className="page-title">设置</h1>
          <p className="page-subtitle">系统配置（即将推出）</p>
        </div>
      </header>

      <DataCard title="配置管理" className="stagger-1">
        <p className="empty-state">
          此页面将支持在线编辑 .env 配置、管理 Heartbeat 参数、调整 LLM 路由策略等。
          目前请直接编辑服务器上的配置文件。
        </p>
      </DataCard>
    </div>
  );
}
```

---

## 7. 页面级样式

### `styles/sidebar.css`

```css
.sidebar {
  position: fixed;
  top: 0;
  left: 0;
  width: var(--sidebar-width);
  height: 100vh;
  background: var(--sidebar-bg);
  backdrop-filter: blur(20px);
  box-shadow: var(--shadow-sidebar);
  display: flex;
  flex-direction: column;
  transition: width 0.25s ease;
  z-index: 100;
  overflow: hidden;
}

.sidebar--collapsed {
  width: var(--sidebar-collapsed-width);
}

.sidebar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1.25rem 1rem;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}

.sidebar-logo {
  font-size: 1.25rem;
  font-weight: 700;
  color: var(--sidebar-text-active);
  letter-spacing: 0.04em;
}

.sidebar-toggle {
  background: transparent;
  border: none;
  color: var(--sidebar-text);
  cursor: pointer;
  padding: 0.4rem;
  border-radius: var(--radius-sm);
  transition: background 0.15s;
}
.sidebar-toggle:hover {
  background: var(--sidebar-item-hover);
}

.sidebar-nav {
  flex: 1;
  padding: 0.75rem 0.6rem;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.sidebar-item {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.65rem 0.85rem;
  border-radius: var(--radius-sm);
  color: var(--sidebar-text);
  text-decoration: none;
  font-size: 0.9rem;
  font-weight: 450;
  transition: all 0.15s ease;
}

.sidebar-item:hover {
  background: var(--sidebar-item-hover);
  color: var(--sidebar-text-active);
}

.sidebar-item--active {
  background: var(--sidebar-item-active);
  color: var(--sidebar-text-active);
  font-weight: 550;
}

.sidebar--collapsed .sidebar-item {
  justify-content: center;
  padding: 0.65rem;
}

.sidebar-footer {
  padding: 1rem;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
}

.sidebar-status {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.8rem;
  color: var(--sidebar-text);
}
```

### `styles/pages.css`

```css
/* ---------- 页面布局 ---------- */
.page {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 1rem;
  flex-wrap: wrap;
}

.page-title {
  font-size: 1.75rem;
  font-weight: 700;
  line-height: 1.1;
  letter-spacing: -0.01em;
}

.page-subtitle {
  color: var(--color-text-secondary);
  margin-top: 0.3rem;
  font-size: 0.9rem;
}

.page-header-actions {
  display: flex;
  gap: 0.6rem;
  flex-wrap: wrap;
}

/* ---------- Chat 选择器 ---------- */
.chat-selector {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-pill);
  background: var(--color-surface);
  padding: 0.55rem 1rem;
  min-width: 200px;
  font-size: 0.875rem;
  outline: none;
  transition: border-color 0.15s;
}
.chat-selector:focus {
  border-color: var(--color-accent);
}

/* ---------- 统计卡片 grid ---------- */
.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 0.75rem;
}

.stat-card {
  padding: 1rem 1.1rem;
  border-radius: var(--radius-md);
  background: var(--color-surface);
  box-shadow: var(--shadow-card);
  transition: box-shadow 0.2s;
}
.stat-card:hover {
  box-shadow: var(--shadow-card-hover);
}

.stat-card-label {
  display: block;
  font-size: 0.8rem;
  color: var(--color-text-muted);
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.stat-card-value {
  display: block;
  margin-top: 0.35rem;
  font-size: 1.15rem;
  font-weight: 600;
}

.stat-card-sub {
  display: block;
  margin-top: 0.2rem;
  font-size: 0.8rem;
  color: var(--color-text-muted);
}

/* ---------- DataCard ---------- */
.data-card {
  padding: 1.15rem 1.25rem;
  border-radius: var(--radius-lg);
  background: var(--color-surface);
  box-shadow: var(--shadow-card);
}

.data-card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1rem;
}

.data-card-head h2 {
  font-size: 1rem;
  font-weight: 600;
}

.data-card-actions {
  display: flex;
  gap: 0.5rem;
}

/* ---------- 双列布局 ---------- */
.two-col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1rem;
}

@media (max-width: 860px) {
  .two-col {
    grid-template-columns: 1fr;
  }
}

/* ---------- 列表 ---------- */
.list-stack {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.list-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.7rem 0.85rem;
  border-radius: var(--radius-sm);
  background: rgba(246, 248, 251, 0.85);
  gap: 0.75rem;
}

.list-row-block {
  padding: 0.7rem 0.85rem;
  border-radius: var(--radius-sm);
  background: rgba(246, 248, 251, 0.85);
}

.list-row-key {
  font-weight: 500;
  font-size: 0.875rem;
}

.list-row-muted {
  color: var(--color-text-muted);
  font-size: 0.82rem;
}

/* ---------- 记忆行 ---------- */
.memory-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 0.75rem;
  padding: 0.75rem 0.85rem;
  border-radius: var(--radius-sm);
  background: rgba(246, 248, 251, 0.85);
}

.memory-row-content { flex: 1; }
.memory-row-key { font-size: 0.82rem; color: var(--color-text-muted); margin-bottom: 0.2rem; }
.memory-row-value { font-size: 0.9rem; margin-bottom: 0.25rem; }

/* ---------- BarMeter ---------- */
.bar-meter { padding: 0.6rem 0; }

.bar-meter-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 0.875rem;
  margin-bottom: 0.4rem;
}

.bar-meter-track {
  height: 6px;
  border-radius: 3px;
  background: rgba(22, 56, 95, 0.06);
  overflow: hidden;
}

.bar-meter-fill {
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, #ff9f68 0%, #4f87e8 100%);
  transition: width 0.4s ease;
}

/* ---------- 任务行 ---------- */
.task-row {
  padding: 0.75rem 0.85rem;
  border-radius: var(--radius-sm);
  background: rgba(246, 248, 251, 0.85);
  cursor: pointer;
  border: 1px solid transparent;
  transition: all 0.15s;
}
.task-row:hover { border-color: var(--color-border-active); }
.task-row--active { border-color: var(--color-accent); background: var(--color-accent-soft); }
.task-row-id { font-size: 0.82rem; font-weight: 500; }
.task-row-text { font-size: 0.9rem; margin: 0.2rem 0; }

.task-detail-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.75rem;
}

.task-status {
  display: inline-block;
  padding: 0.2rem 0.6rem;
  border-radius: var(--radius-pill);
  font-size: 0.78rem;
  font-weight: 500;
}
.task-status--completed { background: var(--color-success-soft); color: var(--color-success); }
.task-status--running { background: var(--color-accent-soft); color: var(--color-accent); }
.task-status--failed { background: var(--color-danger-soft); color: var(--color-danger); }

.task-detail-events {
  margin: 0;
  white-space: pre-wrap;
  font-size: 0.82rem;
  line-height: 1.55;
  color: var(--color-text-secondary);
  font-family: "Outfit", "Noto Sans SC", monospace;
  max-height: 400px;
  overflow-y: auto;
}

/* ---------- 事件行 ---------- */
.event-row {
  padding: 0.75rem 0.85rem;
  border-radius: var(--radius-sm);
  background: rgba(246, 248, 251, 0.85);
}
.event-row-text { margin: 0.35rem 0; font-size: 0.9rem; }

.event-badge {
  display: inline-block;
  padding: 0.15rem 0.5rem;
  border-radius: var(--radius-pill);
  background: var(--color-accent-soft);
  color: var(--color-accent);
  font-size: 0.78rem;
  font-weight: 500;
}

/* ---------- 学习日志 ---------- */
.learning-entry {
  padding: 0.85rem;
  border-radius: var(--radius-sm);
  background: rgba(246, 248, 251, 0.85);
}

.learning-entry-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.5rem;
}

.learning-entry-body {
  margin: 0;
  white-space: pre-wrap;
  font-size: 0.85rem;
  line-height: 1.6;
  color: var(--color-text-secondary);
  font-family: "Outfit", "Noto Sans SC", sans-serif;
}

/* ---------- Auth ---------- */
.auth-notice {
  margin-top: 0.6rem;
  font-size: 0.85rem;
  color: var(--color-text-secondary);
}

.auth-link {
  color: var(--color-accent);
  text-decoration: none;
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
}
.auth-link:hover { text-decoration: underline; }

.connection-pill {
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  padding: 0.5rem 0.9rem;
  border-radius: var(--radius-pill);
  background: var(--color-surface);
  font-size: 0.85rem;
  border: 1px solid var(--color-border);
}

/* ---------- StatusDot ---------- */
.status-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.status-dot--online {
  background: var(--color-success);
  box-shadow: 0 0 0 4px var(--color-success-soft);
}
.status-dot--offline {
  background: var(--color-danger);
  box-shadow: 0 0 0 4px var(--color-danger-soft);
}

/* ---------- 空状态 ---------- */
.empty-state {
  color: var(--color-text-muted);
  font-size: 0.875rem;
  padding: 1rem 0;
}

/* ---------- AuthGuard 页面 ---------- */
.auth-guard-page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: 2rem;
}

.auth-guard-card {
  max-width: 500px;
  width: 100%;
  padding: 2rem;
  border-radius: var(--radius-lg);
  background: var(--color-surface);
  box-shadow: var(--shadow-card-hover);
  backdrop-filter: blur(16px);
}

.auth-guard-card h1 {
  font-size: 1.5rem;
  margin: 0.75rem 0 0;
}

.auth-guard-eyebrow {
  text-transform: uppercase;
  letter-spacing: 0.2em;
  color: var(--color-text-muted);
  font-size: 0.72rem;
  font-weight: 600;
}

.auth-guard-hint {
  color: var(--color-text-secondary);
  margin-top: 0.6rem;
  font-size: 0.9rem;
}
.auth-guard-hint code {
  background: rgba(22, 56, 95, 0.06);
  padding: 0.15rem 0.4rem;
  border-radius: 4px;
  font-size: 0.82rem;
}

.auth-guard-form {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  margin-top: 1rem;
}

.auth-guard-form textarea {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 0.75rem 0.9rem;
  background: rgba(255, 255, 255, 0.6);
  resize: vertical;
  font-size: 0.875rem;
  outline: none;
  transition: border-color 0.15s;
}
.auth-guard-form textarea:focus {
  border-color: var(--color-accent);
}
```

---

## 8. CSS 导入顺序

在 `globals.css` 末尾添加：

```css
@import "./sidebar.css";
@import "./pages.css";
```

或在 `main.tsx` 中分别导入三个文件：

```tsx
import "./styles/globals.css";
import "./styles/sidebar.css";
import "./styles/pages.css";
```

---

## 9. 文件操作清单

以下是 Claude Code 需要执行的操作，按顺序：

### 9.1 安装依赖

```bash
cd desktop
npm install react-router-dom@^6.28.0 lucide-react@^0.460.0
```

### 9.2 删除旧文件

```bash
rm desktop/src/App.tsx
rm desktop/src/styles.css
```

> **不删除** `api.ts`（完整复用）、`main.tsx`（重写）、`vite-env.d.ts`（保留）。

### 9.3 创建新文件

按第 2 节目录结构创建所有文件。具体内容见第 3-7 节。

文件创建顺序建议：
1. `styles/globals.css` → `styles/sidebar.css` → `styles/pages.css`
2. `components/StatusDot.tsx` → `EmptyState.tsx` → `StatCard.tsx` → `DataCard.tsx` → `BarMeter.tsx` → `EventBadge.tsx`
3. `components/AuthGuard.tsx`
4. `components/Sidebar.tsx`
5. `components/AppShell.tsx`
6. `pages/OverviewPage.tsx` → `MemoryPage.tsx` → `PersonaPage.tsx` → `TasksPage.tsx` → `EventsPage.tsx` → `AuthPage.tsx` → `SettingsPage.tsx`
7. `router.tsx`
8. 重写 `main.tsx`

### 9.4 更新 `index.html`

无需改动——现有的 `index.html` 已经有 `<div id="root">` 和 `<script type="module" src="/src/main.tsx">`。

### 9.5 验证

```bash
cd desktop && npm run build
```

应零错误编译。如果有 TS 类型报错，根据 `api.ts` 中的现有类型定义修正。

---

## 10. 迁移对照表

| 旧 App.tsx 功能块 | 新位置 | 备注 |
|---|---|---|
| Bootstrap token / manual auth | `AuthGuard.tsx` | 提取为独立守卫 |
| hero + reload + evolve 按钮 | `OverviewPage.tsx` | 页头操作 |
| 状态面板 (chat_count 等) | `OverviewPage.tsx` | StatCard 组件 |
| 兴趣图谱 | `MemoryPage.tsx` | BarMeter 组件 |
| 记忆管理 | `MemoryPage.tsx` | 含删除操作 |
| Auth 面板 + OAuth + Codex | `AuthPage.tsx` | 完整独立页 |
| 事件流 | `EventsPage.tsx` | SSE 连接 |
| 任务视图 + 详情 | `TasksPage.tsx` | 双栏列表+详情 |
| 学习日志 | `PersonaPage.tsx` | 含进化操作 |
| toolbar (chat 选择器 + 状态) | 各页面内置选择器 + Sidebar 底部状态 | 拆散到各页 |
| 30s 定时轮询 | 各页面独立 useEffect | 按需轮询 |
| latency telemetry | 暂不迁移（可后续添加到 hooks/ | 降低首版复杂度 |
| Notification 权限申请 | `EventsPage.tsx` | SSE 事件触发 |

---

## 11. 后续扩展点

此蓝图只重构前端结构，以下功能可在后续迭代中添加：

1. **`SettingsPage`** — 在线编辑 `.env`、Heartbeat 参数、LLM Router 配置
   - 需要后端新增 `GET/POST /api/config` 端点
2. **`SkillsPage`** — Skill 系统可视化（列表、状态、使用统计）
   - 对接 Skill `_index.json` 和 `_registry.json`
3. **`ChatPage`** — 内嵌对话界面（参考 AstrBot 的 WebChat）
   - 需要后端新增 WebSocket 消息通道
4. **深色模式** — CSS 变量已预备，只需添加 `[data-theme="dark"]` 覆盖
5. **i18n** — 当前硬编码中文，可引入 `react-intl` 或简单 JSON map
6. **移动端适配** — Sidebar 在窄屏切换为 overlay 抽屉

---

## 12. 设计原则回顾

- **零后端改动**：所有 API 端点和 `api.ts` 保持原样
- **渐进增强**：7 个页面可以独立开发和测试
- **AstrBot 模式借鉴**：Sidebar + 路由 + 分页配置，但用 React 而非 Vue
- **保持 Lapwing 美学**：暖色渐变背景、毛玻璃卡片、柔和阴影，但升级字体和配色
- **最小依赖**：只添加 `react-router-dom` 和 `lucide-react`