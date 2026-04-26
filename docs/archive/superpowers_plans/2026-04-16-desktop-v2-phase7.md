# Desktop v2 Phase 7 — "Her Room" Frontend Implementation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform the existing desktop-v2 skeleton into Lapwing's "room" — a functional companion desktop with real-time chat, task visibility, status awareness, and configuration management (P0-P2 of the blueprint).

**Architecture:** Extend the existing React 19 + Zustand + Tailwind app. Add v2 API client functions, new Zustand stores (tasks, status), upgrade SSE to v2 with Last-Event-ID, restructure navigation to 5 items, replace AgentPanel with TaskSidebar in ChatPage, and rebuild SettingsPage with connection/model/permissions tabs.

**Tech Stack:** React 19, TypeScript, Zustand 5, Tailwind CSS 4, shadcn/ui, react-markdown, Tauri v2, WebSocket, SSE (EventSource)

---

## File Structure

### New Files
- `src/types/events.ts` — SSE v2 event types
- `src/types/tasks-v2.ts` — Task v2 types (from `/api/v2/tasks`)
- `src/types/status-v2.ts` — Status v2 types
- `src/types/permissions.ts` — Permission types
- `src/types/models.ts` — Model routing v2 types
- `src/lib/api-v2.ts` — v2 REST API client functions
- `src/stores/tasks.ts` — Task Zustand store
- `src/stores/status.ts` — Status Zustand store
- `src/hooks/useSSEv2.ts` — SSE v2 hook with Last-Event-ID + typed event dispatch
- `src/hooks/useStatus.ts` — Status polling + SSE subscription hook
- `src/hooks/useTasks.ts` — Task loading + SSE subscription hook
- `src/components/tasks/TaskSidebar.tsx` — Right sidebar with task cards
- `src/components/tasks/TaskCard.tsx` — Single task with expand/collapse
- `src/components/tasks/AgentMessageList.tsx` — Agent conversation flow inside TaskCard
- `src/components/status/StatusIndicator.tsx` — Sidebar bottom status dot + label
- `src/components/settings/ConnectionTab.tsx` — Server URL + token + test
- `src/components/settings/ModelsTab.tsx` — Slot configuration via v2 API
- `src/components/settings/PermissionsTab.tsx` — User permission CRUD

### Modified Files
- `src/router.tsx` — Simplify to 5 routes (chat, notes, identity, system, settings)
- `src/components/layout/Sidebar.tsx` — 5 nav items + StatusIndicator at bottom
- `src/components/layout/AppShell.tsx` — Minor: pass through, no structural change
- `src/pages/ChatPage.tsx` — Replace AgentPanel with TaskSidebar
- `src/pages/SettingsPage.tsx` — Full rewrite: 4 tabs (connection, models, permissions, about)
- `src/lib/api.ts` — Add `getAuthHeaders` export for reuse

### Removed/Deprecated (routes removed, files kept for reference)
- `src/pages/TaskCenterPage.tsx` — Functionality moved into TaskSidebar
- `src/pages/DashboardPage.tsx` — Will become SystemPage in P5
- `src/pages/SensingPage.tsx` — Removed from nav
- `src/pages/MemoryPage.tsx` — Will become NotesPage in P3
- `src/pages/PersonaPage.tsx` — Will become IdentityPage in P4
- `src/pages/ModelRoutingPage.tsx` — Functionality moved into SettingsPage ModelsTab

---

## Task 1: Types — v2 API Type Definitions

**Files:**
- Create: `desktop-v2/src/types/events.ts`
- Create: `desktop-v2/src/types/tasks-v2.ts`
- Create: `desktop-v2/src/types/status-v2.ts`
- Create: `desktop-v2/src/types/permissions.ts`
- Create: `desktop-v2/src/types/models.ts`

- [ ] **Step 1: Create `events.ts`**

```typescript
// src/types/events.ts
export interface SSEEvent {
  event_id: string;
  event_type: string;
  timestamp: string;
  actor?: string;
  task_id?: string;
  payload: Record<string, unknown>;
}

// Specific event types that come through SSE
export interface AgentTaskEvent extends SSEEvent {
  event_type: "agent.task_queued" | "agent.task_started" | "agent.task_done" | "agent.task_failed" | "agent.tool_called" | "agent.message";
}

export interface StatusChangedEvent extends SSEEvent {
  event_type: "status.changed";
  payload: {
    state: "idle" | "thinking" | "working" | "browsing";
    current_task_id?: string;
  };
}
```

- [ ] **Step 2: Create `tasks-v2.ts`**

```typescript
// src/types/tasks-v2.ts
export interface TaskV2 {
  task_id: string;
  parent_task_id?: string;
  title: string;
  status: "queued" | "running" | "done" | "failed" | "cancelled";
  agent_name?: string;
  created_at: string;
  updated_at?: string;
}

export interface AgentMessage {
  event_id: string;
  timestamp: string;
  actor: string;        // "lapwing", "team_lead", "researcher", etc.
  content: string;
  event_type: string;   // "agent.message", "agent.tool_called", etc.
  tool_name?: string;
  tool_args?: Record<string, unknown>;
}
```

- [ ] **Step 3: Create `status-v2.ts`**

```typescript
// src/types/status-v2.ts
export interface LapwingStatus {
  state: "idle" | "thinking" | "working" | "browsing";
  current_task_id: string | null;
  current_task_request?: string | null;
  last_interaction: string | null;
  heartbeat_next?: string | null;
  active_agents: string[];
}
```

- [ ] **Step 4: Create `permissions.ts`**

```typescript
// src/types/permissions.ts

/** Backend returns users as a dict keyed by user_id */
export interface UserPermissionEntry {
  level: number;        // 0=GUEST, 1=TRUSTED, 2=OWNER, 3=ADMIN
  name: string;
  source: "env" | "override";
  note?: string;
}

/** Flattened for frontend iteration */
export interface UserPermission {
  user_id: string;
  level: number;
  name: string;
  source: "env" | "override";
  note?: string;
}

export interface PermissionsResponse {
  users: Record<string, UserPermissionEntry>;
  defaults: Record<string, string>;
  operation_auth: Record<string, string>;
  default_auth: string;
}

export interface PermissionDefaultsResponse {
  desktop_default_owner: string;
  default_auth: string;
  default_auth_level: number;
  operation_auth: Record<string, { level: number; name: string }>;
}

export const LEVEL_LABELS: Record<number, string> = {
  0: "GUEST",
  1: "TRUSTED",
  2: "OWNER",
  3: "ADMIN",
};
```

- [ ] **Step 5: Create `models.ts`**

