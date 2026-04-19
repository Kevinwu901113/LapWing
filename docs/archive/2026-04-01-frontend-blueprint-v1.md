# Lapwing Frontend Redesign — Implementation Blueprint

> **For**: Claude Code execution
> **Stack**: Tauri 1.x + React 18 + TypeScript + Vite
> **Target**: Windows exe remote client connecting to PVE server
> **Language**: 全中文 UI
> **Design**: Dark theme, Lapwing light-blue (#a8d4f0) accent

---

## 1. Design System

### 1.1 Color Tokens

Create `desktop/src/styles/tokens.css`:

```css
:root {
  /* Backgrounds */
  --bg-base: #0f1117;
  --bg-surface: #161821;
  --bg-card: #1c1e2a;
  --bg-input: #252736;
  --bg-hover: rgba(255,255,255,0.04);

  /* Accent — Lapwing blue */
  --accent: #a8d4f0;
  --accent-dim: rgba(168,212,240,0.1);
  --accent-border: rgba(168,212,240,0.15);

  /* Text */
  --text-primary: #e2e4e9;
  --text-secondary: #8b8fa3;
  --text-muted: #6b7084;

  /* Semantic */
  --green: #4ade80;
  --green-dim: rgba(74,222,128,0.1);
  --amber: #fbbf24;
  --amber-dim: rgba(251,191,36,0.1);
  --red: #f87171;
  --red-dim: rgba(248,113,113,0.1);
  --blue: #60a5fa;
  --blue-dim: rgba(59,130,246,0.15);

  /* Borders */
  --border: rgba(255,255,255,0.06);
  --border-input: rgba(255,255,255,0.08);

  /* Layout */
  --sidebar-collapsed: 56px;
  --sidebar-expanded: 200px;
  --topbar-height: 48px;
  --radius-sm: 6px;
  --radius-md: 8px;
  --radius-lg: 10px;
  --radius-xl: 12px;

  /* Typography */
  --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  --font-mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace;
}

/* Light mode override */
[data-theme="light"] {
  --bg-base: #f5f5f7;
  --bg-surface: #ffffff;
  --bg-card: #ffffff;
  --bg-input: #f0f0f2;
  --bg-hover: rgba(0,0,0,0.03);
  --accent: #3a8bc7;
  --accent-dim: rgba(58,139,199,0.08);
  --accent-border: rgba(58,139,199,0.15);
  --text-primary: #1a1a2e;
  --text-secondary: #6b7084;
  --text-muted: #9ca3af;
  --border: rgba(0,0,0,0.08);
  --border-input: rgba(0,0,0,0.12);
  --green: #16a34a;
  --green-dim: rgba(22,163,74,0.08);
  --amber: #d97706;
  --amber-dim: rgba(217,119,6,0.08);
  --red: #dc2626;
  --red-dim: rgba(220,38,38,0.08);
  --blue: #2563eb;
  --blue-dim: rgba(37,99,235,0.08);
}
```

### 1.2 New Dependencies

```json
{
  "recharts": "^2.12.0",
  "react-ace": "^12.0.0",
  "ace-builds": "^1.35.0"
}
```

- `recharts`: Charts (area chart, ring/radial charts)
- `react-ace` + `ace-builds`: Markdown editor for soul.md/voice.md/constitution.md

### 1.3 Typography

- Body: 13px, `var(--font-sans)`, color `var(--text-primary)`
- Labels/hints: 11px, color `var(--text-muted)`
- Section titles: 12px, weight 500, color `var(--text-muted)`, uppercase, letter-spacing 0.5px
- Page titles: 15px, weight 500
- Metric numbers: 20-24px, weight 500

---

## 2. Layout

### 2.1 Sidebar — hover expand

```
Default: 56px wide, icon-only
Hover:   200px wide, icon + text label
Transition: width 200ms ease
```

Implementation: CSS `width` transition on `.sidebar`. On `mouseenter` → expand, `mouseleave` → collapse. No click toggle needed (remove current toggle button).

Bottom section (above settings icon):
- Dark/light mode toggle switch
- Settings icon (always at very bottom)

### 2.2 Router

```tsx
export const router = createHashRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: "chat", element: <ChatPage /> },
      { path: "memory", element: <MemoryPage /> },
      { path: "persona", element: <PersonaPage /> },
      { path: "tasks", element: <TasksPage /> },
      { path: "logs", element: <LogsPage /> },
      { path: "settings", element: <SettingsPage /> },
    ],
  },
]);
```

### 2.3 File Structure

```
desktop/src/
  styles/
    tokens.css              # Color/layout tokens
    global.css              # Reset + base styles
  components/
    AppShell.tsx             # Layout shell (sidebar + content area)
    Sidebar.tsx              # Hover-expand sidebar
    RingChart.tsx            # Reusable ring/donut gauge
    AreaChart.tsx            # Reusable area chart wrapper
    HeatmapBar.tsx           # Heartbeat heatmap bar
    StatusDot.tsx             # Online/offline dot (keep)
    ChannelBadge.tsx         # TG/QQ/Desktop channel indicator
    TabBar.tsx               # Reusable horizontal tab bar
    MarkdownEditor.tsx       # Ace editor configured for markdown
    Toggle.tsx               # On/off toggle switch
    SearchInput.tsx          # Search input with icon
    Pagination.tsx           # Page number pagination
    MemoryItem.tsx           # Single memory entry row
    TaskItem.tsx             # Single task entry row
    ProviderCard.tsx         # Model provider card (settings)
    SlotCard.tsx             # Model slot assignment card
    ChatBubble.tsx           # Single chat message bubble
    ToolStatus.tsx           # Inline tool status indicator
    AgentPanel.tsx           # Right-side execution panel
    ThemeToggle.tsx          # Dark/light switch
  pages/
    DashboardPage.tsx        # 仪表盘
    ChatPage.tsx             # 对话
    MemoryPage.tsx           # 记忆 (5 tabs)
    PersonaPage.tsx          # 人格 (5 tabs)
    TasksPage.tsx            # 任务
    LogsPage.tsx             # 日志
    SettingsPage.tsx         # 设置 (7 tabs)
  hooks/
    useWebSocket.ts          # WebSocket connection management
    useTheme.ts              # Dark/light theme toggle
    useEventStream.ts        # SSE event stream
    useLogStream.ts          # SSE log stream
    useServerStatus.ts       # Polling server status
  api.ts                     # API client (updated)
  router.tsx                 # Router config
  main.tsx                   # Entry point
```

---

## 3. Pages

### 3.1 DashboardPage — 仪表盘

**Default landing page.**

Layout: 3 rows

**Row 1 — 4 columns: Server gauges + Channels**

| Component | Data source | Visual |
|-----------|------------|--------|
| CPU usage | New: `GET /api/system/stats` | RingChart (blue) |
| Memory usage | `GET /api/system/stats` | RingChart (accent) |
| Disk usage | `GET /api/system/stats` | RingChart (green) |
| Channels | `GET /api/config/platforms` | 3-row list: TG/QQ/Desktop with status dots |

**Row 2 — 2 columns: API quota + 24h conversation chart**

| Component | Data source | Visual |
|-----------|------------|--------|
| API quotas | New: `GET /api/system/api-usage` | 3 donut charts side by side |
| 24h conversations | `GET /api/chats` + events | Area chart (recharts) |

**Row 3 — Full width: Heartbeat heatmap**

| Component | Data source | Visual |
|-----------|------------|--------|
| Heartbeat actions | New: `GET /api/heartbeat/status` | HeatmapBar per action (24 segments = 24 hours) |

**New backend endpoints needed:**

```python
# GET /api/system/stats — server hardware metrics
@app.get("/api/system/stats")
async def get_system_stats():
    import psutil
    return {
        "cpu_percent": psutil.cpu_percent(interval=0.5),
        "cpu_model": "Xeon E-2174G",  # or read from /proc/cpuinfo
        "memory_total_gb": round(psutil.virtual_memory().total / (1024**3), 1),
        "memory_used_gb": round(psutil.virtual_memory().used / (1024**3), 1),
        "memory_percent": psutil.virtual_memory().percent,
        "disk_total_gb": round(psutil.disk_usage('/').total / (1024**3), 1),
        "disk_used_gb": round(psutil.disk_usage('/').used / (1024**3), 1),
        "disk_percent": round(psutil.disk_usage('/').percent, 1),
    }

# GET /api/system/api-usage — LLM API quota/usage
# Implementation depends on provider APIs. For now return config info.
@app.get("/api/system/api-usage")
async def get_api_usage():
    # Placeholder — real implementation scrapes provider dashboards
    # or tracks token usage internally
    return {
        "providers": [
            {"name": "MiniMax M2.7", "used": 0, "limit": 0, "unit": "tokens"},
            {"name": "GLM-4-Flash", "used": 0, "limit": 0, "unit": "tokens"},
            {"name": "NIM Llama", "used": 0, "limit": 0, "unit": "tokens"},
        ]
    }

# GET /api/heartbeat/status — heartbeat action states
@app.get("/api/heartbeat/status")
async def get_heartbeat_status():
    # Read from heartbeat engine state
    if container.heartbeat is None:
        return {"actions": [], "interval_seconds": 0}
    hb = container.heartbeat
    return {
        "interval_seconds": hb._interval_seconds,
        "actions": [
            {
                "name": action.name,
                "last_run": getattr(action, '_last_run', None),
                "enabled": getattr(action, 'enabled', True),
                # 24h execution history: list of hour indices where action ran
                "history_24h": getattr(action, '_run_history', []),
            }
            for action in hb.registry.all()
        ],
    }
```

**Add `psutil` to requirements.txt.**

### 3.2 ChatPage — 对话

**Two-panel layout: chat area (flex:1) + execution panel (260px right).**

**Chat area:**
- Header: Lapwing avatar + name + online status
- Body: Scrollable message list
  - `ChatBubble` component: left (Lapwing) / right (Kevin) alignment
  - `ToolStatus` component: centered inline status pills with pulse animation
  - System messages (timestamps, session boundaries)
- Input: Textarea (auto-resize) + send button
- WebSocket connection via `useWebSocket` hook

**Execution panel (right side):**
- Current task card: agent name + status + step log
- Tool call log: tool name + duration + success/fail
- Session info: channel, tool count, context tokens, model name

**WebSocket integration (`useWebSocket.ts`):**

```typescript
export function useWebSocket(serverUrl: string, token: string) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [status, setStatus] = useState<"connecting"|"connected"|"disconnected">("disconnected");
  const [toolStatus, setToolStatus] = useState<ToolStatusInfo | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  // Connect to ws://{serverUrl}/ws/chat?token={token}
  // Handle message types: reply, interim, status, typing, error
  // Auto-reconnect with exponential backoff
  // Send: { type: "message", content: "..." }
}
```

**Chat message types:**

```typescript
type ChatMessage = {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: string;
  toolCalls?: ToolCallInfo[];  // attached tool call results
};

type ToolStatusInfo = {
  phase: "thinking" | "searching" | "executing" | "done";
  text: string;
  toolName?: string;
};
```

### 3.3 MemoryPage — 记忆

**5 horizontal tabs:**

| Tab | 中文 | Component | Data source |
|-----|------|-----------|-------------|
| entries | 记忆条目 | MemoryEntriesTab | `GET /api/memory?chat_id=...` |
| identity | 身份笔记 | IdentityNotesTab | `GET /api/persona/files` (kevin/self) |
| interests | 兴趣图谱 | InterestGraphTab | `GET /api/interests?chat_id=...` |
| timeline | 对话摘要 | ConversationTimelineTab | New: `GET /api/memory/summaries` |
| knowledge | 知识笔记 | KnowledgeNotesTab | New: `GET /api/knowledge/notes` |

**Tab: 记忆条目**
- Color-coded category bars: blue (Kevin), accent (self), green (fact), amber (interest)
- Search input + filter dropdown
- Paginated list (15 items/page)
- Delete button per item

**Tab: 身份笔记**
- Two side-by-side MarkdownEditor instances
- Left: KEVIN.md, Right: SELF.md
- Save button per editor

**Tab: 兴趣图谱**
- Bubble chart (recharts ScatterChart or custom SVG)
- Each bubble = one interest topic, size = weight, color by category
- Hover shows detail

**Tab: 对话摘要**
- Vertical timeline with date labels
- Each entry: date + title + summary text
- Scrollable

**Tab: 知识笔记**
- Card list of knowledge notes (topic + excerpt + date)
- Click to expand full content
- Delete button

**New backend endpoints:**

```python
@app.get("/api/memory/summaries")
async def get_conversation_summaries():
    from config.settings import CONVERSATION_SUMMARIES_DIR
    if not CONVERSATION_SUMMARIES_DIR.exists():
        return {"items": []}
    items = []
    for f in sorted(CONVERSATION_SUMMARIES_DIR.glob("*.md"), reverse=True):
        items.append({
            "filename": f.name,
            "date": f.stem,
            "content": f.read_text(encoding="utf-8"),
        })
    return {"items": items[:50]}

@app.get("/api/knowledge/notes")
async def get_knowledge_notes():
    from config.settings import DATA_DIR
    kdir = DATA_DIR / "knowledge"
    if not kdir.exists():
        return {"items": []}
    items = []
    for f in sorted(kdir.glob("*.md"), reverse=True, key=lambda p: p.stat().st_mtime):
        items.append({
            "topic": f.stem,
            "content": f.read_text(encoding="utf-8"),
            "updated_at": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat(),
        })
    return {"items": items}

@app.delete("/api/knowledge/notes/{topic}")
async def delete_knowledge_note(topic: str):
    from config.settings import DATA_DIR
    path = DATA_DIR / "knowledge" / f"{topic}.md"
    if not path.exists():
        raise HTTPException(404, f"Note '{topic}' not found")
    path.unlink()
    return {"success": True}
```

### 3.4 PersonaPage — 人格

**5 horizontal tabs:**

| Tab | 中文 | Component | Data source |
|-----|------|-----------|-------------|
| soul | 核心人格 | SoulEditorTab | `GET/POST /api/persona/files/soul` |
| voice | 说话方式 | VoiceEditorTab | `GET/POST /api/persona/files/voice` |
| constitution | 宪法 | ConstitutionTab | `GET/POST /api/persona/files/constitution` |
| changelog | 进化历史 | EvolutionHistoryTab | New: `GET /api/persona/changelog` |
| journal | 自省日志 | JournalTab | `GET /api/learnings` |

**Tab: 核心人格 / 说话方式 / 宪法**
- Layout: left = MarkdownEditor (flex:1), right = info sidebar (280px)
- Info sidebar: file path, size, last modified, last modified by (auto evolution vs manual)
- Recent evolution diffs (green add / red delete)
- Buttons: "保存并重载" (primary), "重置" (ghost)
- Constitution tab: add a "只读模式" toggle (default on), must explicitly unlock to edit

**Tab: 进化历史**
- Vertical timeline of changelog entries
- Each entry shows: date, summary text, diff hunks (green/red)
- New endpoint:

```python
@app.get("/api/persona/changelog")
async def get_evolution_changelog():
    from config.settings import CHANGELOG_PATH
    if not CHANGELOG_PATH.exists():
        return {"entries": []}
    content = CHANGELOG_PATH.read_text(encoding="utf-8")
    # Parse markdown sections into entries
    entries = _parse_changelog(content)
    return {"entries": entries}
```

**Tab: 自省日志**
- Reuse existing `GET /api/learnings` (journal files)
- Card list with date + content preview
- Click to expand full journal entry

**Manual evolution trigger button** (bottom of sidebar on soul/voice tabs):
- Calls `POST /api/evolve`
- Shows loading state, then success/error

### 3.5 TasksPage — 任务

**Simple list view.**

- Header: page title + badge showing pending count
- Filter row: status filter (全部 / 待执行 / 已完成)
- Task items: icon (clock=pending, check=done, spinner=running) + title + time + recurrence + delete button
- Data: `GET /api/scheduled-tasks`
- Delete: `DELETE /api/scheduled-tasks/{id}`

### 3.6 LogsPage — 日志

**Full-page log viewer.**

- Top control bar:
  - Level dropdown: 全部 / DEBUG / INFO / WARNING / ERROR
  - Module dropdown: 全部 / core.brain / core.llm_router / core.task_runtime / memory / tools / heartbeat / api
  - Search input: free text filter
  - Live toggle: green pulsing dot + "实时" label (SSE stream on/off)
- Log area: monospace terminal style
  - Each line: timestamp | level (colored) | logger name | message
  - Auto-scroll when live mode is on
  - Click a line to expand full message (for multi-line errors)
- Data: `GET /api/logs/stream` (SSE) for live, `GET /api/logs/recent` for historical

**`useLogStream.ts`:**

```typescript
export function useLogStream(level: string, module: string) {
  const [lines, setLines] = useState<LogLine[]>([]);
  const [live, setLive] = useState(true);
  // Connect to /api/logs/stream?level={level}&module={module}
  // Parse SSE data events into LogLine objects
  // Append to lines array (cap at 2000 lines, drop oldest)
}
```

### 3.7 SettingsPage — 设置

**Left vertical tabs (160px) + content area.**

7 tabs:

#### Tab 1: 模型提供商
- Provider card list (icon + name + base_url + model tags + edit/delete buttons)
- "添加提供商" button at bottom (dashed border)
- Click edit → modal/inline form: name, api_type, base_url, api_key, models list
- Data: `GET /api/model-routing/config`
- Actions: `POST/PUT/DELETE /api/model-routing/providers`

#### Tab 2: 槽位分配
- 2x3 grid of SlotCards
- Each card: slot name (Chinese) + description + dropdown selecting provider/model combo
- Data: `GET /api/model-routing/config` (slots + providers)
- Action: `PUT /api/model-routing/slots/{id}`
- "重载路由" button: `POST /api/model-routing/reload`

#### Tab 3: 平台连接
- Telegram section: enabled badge, token preview (masked), proxy url, kevin_id
- QQ section: enabled toggle, ws_url, self_id, kevin_id, group_ids, cooldown
- Desktop section: connection status, token management
- Data: `GET /api/config/platforms`
- Note: For V1, read-only display. Write-back requires env file modification — mark as "需要重启生效" for changed values.

#### Tab 4: 功能开关
- Toggle list:
  - Shell 执行 (SHELL_ENABLED)
  - 联网搜索 (CHAT_WEB_TOOLS_ENABLED)
  - 技能系统 (SKILLS_ENABLED)
  - 经验技能 (EXPERIENCE_SKILLS_ENABLED)
  - Session 管理 (SESSION_ENABLED)
  - 记忆 CRUD (MEMORY_CRUD_ENABLED)
  - 自动记忆提取 (AUTO_MEMORY_EXTRACT_ENABLED)
  - 自定义日程 (SELF_SCHEDULE_ENABLED)
  - QQ 通道 (QQ_ENABLED)
- Each row: label + description + Toggle component
- Data: `GET /api/config/features`
- Note: V1 read-only. Write-back needs `POST /api/config/features` (writes to runtime overlay or env).

#### Tab 5: 安全
- Owner IDs list (display + add/remove)
- Trusted IDs list
- Desktop default owner toggle
- Bootstrap token: show current, regenerate button
- API session TTL setting

#### Tab 6: 服务器
- API host + port display
- CORS allowed origins list
- Log level dropdown
- Restart hint ("部分设置修改后需要重启服务")

#### Tab 7: 关于
- Lapwing version
- Server uptime
- Python version
- Key dependency versions
- Git commit hash (if available)
- Links: Gitea repo

---

## 4. Vite Config Update

```typescript
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 1420,
    strictPort: true,
    host: "0.0.0.0",
    proxy: {
      "/api": "http://127.0.0.1:8765",
      "/ws": {
        target: "ws://127.0.0.1:8765",
        ws: true,
      },
    },
  },
});
```

---

## 5. Tauri Config Update

For remote connection support, update `tauri.conf.json`:

```json
{
  "build": {
    "beforeDevCommand": "npm run dev",
    "beforeBuildCommand": "npm run build",
    "devPath": "http://localhost:1420",
    "distDir": "../dist"
  },
  "package": {
    "productName": "Lapwing",
    "version": "0.2.0"
  },
  "tauri": {
    "allowlist": {
      "notification": { "all": true },
      "http": {
        "all": true,
        "scope": ["https://*.lapw1ng.com/*", "http://127.0.0.1:*/*"]
      }
    },
    "windows": [
      {
        "title": "Lapwing",
        "width": 1440,
        "height": 900,
        "minWidth": 1024,
        "minHeight": 680,
        "resizable": true,
        "decorations": true
      }
    ],
    "bundle": {
      "active": true,
      "identifier": "com.lapwing.desktop",
      "targets": ["msi", "nsis"],
      "icon": [
        "icons/32x32.png",
        "icons/128x128.png",
        "icons/128x128@2x.png",
        "icons/icon.ico"
      ]
    }
  }
}
```

---

## 6. API Client Update

Update `desktop/src/api.ts` to support:

1. **Configurable server URL** (not just localhost):

```typescript
// Read from localStorage or env
const getServerUrl = (): string => {
  return localStorage.getItem("lapwing_server_url") || 
    import.meta.env.VITE_LAPWING_API_BASE || 
    "http://127.0.0.1:8765";
};

const getAuthToken = (): string => {
  return localStorage.getItem("lapwing_desktop_token") || "";
};
```

2. **Add all new endpoint functions:**

```typescript
// System
export function getSystemStats() { return fetchJson<SystemStats>("/api/system/stats"); }
export function getApiUsage() { return fetchJson<ApiUsage>("/api/system/api-usage"); }
export function getHeartbeatStatus() { return fetchJson<HeartbeatStatus>("/api/heartbeat/status"); }

// Config
export function getPlatformConfig() { return fetchJson<PlatformConfig>("/api/config/platforms"); }
export function getFeatureFlags() { return fetchJson<FeatureFlags>("/api/config/features"); }

// Persona
export function getPersonaFiles() { return fetchJson<PersonaFiles>("/api/persona/files"); }
export function updatePersonaFile(name: string, content: string) { ... }
export function getChangelog() { return fetchJson<Changelog>("/api/persona/changelog"); }

// Memory
export function getConversationSummaries() { return fetchJson<Summaries>("/api/memory/summaries"); }
export function getKnowledgeNotes() { return fetchJson<KnowledgeNotes>("/api/knowledge/notes"); }
export function deleteKnowledgeNote(topic: string) { ... }

// Tasks
export function getScheduledTasks() { return fetchJson<ScheduledTasks>("/api/scheduled-tasks"); }
export function deleteScheduledTask(id: string) { ... }

// Logs
export function getRecentLogs(lines?: number, level?: string) { ... }
// Note: log stream uses SSE, not fetch — handled in useLogStream hook
```

---

## 7. Connection Setup Flow

When the exe launches for the first time, it needs to connect to the server.

**First-run flow:**
1. Show a connection setup screen: input server URL + bootstrap token
2. Call `POST /api/auth/desktop-token` with bootstrap token
3. Store returned long-lived token in localStorage
4. Store server URL in localStorage
5. Proceed to main app

**Subsequent launches:**
1. Read server URL + token from localStorage
2. Attempt connection (health check via `GET /api/status`)
3. If fails → show connection setup screen
4. If succeeds → proceed to main app

Create `pages/ConnectionPage.tsx` for this flow.
Update router to check connection state before loading AppShell.

---

## 8. DELETE old files

```
DELETE  desktop/src/pages/OverviewPage.tsx     → replaced by DashboardPage.tsx
DELETE  desktop/src/pages/EventsPage.tsx       → merged into DashboardPage (events stream)
DELETE  desktop/src/pages/AuthPage.tsx         → replaced by ConnectionPage.tsx
DELETE  desktop/src/components/BarMeter.tsx     → replaced by RingChart
DELETE  desktop/src/components/DataCard.tsx     → replaced by new card components
DELETE  desktop/src/components/EmptyState.tsx   → inline where needed
DELETE  desktop/src/components/EventBadge.tsx   → not needed
DELETE  desktop/src/components/StatCard.tsx     → replaced by RingChart cards
```

---

## 9. Implementation Order

```
1. Design system: tokens.css, global.css
2. Layout: AppShell, Sidebar (hover-expand), ThemeToggle
3. Shared components: RingChart, TabBar, Toggle, SearchInput, Pagination, MarkdownEditor
4. DashboardPage (uses RingChart, AreaChart, HeatmapBar, ChannelBadge)
5. ChatPage (uses ChatBubble, ToolStatus, AgentPanel, useWebSocket)
6. MemoryPage (5 tabs, uses MemoryItem, SearchInput, Pagination)
7. PersonaPage (5 tabs, uses MarkdownEditor)
8. TasksPage (uses TaskItem)
9. LogsPage (uses useLogStream)
10. SettingsPage (7 tabs, uses ProviderCard, SlotCard, Toggle)
11. ConnectionPage + first-run flow
12. API client update (api.ts)
13. Backend: add missing endpoints (system/stats, heartbeat/status, etc.)
14. Tauri config + build test
```

---

## 10. Backend Endpoints — Complete Summary

All endpoints the frontend consumes. **Bold** = new (needs to be added).

```
# Existing
GET  /api/status
GET  /api/chats
GET  /api/memory?chat_id=...
POST /api/memory/delete
GET  /api/interests?chat_id=...
GET  /api/learnings
POST /api/reload
POST /api/evolve
GET  /api/model-routing/config
POST /api/model-routing/providers
PUT  /api/model-routing/providers/{id}
DEL  /api/model-routing/providers/{id}
PUT  /api/model-routing/slots/{id}
POST /api/model-routing/reload
GET  /api/events/stream
GET  /api/tasks
GET  /api/tasks/{id}

# Added in backend restructuring (Phase J/K)
GET  /api/logs/stream
GET  /api/logs/recent
GET  /api/config/platforms
GET  /api/config/features
GET  /api/persona/files
POST /api/persona/files/{name}
GET  /api/scheduled-tasks
DEL  /api/scheduled-tasks/{id}
POST /api/auth/desktop-token
WS   /ws/chat

# NEW — add for frontend
GET  /api/system/stats              # CPU/RAM/disk (psutil)
GET  /api/system/api-usage          # LLM token usage
GET  /api/heartbeat/status          # Heartbeat action states
GET  /api/persona/changelog         # Evolution history
GET  /api/memory/summaries          # Conversation summaries
GET  /api/knowledge/notes           # Knowledge notes list
DEL  /api/knowledge/notes/{topic}   # Delete knowledge note
```

---

## 11. Verification

```bash
# Frontend
cd desktop
npm install
npm run dev  # should serve on :1420

# Type check
npx tsc --noEmit

# Build
npm run build  # produces dist/

# Tauri build (Windows exe)
npm run tauri build
# Output: src-tauri/target/release/Lapwing.exe + installer

# Backend endpoints
curl http://127.0.0.1:8765/api/system/stats
curl http://127.0.0.1:8765/api/heartbeat/status
curl http://127.0.0.1:8765/api/persona/changelog
curl http://127.0.0.1:8765/api/memory/summaries
curl http://127.0.0.1:8765/api/knowledge/notes
```