```typescript
// src/types/models.ts

/** Backend returns slots as a dict keyed by slot name */
export interface SlotAssignment {
  provider_id: string;
  model_id: string;
}

export interface SlotDefinition {
  name: string;
  description: string;
}

export interface ModelProvider {
  id: string;
  name: string;
  base_url: string;
  api_type: string;
  api_key_preview?: string;
  models?: { id: string; name: string }[];
}

export interface ModelRoutingConfig {
  providers: ModelProvider[];
  slots: Record<string, SlotAssignment>;
  slot_definitions?: Record<string, SlotDefinition>;
}

/** Flattened slot for frontend display */
export interface SlotDisplayItem {
  slot: string;
  provider_id: string;
  model_id: string;
  description: string;
}
```

- [ ] **Step 6: Commit**

```bash
git add desktop-v2/src/types/events.ts desktop-v2/src/types/tasks-v2.ts desktop-v2/src/types/status-v2.ts desktop-v2/src/types/permissions.ts desktop-v2/src/types/models.ts
git commit -m "feat(desktop): add v2 API type definitions for Phase 7"
```

---

## Task 2: API Client — v2 REST Functions

**Files:**
- Create: `desktop-v2/src/lib/api-v2.ts`
- Modify: `desktop-v2/src/lib/api.ts` — export `getAuthHeaders`

- [ ] **Step 1: Export `getAuthHeaders` from existing `api.ts`**

In `desktop-v2/src/lib/api.ts`, change the existing `getAuthHeaders` from a private function to an export:

```typescript
// Change: function getAuthHeaders()
// To:     export function getAuthHeaders()
```

- [ ] **Step 2: Create `api-v2.ts`**

```typescript
// src/lib/api-v2.ts
import type { LapwingStatus } from "@/types/status-v2";
import type { TaskV2 } from "@/types/tasks-v2";
import type { AgentMessage } from "@/types/tasks-v2";
import type { PermissionsResponse, PermissionDefaultsResponse } from "@/types/permissions";
import type { ModelRoutingConfig } from "@/types/models";
import type { SSEEvent } from "@/types/events";
import { getApiBase, getAuthHeaders } from "./api";

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const base = getApiBase();
  const res = await fetch(`${base}${path}`, {
    credentials: "include",
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...getAuthHeaders(),
      ...init?.headers,
    },
  });
  if (!res.ok) throw new Error(`API error ${res.status}: ${res.statusText}`);
  return res.json();
}

// ── Status v2 ──
export const getStatusV2 = () =>
  fetchJson<LapwingStatus>("/api/v2/status");

// ── Tasks v2 ──
export const getTasksV2 = (status?: string, limit = 50) => {
  const params = new URLSearchParams({ limit: String(limit) });
  if (status) params.set("status", status);
  return fetchJson<{ tasks: TaskV2[]; count: number }>(`/api/v2/tasks?${params}`);
};

export const getTaskV2 = (taskId: string) =>
  fetchJson<TaskV2>(`/api/v2/tasks/${taskId}`);

/** Backend returns messages with payload dict. We transform to flat AgentMessage. */
export const getTaskMessages = async (taskId: string): Promise<{ messages: AgentMessage[] }> => {
  const raw = await fetchJson<{
    task_id: string;
    messages: { event_id: string; event_type: string; timestamp: string; actor: string; payload: Record<string, unknown> }[];
  }>(`/api/v2/tasks/${taskId}/messages`);

  return {
    messages: raw.messages.map((m) => ({
      event_id: m.event_id,
      timestamp: m.timestamp,
      actor: m.actor,
      content: (m.payload.content as string) ?? (m.payload.summary as string) ?? "",
      event_type: m.event_type,
      tool_name: m.payload.tool_name as string | undefined,
      tool_args: m.payload.tool_args as Record<string, unknown> | undefined,
    })),
  };
};

// ── Models v2 ──
export const getModelRouting = () =>
  fetchJson<ModelRoutingConfig>("/api/v2/models/routing");

export const updateModelRouting = (slots: Record<string, { provider_id: string; model_id: string }>) =>
  fetchJson<{ success: boolean }>("/api/v2/models/routing", {
    method: "PUT",
    body: JSON.stringify({ slots }),
  });

export const getAvailableModels = () =>
  fetchJson<{ slots: string[]; slot_definitions: Record<string, unknown>; providers: unknown[] }>("/api/v2/models/available");

// ── Permissions v2 ──
export const getPermissions = () =>
  fetchJson<PermissionsResponse>("/api/v2/permissions");

export const setPermission = (userId: string, level: number, name?: string, note?: string) =>
  fetchJson<{ success: boolean; user_id: string; level: number }>(`/api/v2/permissions/${encodeURIComponent(userId)}`, {
    method: "PUT",
    body: JSON.stringify({ level, name: name ?? "", note: note ?? "" }),
  });

export const deletePermission = (userId: string) =>
  fetchJson<{ success: boolean }>(`/api/v2/permissions/${encodeURIComponent(userId)}`, {
    method: "DELETE",
  });

export const getPermissionDefaults = () =>
  fetchJson<PermissionDefaultsResponse>("/api/v2/permissions/defaults");

// ── System v2 ──
export const getSystemInfo = () =>
  fetchJson<{
    uptime_seconds: number;
    cpu_percent: number;
    memory: { total: number; available: number; percent: number };
    disk: { total: number; free: number; percent: number };
    channels: Record<string, string | boolean>;
    consciousness?: { current_interval: number | null; idle_streak: number; next_tick_at: string | null };
  }>("/api/v2/system/info");

export const getSystemEvents = (params?: { event_type?: string; task_id?: string; limit?: number }) => {
  const search = new URLSearchParams();
  if (params?.event_type) search.set("event_type", params.event_type);
  if (params?.task_id) search.set("task_id", params.task_id);
  if (params?.limit) search.set("limit", String(params.limit));
  return fetchJson<{ events: SSEEvent[] }>(`/api/v2/system/events?${search}`);
};
```

- [ ] **Step 3: Commit**

```bash
git add desktop-v2/src/lib/api-v2.ts desktop-v2/src/lib/api.ts
git commit -m "feat(desktop): add v2 REST API client functions"
```

---

## Task 3: Stores — Tasks and Status Zustand Stores

**Files:**
- Create: `desktop-v2/src/stores/tasks.ts`
- Create: `desktop-v2/src/stores/status.ts`

- [ ] **Step 1: Create `stores/tasks.ts`**

```typescript
// src/stores/tasks.ts
import { create } from "zustand";
import type { TaskV2, AgentMessage } from "@/types/tasks-v2";
import { getTasksV2, getTaskMessages } from "@/lib/api-v2";

interface TasksState {
  tasks: Map<string, TaskV2>;
  agentMessages: Map<string, AgentMessage[]>;
  loading: boolean;

  upsertTask: (task: TaskV2) => void;
  removeTask: (taskId: string) => void;
  setAgentMessages: (taskId: string, messages: AgentMessage[]) => void;
  addAgentMessage: (taskId: string, msg: AgentMessage) => void;
  loadTasks: () => Promise<void>;
  loadTaskMessages: (taskId: string) => Promise<void>;
}

export const useTasksStore = create<TasksState>((set, get) => ({
  tasks: new Map(),
  agentMessages: new Map(),
  loading: false,

  upsertTask: (task) =>
    set((s) => {
      const next = new Map(s.tasks);
      next.set(task.task_id, task);
      return { tasks: next };
    }),

  removeTask: (taskId) =>
    set((s) => {
      const next = new Map(s.tasks);
      next.delete(taskId);
      return { tasks: next };
    }),

  setAgentMessages: (taskId, messages) =>
    set((s) => {
      const next = new Map(s.agentMessages);
      next.set(taskId, messages);
      return { agentMessages: next };
    }),

  addAgentMessage: (taskId, msg) =>
    set((s) => {
      const next = new Map(s.agentMessages);
      const existing = next.get(taskId) ?? [];
      next.set(taskId, [...existing, msg]);
      return { agentMessages: next };
    }),

  loadTasks: async () => {
    set({ loading: true });
    try {
      const data = await getTasksV2();
      const map = new Map<string, TaskV2>();
      for (const t of data.tasks) map.set(t.task_id, t);
      set({ tasks: map });
    } catch {
      // offline
    } finally {
      set({ loading: false });
    }
  },

  loadTaskMessages: async (taskId) => {
    try {
      const data = await getTaskMessages(taskId);
      get().setAgentMessages(taskId, data.messages);
    } catch {
      // offline
    }
  },
}));
```

- [ ] **Step 2: Create `stores/status.ts`**

```typescript
// src/stores/status.ts
import { create } from "zustand";
import type { LapwingStatus } from "@/types/status-v2";
import { getStatusV2 } from "@/lib/api-v2";

interface StatusState {
  status: LapwingStatus;
  loading: boolean;
  refresh: () => Promise<void>;
  setState: (state: LapwingStatus["state"]) => void;
  setCurrentTask: (taskId: string | null, request?: string | null) => void;
  setActiveAgents: (agents: string[]) => void;
}

const DEFAULT_STATUS: LapwingStatus = {
  state: "idle",
  current_task_id: null,
  current_task_request: null,
  last_interaction: null,
  active_agents: [],
};

export const useStatusStore = create<StatusState>((set) => ({
  status: DEFAULT_STATUS,
  loading: false,

  refresh: async () => {
    set({ loading: true });
    try {
      const status = await getStatusV2();
      set({ status });
    } catch {
      // offline — keep current state
    } finally {
      set({ loading: false });
    }
  },

  setState: (state) =>
    set((s) => ({ status: { ...s.status, state } })),

  setCurrentTask: (taskId, request) =>
    set((s) => ({
      status: { ...s.status, current_task_id: taskId, current_task_request: request ?? null },
    })),

  setActiveAgents: (agents) =>
    set((s) => ({ status: { ...s.status, active_agents: agents } })),
}));
```

- [ ] **Step 3: Commit**

```bash
git add desktop-v2/src/stores/tasks.ts desktop-v2/src/stores/status.ts
git commit -m "feat(desktop): add tasks and status Zustand stores"
```

---

## Task 4: SSE v2 Hook — Last-Event-ID + Typed Dispatch

**Files:**
- Create: `desktop-v2/src/hooks/useSSEv2.ts`

The existing `useSSE.ts` uses the old `/api/events/stream` endpoint and doesn't support Last-Event-ID or typed events. We create a new hook for the v2 endpoint that dispatches events to the tasks and status stores.

- [ ] **Step 1: Create `useSSEv2.ts`**

```typescript
// src/hooks/useSSEv2.ts
import { useEffect, useRef, useCallback, useState } from "react";
import { getApiBase } from "@/lib/api";
import { useTasksStore } from "@/stores/tasks";
import { useStatusStore } from "@/stores/status";
import type { SSEEvent } from "@/types/events";

const RECONNECT_DELAY_MS = 3000;
const MAX_EVENTS = 200;

export function useSSEv2() {
  const [connected, setConnected] = useState(false);
  const [events, setEvents] = useState<SSEEvent[]>([]);
  const esRef = useRef<EventSource | null>(null);
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const unmountedRef = useRef(false);

  const dispatch = useCallback((event: SSEEvent) => {
    // Route events to appropriate stores
    const type = event.event_type;

    if (type.startsWith("agent.")) {
      const tasksStore = useTasksStore.getState();
      if (type === "agent.task_queued" || type === "agent.task_started" ||
          type === "agent.task_done" || type === "agent.task_failed") {
        // Upsert task from event payload
        const payload = event.payload as Record<string, unknown>;
        const status = type === "agent.task_queued" ? "queued"
          : type === "agent.task_started" ? "running"
          : type === "agent.task_done" ? "done"
          : "failed";
        tasksStore.upsertTask({
          task_id: (payload.task_id as string) ?? event.task_id ?? "",
          parent_task_id: payload.parent_task_id as string | undefined,
          title: (payload.title as string) ?? (payload.request as string) ?? "",
          status,
          agent_name: payload.agent_name as string | undefined,
          created_at: event.timestamp,
          updated_at: event.timestamp,
        });
      }
      if (event.task_id && (type === "agent.message" || type === "agent.tool_called")) {
        const payload = event.payload as Record<string, unknown>;
        tasksStore.addAgentMessage(event.task_id, {
          event_id: event.event_id,
          timestamp: event.timestamp,
          actor: (payload.actor as string) ?? event.actor ?? "unknown",
          content: (payload.content as string) ?? (payload.summary as string) ?? "",
          event_type: type,
          tool_name: payload.tool_name as string | undefined,
          tool_args: payload.tool_args as Record<string, unknown> | undefined,
        });
      }
    }

    if (type === "status.changed") {
      const statusStore = useStatusStore.getState();
      const payload = event.payload as Record<string, unknown>;
      if (payload.state) {
        statusStore.setState(payload.state as "idle" | "thinking" | "working" | "browsing");
      }
      if (payload.current_task_id !== undefined) {
        statusStore.setCurrentTask(
          payload.current_task_id as string | null,
          payload.current_task_request as string | null,
        );
      }
      if (payload.active_agents) {
        statusStore.setActiveAgents(payload.active_agents as string[]);
      }
    }
  }, []);

  const connect = useCallback(() => {
    if (unmountedRef.current) return;
    const token = localStorage.getItem("lapwing_desktop_token") ?? "";
    const base = getApiBase();
    // Token in query param (EventSource can't set custom headers).
    // Last-Event-ID is automatically sent by the browser on reconnect.
    const url = `${base}/api/v2/events?token=${encodeURIComponent(token)}`;

    const es = new EventSource(url);
    esRef.current = es;

    es.onopen = () => setConnected(true);

    es.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data as string) as SSEEvent;
        setEvents((prev) => {
          const next = [...prev, event];
          return next.length > MAX_EVENTS ? next.slice(-MAX_EVENTS) : next;
        });
        dispatch(event);
      } catch {
        // ignore parse errors
      }
    };

    es.onerror = () => {
      setConnected(false);
      es.close();
      esRef.current = null;
      if (!unmountedRef.current) {
        retryRef.current = setTimeout(connect, RECONNECT_DELAY_MS);
      }
    };
  }, [dispatch]);

  useEffect(() => {
    unmountedRef.current = false;
    connect();
    return () => {
      unmountedRef.current = true;
      if (retryRef.current) clearTimeout(retryRef.current);
      esRef.current?.close();
    };
  }, [connect]);

  return { events, connected };
}
```

- [ ] **Step 2: Commit**

```bash
git add desktop-v2/src/hooks/useSSEv2.ts
git commit -m "feat(desktop): add SSE v2 hook with Last-Event-ID and store dispatch"
```

---

## Task 5: Status Indicator Component

**Files:**
- Create: `desktop-v2/src/components/status/StatusIndicator.tsx`
- Create: `desktop-v2/src/hooks/useStatus.ts`

- [ ] **Step 1: Create `useStatus.ts` hook**

```typescript
// src/hooks/useStatus.ts
import { useEffect } from "react";
import { useStatusStore } from "@/stores/status";

const POLL_INTERVAL_MS = 30_000;

/** Polls /api/v2/status on mount + interval. SSE events update in real-time via useSSEv2 dispatch. */
export function useStatus() {
  const refresh = useStatusStore((s) => s.refresh);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  return useStatusStore((s) => s.status);
}
```

- [ ] **Step 2: Create `StatusIndicator.tsx`**

```typescript
// src/components/status/StatusIndicator.tsx
import { useStatusStore } from "@/stores/status";

const STATUS_CONFIG = {
  idle: { color: "bg-green-400", label: "idle" },
  thinking: { color: "bg-yellow-400 animate-pulse", label: "thinking" },
  working: { color: "bg-blue-400 animate-pulse", label: "working" },
  browsing: { color: "bg-purple-400 animate-pulse", label: "browsing" },
} as const;

export function StatusIndicator() {
  const state = useStatusStore((s) => s.status.state);
  const config = STATUS_CONFIG[state] ?? STATUS_CONFIG.idle;

  return (
    <div className="flex items-center gap-2 px-3 py-2 text-xs text-text-secondary">
      <span className={`w-2 h-2 rounded-full ${config.color}`} />
      <span>{config.label}</span>
    </div>
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add desktop-v2/src/hooks/useStatus.ts desktop-v2/src/components/status/StatusIndicator.tsx
git commit -m "feat(desktop): add StatusIndicator component and useStatus hook"
```

---

## Task 6: Task Sidebar Components

**Files:**
- Create: `desktop-v2/src/components/tasks/TaskSidebar.tsx`
- Create: `desktop-v2/src/components/tasks/TaskCard.tsx`
- Create: `desktop-v2/src/components/tasks/AgentMessageList.tsx`
- Create: `desktop-v2/src/hooks/useTasks.ts`

- [ ] **Step 1: Create `useTasks.ts` hook**

```typescript
// src/hooks/useTasks.ts
import { useEffect } from "react";
import { useTasksStore } from "@/stores/tasks";

/** Loads initial tasks on mount. SSE events update in real-time via useSSEv2 dispatch. */
export function useTasks() {
  const loadTasks = useTasksStore((s) => s.loadTasks);

  useEffect(() => {
    loadTasks();
  }, [loadTasks]);

  return useTasksStore();
}
```

- [ ] **Step 2: Create `AgentMessageList.tsx`**

```typescript
// src/components/tasks/AgentMessageList.tsx
import { useEffect } from "react";
import { useTasksStore } from "@/stores/tasks";
import type { AgentMessage } from "@/types/tasks-v2";
import { Wrench } from "lucide-react";

function MessageRow({ msg }: { msg: AgentMessage }) {
  const isToolCall = msg.event_type === "agent.tool_called";

  return (
    <div className="flex gap-2 py-1.5">
      <div className="w-px bg-surface-border shrink-0 ml-2" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          {isToolCall && <Wrench size={10} className="text-yellow-400 shrink-0" />}
          <span className="text-[11px] font-medium text-text-accent truncate">
            {msg.actor}
            {isToolCall && msg.tool_name ? ` calls ${msg.tool_name}` : ""}
          </span>
          <span className="text-[10px] text-text-muted ml-auto shrink-0">
            {formatTime(msg.timestamp)}
          </span>
        </div>
        {msg.content && (
          <p className="text-xs text-text-secondary mt-0.5 line-clamp-3 break-words">
            {msg.content}
          </p>
        )}
      </div>
    </div>
  );
}

function formatTime(ts: string): string {
  try {
    return new Date(ts).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

export function AgentMessageList({ taskId }: { taskId: string }) {
  const messages = useTasksStore((s) => s.agentMessages.get(taskId));
  const loadTaskMessages = useTasksStore((s) => s.loadTaskMessages);

  useEffect(() => {
    if (!messages) loadTaskMessages(taskId);
  }, [taskId, messages, loadTaskMessages]);

  if (!messages || messages.length === 0) {
    return (
      <div className="text-[11px] text-text-muted py-2 pl-4">
        loading...
      </div>
    );
  }

  return (
    <div className="pl-1">
      {messages.map((msg) => (
        <MessageRow key={msg.event_id} msg={msg} />
      ))}
    </div>
  );
}
```

- [ ] **Step 3: Create `TaskCard.tsx`**

```typescript
// src/components/tasks/TaskCard.tsx
import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { TaskV2 } from "@/types/tasks-v2";
import { AgentMessageList } from "./AgentMessageList";

const STATUS_STYLE: Record<string, { dot: string; text: string }> = {
  queued: { dot: "bg-gray-400", text: "text-text-muted" },
  running: { dot: "bg-blue-400 animate-pulse", text: "text-blue-400" },
  done: { dot: "bg-green-400", text: "text-green-400" },
  failed: { dot: "bg-red-400", text: "text-red-400" },
  cancelled: { dot: "bg-gray-500", text: "text-gray-500" },
};

function timeAgo(ts: string): string {
  try {
    const diff = Date.now() - new Date(ts).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    return `${Math.floor(hours / 24)}d ago`;
  } catch {
    return "";
  }
}

export function TaskCard({ task }: { task: TaskV2 }) {
  const [expanded, setExpanded] = useState(false);
  const style = STATUS_STYLE[task.status] ?? STATUS_STYLE.queued;

  return (
    <div className="bg-surface border border-surface-border rounded-md">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 w-full text-left px-2.5 py-2 hover:bg-surface-hover rounded-md transition-colors"
      >
        {expanded ? (
          <ChevronDown size={12} className="text-text-muted shrink-0" />
        ) : (
          <ChevronRight size={12} className="text-text-muted shrink-0" />
        )}
        <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${style.dot}`} />
        <span className="text-xs text-text-primary truncate flex-1">{task.title}</span>
        <span className="text-[10px] text-text-muted shrink-0">
          {timeAgo(task.updated_at ?? task.created_at)}
        </span>
      </button>

      {expanded && (
        <div className="px-2.5 pb-2 border-t border-surface-border">
          <div className="flex items-center gap-2 py-1.5 text-[11px]">
            {task.agent_name && (
              <span className="text-text-secondary">{task.agent_name}</span>
            )}
            <span className={style.text}>{task.status}</span>
          </div>
          <AgentMessageList taskId={task.task_id} />
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Create `TaskSidebar.tsx`**

```typescript
// src/components/tasks/TaskSidebar.tsx
import { useState } from "react";
import { ChevronLeft, ChevronRight, ListTodo } from "lucide-react";
import { useTasksStore } from "@/stores/tasks";
import { TaskCard } from "./TaskCard";
import type { TaskV2 } from "@/types/tasks-v2";

export function TaskSidebar() {
  const [collapsed, setCollapsed] = useState(false);
  const tasks = useTasksStore((s) => s.tasks);

  if (collapsed) {
    return (
      <button
        onClick={() => setCollapsed(false)}
        className="flex items-center justify-center w-8 h-full border-l border-surface-border bg-void-100 hover:bg-surface-hover"
        title="Show tasks"
      >
        <ChevronLeft size={14} className="text-text-muted" />
      </button>
    );
  }

  const allTasks = Array.from(tasks.values());
  const activeTasks = allTasks.filter(
    (t) => t.status === "queued" || t.status === "running"
  );
  const recentDone = allTasks
    .filter((t) => t.status === "done" || t.status === "failed")
    .sort((a, b) => (b.updated_at ?? b.created_at).localeCompare(a.updated_at ?? a.created_at))
    .slice(0, 5);

  return (
    <div className="w-[260px] shrink-0 border-l border-surface-border bg-void-100 flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-surface-border">
        <div className="flex items-center gap-1.5 text-sm font-medium text-text-accent">
          <ListTodo size={14} />
          <span>Tasks</span>
          {activeTasks.length > 0 && (
            <span className="text-xs text-blue-400">({activeTasks.length})</span>
          )}
        </div>
        <button
          onClick={() => setCollapsed(true)}
          className="p-1 hover:bg-surface-hover rounded"
          title="Collapse"
        >
          <ChevronRight size={12} className="text-text-muted" />
        </button>
      </div>

      {/* Task list */}
      <div className="flex-1 overflow-y-auto p-2 space-y-1.5">
        {activeTasks.length === 0 && recentDone.length === 0 && (
          <div className="text-xs text-text-muted text-center py-8">
            No active tasks
          </div>
        )}

        {activeTasks.length > 0 && (
          <>
            <div className="text-[11px] text-text-muted uppercase tracking-wider px-1 pb-1">
              Active
            </div>
            {activeTasks.map((t) => (
              <TaskCard key={t.task_id} task={t} />
            ))}
          </>
        )}

        {recentDone.length > 0 && (
          <>
            <div className="text-[11px] text-text-muted uppercase tracking-wider px-1 pt-2 pb-1">
              Recent
            </div>
            {recentDone.map((t) => (
              <TaskCard key={t.task_id} task={t} />
            ))}
          </>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Commit**

```bash
git add desktop-v2/src/hooks/useTasks.ts desktop-v2/src/components/tasks/AgentMessageList.tsx desktop-v2/src/components/tasks/TaskCard.tsx desktop-v2/src/components/tasks/TaskSidebar.tsx
git commit -m "feat(desktop): add TaskSidebar with TaskCard and AgentMessageList"
```

---

## Task 7: Restructure Navigation — Router + Sidebar

**Files:**
- Modify: `desktop-v2/src/router.tsx`
- Modify: `desktop-v2/src/components/layout/Sidebar.tsx`
- Modify: `desktop-v2/src/components/layout/AppShell.tsx`

The blueprint simplifies navigation to 5 items: Chat, Notes, Identity, System, Settings.
For now, Notes/Identity/System routes point at placeholder pages (existing pages will be refactored in P3-P6). The key change is removing TaskCenter, Dashboard, Sensing, Memory, Persona, ModelRouting from the sidebar and adding the StatusIndicator.

- [ ] **Step 1: Update `router.tsx`**

Replace entire content of `desktop-v2/src/router.tsx`:

```typescript
import { createBrowserRouter, Navigate } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import ChatPage from "@/pages/ChatPage";
import SettingsPage from "@/pages/SettingsPage";

// P3-P6 pages — use existing pages as placeholders for now
import MemoryPage from "@/pages/MemoryPage";
import PersonaPage from "@/pages/PersonaPage";
import DashboardPage from "@/pages/DashboardPage";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <Navigate to="/chat" replace /> },
      { path: "chat", element: <ChatPage /> },
      { path: "notes", element: <MemoryPage /> },
      { path: "identity", element: <PersonaPage /> },
      { path: "system", element: <DashboardPage /> },
      { path: "settings", element: <SettingsPage /> },
    ],
  },
]);
```

- [ ] **Step 2: Update `Sidebar.tsx`**

Replace entire content of `desktop-v2/src/components/layout/Sidebar.tsx`:

```typescript
import { NavLink } from "react-router-dom";
import {
  MessageSquare, BookOpen, Fingerprint,
  Monitor, Settings,
} from "lucide-react";
import { useServerStore } from "@/stores/server";
import { StatusIndicator } from "@/components/status/StatusIndicator";

const NAV_ITEMS = [
  { to: "/chat", icon: MessageSquare, label: "Chat" },
  { to: "/notes", icon: BookOpen, label: "Notes" },
  { to: "/identity", icon: Fingerprint, label: "Identity" },
  { to: "/system", icon: Monitor, label: "System" },
  { to: "/settings", icon: Settings, label: "Settings" },
] as const;

export function Sidebar() {
  const connected = useServerStore((s) => s.connected);

  return (
    <aside className="w-[200px] h-full flex flex-col bg-void-100 border-r border-surface-border shrink-0">
      {/* Header */}
      <div className="px-4 pt-5 pb-3 flex items-center gap-3">
        <div className="w-9 h-9 rounded-full bg-gradient-to-br from-lapwing-light to-lapwing-dark flex items-center justify-center text-void font-bold text-sm">
          L
        </div>
        <div>
          <div className="text-text-accent font-medium text-sm">Lapwing</div>
          <div className="flex items-center gap-1.5 text-[11px] text-text-secondary">
            <span className={`w-1.5 h-1.5 rounded-full ${connected ? "bg-green-400" : "bg-gray-500"}`} />
            {connected ? "online" : "offline"}
          </div>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-2 py-2 space-y-0.5 overflow-y-auto">
        {NAV_ITEMS.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors ${
                isActive
                  ? "bg-surface-active text-text-accent"
                  : "text-text-secondary hover:bg-surface-hover hover:text-text-primary"
              }`
            }
          >
            <Icon size={18} />
            <span>{label}</span>
          </NavLink>
        ))}
      </nav>

      {/* Status indicator */}
      <div className="border-t border-surface-border">
        <StatusIndicator />
      </div>
    </aside>
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add desktop-v2/src/router.tsx desktop-v2/src/components/layout/Sidebar.tsx
git commit -m "refactor(desktop): simplify navigation to 5 routes with StatusIndicator"
```

---

## Task 8: ChatPage — Replace AgentPanel with TaskSidebar + Wire SSE

**Files:**
- Modify: `desktop-v2/src/pages/ChatPage.tsx`
- Modify: `desktop-v2/src/components/layout/AppShell.tsx`

The AppShell needs to initialize SSEv2 and useStatus at app level so they're always connected.

- [ ] **Step 1: Update `AppShell.tsx` to initialize global connections**

Replace entire content of `desktop-v2/src/components/layout/AppShell.tsx`:

```typescript
import { Outlet } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { useSSEv2 } from "@/hooks/useSSEv2";
import { useStatus } from "@/hooks/useStatus";

export function AppShell() {
  // Initialize global SSE connection and status polling
  useSSEv2();
  useStatus();

  return (
    <div className="flex h-screen w-screen bg-void">
      <Sidebar />
      <main className="flex-1 overflow-hidden">
        <Outlet />
      </main>
    </div>
  );
}
```

- [ ] **Step 2: Update `ChatPage.tsx` to use TaskSidebar**

Replace entire content of `desktop-v2/src/pages/ChatPage.tsx`:

```typescript
import { useEffect, useCallback } from "react";
import { useChatStore } from "@/stores/chat";
import { useWebSocket } from "@/hooks/useWebSocket";
import { useTasks } from "@/hooks/useTasks";
import { getChatHistory } from "@/lib/api";
import { MessageList } from "@/components/chat/MessageList";
import { MessageInput } from "@/components/chat/MessageInput";
import { ChatHeader } from "@/components/chat/ChatHeader";
import { TaskSidebar } from "@/components/tasks/TaskSidebar";

export default function ChatPage() {
  const wsStatus = useChatStore((s) => s.wsStatus);
  const chatId = useChatStore((s) => s.chatId);
  const messages = useChatStore((s) => s.messages);
  const { send } = useWebSocket();

  // Initialize task loading (SSE updates handled globally in AppShell)
  useTasks();

  const loadHistory = useCallback(async () => {
    if (!chatId || messages.length > 0) return;
    try {
      const data = await getChatHistory(chatId);
      if (data.messages.length > 0) {
        useChatStore.getState().setMessages(data.messages);
      }
    } catch {
      // offline
    }
  }, [chatId, messages.length]);

  useEffect(() => {
    loadHistory();
  }, [loadHistory]);

  return (
    <div className="h-full flex">
      {/* Main chat area */}
      <div className="flex-1 flex flex-col min-w-0">
        <ChatHeader />
        <MessageList />
        <MessageInput onSend={send} disabled={wsStatus !== "connected"} />
      </div>

      {/* Task sidebar (right) */}
      <TaskSidebar />
    </div>
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add desktop-v2/src/pages/ChatPage.tsx desktop-v2/src/components/layout/AppShell.tsx
git commit -m "feat(desktop): wire TaskSidebar into ChatPage, init SSE/status in AppShell"
```

---

## Task 9: Settings Page — Connection + Models + Permissions Tabs

**Files:**
- Create: `desktop-v2/src/components/settings/ConnectionTab.tsx`
- Create: `desktop-v2/src/components/settings/ModelsTab.tsx`
- Create: `desktop-v2/src/components/settings/PermissionsTab.tsx`
- Modify: `desktop-v2/src/pages/SettingsPage.tsx`

- [ ] **Step 1: Create `ConnectionTab.tsx`**

```typescript
// src/components/settings/ConnectionTab.tsx
import { useState } from "react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { useServerStore } from "@/stores/server";
import { useChatStore } from "@/stores/chat";
import { Wifi, WifiOff } from "lucide-react";

export function ConnectionTab() {
  const { serverUrl, token, setServerUrl, setToken } = useServerStore();
  const wsStatus = useChatStore((s) => s.wsStatus);
  const [url, setUrl] = useState(serverUrl);
  const [tok, setTok] = useState(token);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<string | null>(null);

  const handleTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const headers: HeadersInit = { "Content-Type": "application/json" };
      if (tok) headers["Authorization"] = `Bearer ${tok}`;
      const res = await fetch(`${url}/api/status`, { headers });
      if (res.ok) {
        setTestResult("Connection successful");
        setServerUrl(url);
        setToken(tok);
      } else {
        setTestResult(`Failed: HTTP ${res.status}`);
      }
    } catch (e) {
      setTestResult(`Failed: ${e}`);
    }
    setTesting(false);
  };

  const handleSave = () => {
    setServerUrl(url);
    setToken(tok);
  };

  return (
    <div className="space-y-6 max-w-lg">
      <div>
        <label className="text-sm text-text-accent block mb-2">Server URL</label>
        <Input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="http://127.0.0.1:8765"
          className="bg-surface border-surface-border text-text-primary"
        />
      </div>

      <div>
        <label className="text-sm text-text-accent block mb-2">Auth Token</label>
        <Input
          type="password"
          value={tok}
          onChange={(e) => setTok(e.target.value)}
          placeholder="Desktop token"
          className="bg-surface border-surface-border text-text-primary"
        />
      </div>

      <div className="flex items-center gap-3">
        <Button onClick={handleSave} variant="outline">
          Save
        </Button>
        <Button
          onClick={handleTest}
          disabled={testing}
          className="bg-lapwing text-void hover:bg-lapwing-dark"
        >
          {testing ? "Testing..." : "Test Connection"}
        </Button>
      </div>

      {testResult && (
        <p className={`text-xs ${testResult.includes("successful") ? "text-green-400" : "text-red-400"}`}>
          {testResult}
        </p>
      )}

      <div className="border-t border-surface-border pt-4">
        <div className="text-sm text-text-accent mb-2">Connection Status</div>
        <div className="space-y-2 text-sm">
          <div className="flex items-center gap-2 text-text-secondary">
            {wsStatus === "connected" ? (
              <Wifi size={14} className="text-green-400" />
            ) : (
              <WifiOff size={14} className="text-red-400" />
            )}
            <span>WebSocket: {wsStatus}</span>
          </div>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Create `ModelsTab.tsx`**

```typescript
// src/components/settings/ModelsTab.tsx
import { useEffect, useState, useCallback } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { getModelRouting, updateModelRouting } from "@/lib/api-v2";
import type { ModelRoutingConfig, SlotDisplayItem } from "@/types/models";

/** Flatten dict-based slots into an array for display */
function flattenSlots(config: ModelRoutingConfig): SlotDisplayItem[] {
  return Object.entries(config.slots).map(([slot, assignment]) => ({
    slot,
    provider_id: assignment.provider_id,
    model_id: assignment.model_id,
    description: config.slot_definitions?.[slot]?.description ?? "",
  }));
}

export function ModelsTab() {
  const [config, setConfig] = useState<ModelRoutingConfig | null>(null);
  const [edits, setEdits] = useState<Map<string, { provider_id: string; model_id: string }>>(new Map());
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchConfig = useCallback(async () => {
    try {
      const data = await getModelRouting();
      setConfig(data);
    } catch {
      setError("Failed to load model config");
    }
  }, []);

  useEffect(() => {
    fetchConfig();
  }, [fetchConfig]);

  const handleSlotEdit = (slot: string, field: "provider_id" | "model_id", value: string) => {
    const assignment = config?.slots[slot];
    const current = edits.get(slot) ?? {
      provider_id: assignment?.provider_id ?? "",
      model_id: assignment?.model_id ?? "",
    };
    setEdits(new Map(edits).set(slot, { ...current, [field]: value }));
  };

  const handleSave = async () => {
    if (edits.size === 0) return;
    setSaving(true);
    setError(null);
    try {
      const payload: Record<string, { provider_id: string; model_id: string }> = {};
      edits.forEach((v, k) => { payload[k] = v; });
      await updateModelRouting(payload);
      setEdits(new Map());
      await fetchConfig();
    } catch (e) {
      setError(`Save failed: ${e}`);
    }
    setSaving(false);
  };

  if (!config) {
    return <div className="text-sm text-text-muted py-4">Loading...</div>;
  }

  const slots = flattenSlots(config);

  return (
    <div className="space-y-4 max-w-2xl">
      {error && <p className="text-xs text-red-400">{error}</p>}

      {slots.map((slot) => {
        const edit = edits.get(slot.slot);
        return (
          <div key={slot.slot} className="bg-surface border border-surface-border rounded-lg p-4">
            <div className="flex items-center gap-2 mb-3">
              <span className="text-sm font-medium text-text-accent">{slot.slot}</span>
              {slot.description && (
                <span className="text-xs text-text-muted">({slot.description})</span>
              )}
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-text-muted block mb-1">Provider</label>
                <Input
                  value={edit?.provider_id ?? slot.provider_id}
                  onChange={(e) => handleSlotEdit(slot.slot, "provider_id", e.target.value)}
                  className="bg-void-50 border-surface-border text-text-primary text-sm h-8"
                />
              </div>
              <div>
                <label className="text-xs text-text-muted block mb-1">Model</label>
                <Input
                  value={edit?.model_id ?? slot.model_id}
                  onChange={(e) => handleSlotEdit(slot.slot, "model_id", e.target.value)}
                  className="bg-void-50 border-surface-border text-text-primary text-sm h-8"
                />
              </div>
            </div>
          </div>
        );
      })}

      {config.providers.length > 0 && (
        <div className="border-t border-surface-border pt-4">
          <div className="text-sm text-text-accent mb-2">Providers</div>
          <div className="flex flex-wrap gap-2">
            {config.providers.map((p) => (
              <Badge key={p.id} variant="outline" className="text-xs">
                {p.name} ({p.api_type})
              </Badge>
            ))}
          </div>
        </div>
      )}

      <div className="flex gap-2 pt-2">
        <Button
          onClick={handleSave}
          disabled={saving || edits.size === 0}
          className="bg-lapwing text-void hover:bg-lapwing-dark"
        >
          {saving ? "Saving..." : "Save"}
        </Button>
        {edits.size > 0 && (
          <Button variant="outline" onClick={() => setEdits(new Map())}>
            Reset
          </Button>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Create `PermissionsTab.tsx`**

```typescript
// src/components/settings/PermissionsTab.tsx
import { useEffect, useState, useCallback } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Trash2, Plus } from "lucide-react";
import { getPermissions, setPermission, deletePermission } from "@/lib/api-v2";
import type { UserPermission } from "@/types/permissions";

/** Flatten dict-based users into array for display */
function flattenUsers(usersDict: Record<string, { level: number; name: string; source: string; note?: string }>): UserPermission[] {
  return Object.entries(usersDict).map(([user_id, entry]) => ({
    user_id,
    level: entry.level,
    name: entry.name,
    source: entry.source as "env" | "override",
    note: entry.note,
  }));
}

export function PermissionsTab() {
  const [users, setUsers] = useState<UserPermission[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [newUserId, setNewUserId] = useState("");
  const [newLevel, setNewLevel] = useState(1);
  const [newName, setNewName] = useState("");
  const [newNote, setNewNote] = useState("");

  const fetchPermissions = useCallback(async () => {
    try {
      const data = await getPermissions();
      setUsers(flattenUsers(data.users));
    } catch {
      setError("Failed to load permissions");
    }
  }, []);

  useEffect(() => {
    fetchPermissions();
  }, [fetchPermissions]);

  const handleDelete = async (userId: string) => {
    try {
      await deletePermission(userId);
      fetchPermissions();
    } catch (e) {
      setError(`Delete failed: ${e}`);
    }
  };

  const handleAdd = async () => {
    if (!newUserId.trim()) return;
    try {
      await setPermission(newUserId.trim(), newLevel, newName || undefined, newNote || undefined);
      setNewUserId("");
      setNewName("");
      setNewNote("");
      setShowAdd(false);
      fetchPermissions();
    } catch (e) {
      setError(`Add failed: ${e}`);
    }
  };

  const handleLevelChange = async (userId: string, level: number, name?: string) => {
    try {
      await setPermission(userId, level, name);
      fetchPermissions();
    } catch (e) {
      setError(`Update failed: ${e}`);
    }
  };

  return (
    <div className="space-y-4 max-w-lg">
      {error && <p className="text-xs text-red-400 mb-2">{error}</p>}

      <div className="text-sm text-text-accent">User Permissions</div>

      <div className="space-y-2">
        {users.map((u) => (
          <div key={u.user_id} className="bg-surface border border-surface-border rounded-lg p-3 flex items-center gap-3">
            <div className="flex-1 min-w-0">
              <div className="text-sm text-text-primary truncate">{u.name || u.user_id}</div>
              {u.name && <div className="text-xs text-text-muted truncate">{u.user_id}</div>}
              <div className="flex items-center gap-2 mt-0.5">
                {u.note && <span className="text-xs text-text-secondary">{u.note}</span>}
                <span className="text-[10px] text-text-muted">{u.source}</span>
              </div>
            </div>
            <select
              value={u.level}
              onChange={(e) => handleLevelChange(u.user_id, Number(e.target.value), u.name)}
              className="bg-void-50 border border-surface-border rounded px-2 py-1 text-xs text-text-primary"
            >
              <option value={0}>GUEST</option>
              <option value={1}>TRUSTED</option>
              <option value={2}>OWNER</option>
            </select>
            {u.source === "override" && (
              <button
                onClick={() => handleDelete(u.user_id)}
                className="p-1 hover:bg-surface-hover rounded text-text-muted hover:text-red-400"
                title="Remove override"
              >
                <Trash2 size={14} />
              </button>
            )}
          </div>
        ))}
      </div>

      {showAdd ? (
        <div className="bg-surface border border-surface-border rounded-lg p-3 space-y-2">
          <Input
            value={newUserId}
            onChange={(e) => setNewUserId(e.target.value)}
            placeholder="User ID (e.g. qq:12345)"
            className="bg-void-50 border-surface-border text-text-primary text-sm h-8"
          />
          <div className="flex gap-2">
            <select
              value={newLevel}
              onChange={(e) => setNewLevel(Number(e.target.value))}
              className="bg-void-50 border border-surface-border rounded px-2 py-1 text-xs text-text-primary"
            >
              <option value={0}>GUEST</option>
              <option value={1}>TRUSTED</option>
              <option value={2}>OWNER</option>
            </select>
            <Input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="Display name"
              className="bg-void-50 border-surface-border text-text-primary text-sm h-8 flex-1"
            />
          </div>
          <Input
            value={newNote}
            onChange={(e) => setNewNote(e.target.value)}
            placeholder="Note (optional)"
            className="bg-void-50 border-surface-border text-text-primary text-sm h-8"
          />
          <div className="flex gap-2">
            <Button onClick={handleAdd} size="sm" className="bg-lapwing text-void hover:bg-lapwing-dark">
              Add
            </Button>
            <Button onClick={() => setShowAdd(false)} size="sm" variant="outline">
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <Button
          onClick={() => setShowAdd(true)}
          variant="outline"
          size="sm"
          className="gap-1"
        >
          <Plus size={14} /> Add User
        </Button>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Rewrite `SettingsPage.tsx`**

Replace entire content of `desktop-v2/src/pages/SettingsPage.tsx`:

```typescript
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ConnectionTab } from "@/components/settings/ConnectionTab";
import { ModelsTab } from "@/components/settings/ModelsTab";
import { PermissionsTab } from "@/components/settings/PermissionsTab";

export default function SettingsPage() {
  return (
    <div className="h-full flex flex-col">
      <div className="p-4 border-b border-surface-border">
        <h1 className="text-lg font-medium text-text-accent">Settings</h1>
      </div>

      <Tabs defaultValue="connection" className="flex-1 flex flex-col">
        <TabsList className="mx-4 mt-2 bg-void-50">
          <TabsTrigger value="connection">Connection</TabsTrigger>
          <TabsTrigger value="models">Models</TabsTrigger>
          <TabsTrigger value="permissions">Permissions</TabsTrigger>
          <TabsTrigger value="about">About</TabsTrigger>
        </TabsList>

        <TabsContent value="connection" className="flex-1 m-0">
          <ScrollArea className="h-full px-4 py-4">
            <ConnectionTab />
          </ScrollArea>
        </TabsContent>

        <TabsContent value="models" className="flex-1 m-0">
          <ScrollArea className="h-full px-4 py-4">
            <ModelsTab />
          </ScrollArea>
        </TabsContent>

        <TabsContent value="permissions" className="flex-1 m-0">
          <ScrollArea className="h-full px-4 py-4">
            <PermissionsTab />
          </ScrollArea>
        </TabsContent>

        <TabsContent value="about" className="flex-1 m-0">
          <ScrollArea className="h-full px-4 py-4">
            <div className="space-y-3 text-sm">
              <div className="text-text-accent">Lapwing Desktop v0.2.0</div>
              <div className="text-text-secondary">
                Built with Tauri v2 + React 19 + TypeScript
              </div>
              <div className="text-text-muted text-xs">
                Phase 7 — "Her Room"
              </div>
            </div>
          </ScrollArea>
        </TabsContent>
      </Tabs>
    </div>
  );
}
```

- [ ] **Step 5: Commit**

```bash
git add desktop-v2/src/components/settings/ConnectionTab.tsx desktop-v2/src/components/settings/ModelsTab.tsx desktop-v2/src/components/settings/PermissionsTab.tsx desktop-v2/src/pages/SettingsPage.tsx
git commit -m "feat(desktop): rebuild SettingsPage with connection, models, permissions tabs"
```

---

## Task 10: Verify — TypeScript Build + Visual Check

**Files:** None (verification only)

- [ ] **Step 1: Run TypeScript build to check for errors**

```bash
cd desktop-v2 && npx tsc --noEmit
```

Fix any type errors that arise.

- [ ] **Step 2: Start dev server and visually verify**

```bash
cd desktop-v2 && npm run dev
```

Open `http://localhost:1420` in a browser. Check:
- Sidebar shows 5 items (Chat, Notes, Identity, System, Settings)
- StatusIndicator shows at sidebar bottom
- ChatPage loads with TaskSidebar on right
- TaskSidebar is collapsible
- Settings page has 4 tabs (Connection, Models, Permissions, About)
- No console errors

- [ ] **Step 3: Fix any issues found**

Address TypeScript errors, missing imports, or visual bugs.

- [ ] **Step 4: Commit fixes**

```bash
git add -u desktop-v2/
git commit -m "fix(desktop): resolve build errors from Phase 7 P0-P2 integration"
```

---

## Summary

| Task | Description | Priority |
|------|-------------|----------|
| 1 | Type definitions for v2 APIs | Foundation |
| 2 | v2 REST API client functions | Foundation |
| 3 | Tasks + Status Zustand stores | Foundation |
| 4 | SSE v2 hook with event dispatch | Foundation |
| 5 | StatusIndicator component + hook | P1 |
| 6 | TaskSidebar + TaskCard + AgentMessageList | P0 |
| 7 | Router + Sidebar restructure | P0 |
| 8 | ChatPage + AppShell wiring | P0 |
| 9 | SettingsPage (connection + models + permissions) | P2 |
| 10 | TypeScript build + visual verification | Verification |

**Post-MVP follow-up (P3-P6):** NotesPage, IdentityPage (soul history/diff/rollback), SystemPage, StatusDetailPage — these can be planned separately once P0-P2 is verified working.
