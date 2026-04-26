# ChatPage + Agent Visualization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete ChatPage with Markdown rendering, streaming, tool call indicators, and Agent activity visualization — making desktop the primary chat interface.

**Architecture:** Extend existing WebSocket protocol with new message types (`tool_call`, `tool_result`, `agent_emit`, `agent_notify`). Frontend adds Markdown rendering via `react-markdown`, collapsible tool call panels, inline Agent activity cards, and a collapsible right-side Agent panel. Backend adds `/api/agents` REST routes and wires Agent progress/result callbacks through the WebSocket.

**Tech Stack:** React 19, Zustand 5, Tailwind CSS 4, react-markdown + remark-gfm (new deps), FastAPI, existing AgentDispatcher/AgentRegistry/DesktopEventBus.

---

## File Structure

### Frontend (desktop-v2/)

| Action | File | Responsibility |
|--------|------|---------------|
| Modify | `src/types/chat.ts` | Add `AgentActivity`, `ToolCallEvent` types; extend `ChatMessage` |
| Modify | `src/stores/chat.ts` | Add streaming/agent/tool state and actions |
| Modify | `src/lib/api.ts` | Add agent API wrappers (getAgents, getActiveTasks, cancelAgent) |
| Modify | `src/hooks/useWebSocket.ts` | Handle new WS message types |
| Rewrite | `src/pages/ChatPage.tsx` | Full layout with header, messages, input, agent panel |
| Create | `src/components/chat/ChatHeader.tsx` | Status bar showing Lapwing state |
| Modify | `src/components/chat/MessageBubble.tsx` | Markdown rendering, tool call display |
| Create | `src/components/chat/AgentActivityCard.tsx` | Inline agent activity card in message flow |
| Create | `src/components/chat/AgentPanel.tsx` | Right sidebar agent status panel |
| Modify | `src/components/chat/MessageList.tsx` | Smart scroll, agent cards in flow |
| New dep | `package.json` | `react-markdown`, `remark-gfm`, `rehype-raw` |

### Backend (src/)

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `src/api/routes/agents.py` | REST endpoints: list agents, active tasks, cancel |
| Modify | `src/api/server.py` | Mount agents router |
| Modify | `src/api/routes/chat_ws.py` | Forward agent events through WebSocket |
| Modify | `src/app/container.py` | Wire agent dispatcher callbacks |

---

## Task 1: Install frontend dependencies

**Files:**
- Modify: `desktop-v2/package.json`

- [ ] **Step 1: Install Markdown rendering packages**

```bash
cd desktop-v2 && npm install react-markdown remark-gfm rehype-raw
```

- [ ] **Step 2: Verify installation**

```bash
cd desktop-v2 && npm ls react-markdown
```

Expected: `react-markdown@X.X.X`

- [ ] **Step 3: Commit**

```bash
git add desktop-v2/package.json desktop-v2/package-lock.json
git commit -m "feat(desktop): add react-markdown + remark-gfm for chat rendering"
```

---

## Task 2: Extend types and store

**Files:**
- Modify: `desktop-v2/src/types/chat.ts`
- Modify: `desktop-v2/src/stores/chat.ts`

- [ ] **Step 1: Extend types in `src/types/chat.ts`**

Add these types to the existing file:

```typescript
export interface ToolCallEvent {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
  result?: string;
  success?: boolean;
  startedAt: number;
  completedAt?: number;
}

export interface AgentActivity {
  commandId: string;
  agentName: string;
  state: "queued" | "working" | "done" | "failed" | "blocked" | "cancelled";
  progress: number | null;
  note: string | null;
  headline: string | null;
  startedAt: number;
  completedAt?: number;
}
```

- [ ] **Step 2: Extend store in `src/stores/chat.ts`**

Add new state fields and actions to `ChatState`:

```typescript
// New state fields:
isStreaming: boolean;
activeToolCalls: ToolCallEvent[];
agentActivities: AgentActivity[];
lapwingStatus: "idle" | "thinking" | "using_tool" | "delegating";

// New actions:
setIsStreaming: (v: boolean) => void;
addToolCall: (tc: ToolCallEvent) => void;
completeToolCall: (id: string, result: string, success: boolean) => void;
clearToolCalls: () => void;
upsertAgentActivity: (activity: AgentActivity) => void;
clearAgentActivities: () => void;
setLapwingStatus: (status: "idle" | "thinking" | "using_tool" | "delegating") => void;
```

Implement each action:
- `addToolCall`: append to `activeToolCalls`
- `completeToolCall`: find by `id`, set `result`, `success`, `completedAt`
- `upsertAgentActivity`: find by `commandId`, update if exists, push if not; on state `"done"/"failed"` set `completedAt`
- `clearAgentActivities`: reset to `[]`
- `setLapwingStatus`: set status string

Default values: `isStreaming: false`, `activeToolCalls: []`, `agentActivities: []`, `lapwingStatus: "idle"`.

- [ ] **Step 3: Add agent API wrappers in `src/lib/api.ts`**

`fetchJson` is a private function in `api.ts`. Following the existing pattern of named export wrappers, add after the `// ── Tasks ──` section:

```typescript
// ── Agents ──
export const getAgents = () =>
  fetchJson<{ agents: { name: string; status: string; capabilities: string[]; current_command_id: string | null }[] }>("/api/agents");
export const getActiveTasks = () =>
  fetchJson<{ tasks: { agent_name: string; command_id: string; status: string }[] }>("/api/agents/active");
export const cancelAgent = (agentName: string) =>
  fetchJson<{ success: boolean; error?: string }>(`/api/agents/${agentName}/cancel`, { method: "POST" });
```

- [ ] **Step 4: Verify build**

```bash
cd desktop-v2 && npx tsc --noEmit
```

Expected: No errors.

- [ ] **Step 5: Commit**

```bash
git add desktop-v2/src/types/chat.ts desktop-v2/src/stores/chat.ts desktop-v2/src/lib/api.ts
git commit -m "feat(desktop): extend chat types, store, and API for agent/tool state"
```

---

## Task 3: Extend WebSocket hook for new message types

**Files:**
- Modify: `desktop-v2/src/hooks/useWebSocket.ts`

- [ ] **Step 1: Add handlers for new message types**

In the `ws.onmessage` handler, add cases for the new message types. The existing handler at `desktop-v2/src/hooks/useWebSocket.ts:46-82` currently handles `reply|message`, `interim`, `status`, `typing`, `error`. Add:

```typescript
// After the existing "typing" case:

} else if (msg.type === "tool_call") {
  addToolCall({
    id: msg.call_id ?? crypto.randomUUID(),
    name: msg.name,
    arguments: msg.arguments ?? {},
    startedAt: Date.now(),
  });
  setLapwingStatus("using_tool");
  setToolStatus({
    phase: "executing",
    text: msg.name,
    toolName: msg.name,
  });
} else if (msg.type === "tool_result") {
  completeToolCall(msg.call_id ?? "", msg.result_preview ?? "", msg.success ?? true);
} else if (msg.type === "agent_emit") {
  upsertAgentActivity({
    commandId: msg.ref_id ?? msg.command_id ?? "",
    agentName: msg.agent_name,
    state: msg.state,
    progress: msg.progress ?? null,
    note: msg.note ?? null,
    headline: null,
    startedAt: Date.now(),
  });
  setLapwingStatus("delegating");
} else if (msg.type === "agent_notify") {
  upsertAgentActivity({
    commandId: msg.ref_command_id ?? "",
    agentName: msg.agent_name,
    state: msg.kind === "error" ? "failed" : "done",
    progress: 1,
    note: null,
    headline: msg.headline ?? null,
    startedAt: Date.now(),
  });
}
```

Also destructure the new store actions alongside the existing ones:

```typescript
const {
  addMessage, updateInterim, setWsStatus, setToolStatus,
  addToolCall, completeToolCall, upsertAgentActivity,
  setIsStreaming, setLapwingStatus, clearToolCalls,
} = useChatStore.getState();
```

Update the existing `typing` case to also call `setLapwingStatus("thinking")`.

Update the existing `reply`/`message` case (line 50-62) to also reset all streaming/tool state:
```typescript
setToolStatus(null);
setIsStreaming(false);
setLapwingStatus("idle");
clearToolCalls();
interimIdRef.current = null;
```

Note: The backend does not emit a separate `message_complete` event — the `reply` with `final: true` serves that purpose. All status/streaming cleanup happens in the existing `reply`/`message` handler.

- [ ] **Step 2: Verify build**

```bash
cd desktop-v2 && npx tsc --noEmit
```

- [ ] **Step 3: Commit**

```bash
git add desktop-v2/src/hooks/useWebSocket.ts
git commit -m "feat(desktop): handle tool_call, agent_emit, agent_notify in WebSocket hook"
```

---

## Task 4: Create ChatHeader component

**Files:**
- Create: `desktop-v2/src/components/chat/ChatHeader.tsx`

- [ ] **Step 1: Implement ChatHeader**

```tsx
import { useChatStore } from "@/stores/chat";
import { Loader2, Wifi, WifiOff, Bot, Wrench } from "lucide-react";

const STATUS_CONFIG = {
  idle: { icon: Bot, text: "Lapwing", className: "text-lapwing" },
  thinking: { icon: Loader2, text: "思考中...", className: "text-lapwing animate-spin" },
  using_tool: { icon: Wrench, text: "", className: "text-yellow-400" },
  delegating: { icon: Bot, text: "委派 Agent...", className: "text-blue-400" },
} as const;

export function ChatHeader() {
  const wsStatus = useChatStore((s) => s.wsStatus);
  const lapwingStatus = useChatStore((s) => s.lapwingStatus);
  const toolStatus = useChatStore((s) => s.toolStatus);

  const config = STATUS_CONFIG[lapwingStatus];
  const Icon = config.icon;
  const statusText = lapwingStatus === "using_tool"
    ? (toolStatus?.toolName ?? toolStatus?.text ?? "使用工具中...")
    : config.text;

  return (
    <div className="flex items-center justify-between px-4 py-2 border-b border-surface-border bg-void-100">
      <div className="flex items-center gap-2">
        <div className="w-7 h-7 rounded-full bg-gradient-to-br from-lapwing-light to-lapwing-dark flex items-center justify-center text-void text-xs font-bold">
          L
        </div>
        <div>
          <div className="text-sm font-medium text-text-accent">Lapwing</div>
          <div className="flex items-center gap-1 text-xs text-text-muted">
            <Icon size={12} className={config.className} />
            <span>{statusText}</span>
          </div>
        </div>
      </div>
      <div className="flex items-center gap-1 text-xs text-text-muted">
        {wsStatus === "connected" ? (
          <Wifi size={14} className="text-green-500" />
        ) : (
          <WifiOff size={14} className="text-red-400" />
        )}
        <span>{wsStatus === "connected" ? "已连接" : wsStatus === "connecting" ? "连接中..." : "已断开"}</span>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify build**

```bash
cd desktop-v2 && npx tsc --noEmit
```

- [ ] **Step 3: Commit**

```bash
git add desktop-v2/src/components/chat/ChatHeader.tsx
git commit -m "feat(desktop): add ChatHeader with connection/status display"
```

---

## Task 5: Add Markdown rendering to MessageBubble

**Files:**
- Modify: `desktop-v2/src/components/chat/MessageBubble.tsx`

- [ ] **Step 1: Add Markdown rendering for assistant messages**

Currently `MessageBubble.tsx` renders message content as plain text with `whitespace-pre-wrap`. Replace the segment rendering for assistant messages with `react-markdown`.

The key change is in the segment rendering inside the map at line 44-50. For assistant messages, replace the plain text `{segment}` with:

```tsx
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

// Inside the bubble div, replace {segment} for assistant messages:
{isUser ? (
  segment
) : (
  <Markdown
    remarkPlugins={[remarkGfm]}
    components={{
      p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
      a: ({ href, children }) => (
        <a href={href} className="text-lapwing hover:underline" target="_blank" rel="noopener noreferrer">
          {children}
        </a>
      ),
      pre: ({ children }) => (
        <pre className="bg-void rounded p-2 my-2 overflow-x-auto text-xs">{children}</pre>
      ),
      code: ({ children }) => (
        <code className="bg-void rounded px-1 py-0.5 text-xs">{children}</code>
      ),
      ul: ({ children }) => <ul className="list-disc pl-4 mb-2">{children}</ul>,
      ol: ({ children }) => <ol className="list-decimal pl-4 mb-2">{children}</ol>,
      li: ({ children }) => <li className="mb-0.5">{children}</li>,
      blockquote: ({ children }) => (
        <blockquote className="border-l-2 border-lapwing-border pl-3 my-2 text-text-secondary italic">
          {children}
        </blockquote>
      ),
      h1: ({ children }) => <h3 className="text-base font-semibold text-text-accent mb-1">{children}</h3>,
      h2: ({ children }) => <h3 className="text-base font-semibold text-text-accent mb-1">{children}</h3>,
      h3: ({ children }) => <h4 className="text-sm font-semibold text-text-accent mb-1">{children}</h4>,
    }}
  >
    {segment}
  </Markdown>
)}
```

Also remove `whitespace-pre-wrap` from the assistant bubble className (keep it for user messages only).

Update the bubble div className logic:
```tsx
className={`px-3 py-2 rounded-lg text-sm leading-relaxed break-words ${
  isUser
    ? "bg-lapwing-muted border border-lapwing-border text-text-primary whitespace-pre-wrap"
    : "bg-surface border border-surface-border text-text-primary"
}`}
```

- [ ] **Step 2: Add collapsible tool call display**

If `message.tool_calls` has items, render them below the message content. Add after the Markdown content:

```tsx
{!isUser && message.tool_calls && message.tool_calls.length > 0 && (
  <div className="mt-2 space-y-1">
    {message.tool_calls.map((tc, j) => (
      <ToolCallChip key={j} toolCall={tc} />
    ))}
  </div>
)}
```

Create a `ToolCallChip` component in the same file (or inline):

```tsx
import { useState } from "react";
import { ChevronDown, ChevronRight, Wrench } from "lucide-react";
import type { ToolCall } from "@/types/chat";

function ToolCallChip({ toolCall }: { toolCall: ToolCall }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="rounded bg-void border border-surface-border text-xs">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1.5 px-2 py-1 w-full text-left text-text-secondary hover:text-text-primary"
      >
        <Wrench size={12} className="text-lapwing shrink-0" />
        <span className="truncate">{toolCall.name}</span>
        {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
      </button>
      {expanded && (
        <div className="px-2 pb-1.5 text-text-muted border-t border-surface-border">
          <pre className="overflow-x-auto mt-1 whitespace-pre-wrap">
            {JSON.stringify(toolCall.arguments, null, 2)}
          </pre>
          {toolCall.result && (
            <div className="mt-1 pt-1 border-t border-surface-border text-text-secondary">
              {toolCall.result.slice(0, 200)}{toolCall.result.length > 200 ? "..." : ""}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Verify build and visual test**

```bash
cd desktop-v2 && npx tsc --noEmit
```

Then start dev server (`npm run dev`) and verify Markdown renders correctly in chat bubbles (send messages with bold, lists, code blocks).

- [ ] **Step 4: Commit**

```bash
git add desktop-v2/src/components/chat/MessageBubble.tsx
git commit -m "feat(desktop): add Markdown rendering and tool call chips to MessageBubble"
```

---

## Task 6: Create AgentActivityCard component

**Files:**
- Create: `desktop-v2/src/components/chat/AgentActivityCard.tsx`

- [ ] **Step 1: Implement AgentActivityCard**

This card renders inline in the message flow when Lapwing delegates to an agent.

```tsx
import { Bot, Loader2, CheckCircle2, XCircle } from "lucide-react";
import type { AgentActivity } from "@/types/chat";

const STATE_STYLES: Record<string, { icon: typeof Bot; color: string; bg: string }> = {
  queued: { icon: Loader2, color: "text-yellow-400", bg: "bg-yellow-900/20 border-yellow-800/30" },
  working: { icon: Loader2, color: "text-blue-400", bg: "bg-blue-900/20 border-blue-800/30" },
  done: { icon: CheckCircle2, color: "text-green-400", bg: "bg-green-900/20 border-green-800/30" },
  failed: { icon: XCircle, color: "text-red-400", bg: "bg-red-900/20 border-red-800/30" },
  blocked: { icon: XCircle, color: "text-orange-400", bg: "bg-orange-900/20 border-orange-800/30" },
  cancelled: { icon: XCircle, color: "text-text-muted", bg: "bg-surface border-surface-border" },
};

export function AgentActivityCard({ activity }: { activity: AgentActivity }) {
  const style = STATE_STYLES[activity.state] ?? STATE_STYLES.queued;
  const Icon = style.icon;
  const isAnimated = activity.state === "queued" || activity.state === "working";

  return (
    <div className={`flex items-start gap-3 px-3 py-2 rounded-lg border text-sm ${style.bg} ml-10`}>
      <Icon size={16} className={`${style.color} shrink-0 mt-0.5 ${isAnimated ? "animate-spin" : ""}`} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-medium text-text-accent">{activity.agentName}</span>
          <span className={`text-xs ${style.color}`}>{activity.state}</span>
        </div>
        {activity.note && (
          <div className="text-xs text-text-secondary mt-0.5 truncate">{activity.note}</div>
        )}
        {activity.headline && (
          <div className="text-xs text-text-primary mt-1">{activity.headline}</div>
        )}
        {activity.progress != null && activity.state === "working" && (
          <div className="mt-1.5 h-1 rounded-full bg-void overflow-hidden">
            <div
              className="h-full rounded-full bg-blue-400 transition-all duration-300"
              style={{ width: `${Math.round(activity.progress * 100)}%` }}
            />
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify build**

```bash
cd desktop-v2 && npx tsc --noEmit
```

- [ ] **Step 3: Commit**

```bash
git add desktop-v2/src/components/chat/AgentActivityCard.tsx
git commit -m "feat(desktop): add AgentActivityCard for inline agent status display"
```

---

## Task 7: Update MessageList with agent activities and smart scroll

**Files:**
- Modify: `desktop-v2/src/components/chat/MessageList.tsx`

- [ ] **Step 1: Add agent activities to the message flow**

Import the new component and store fields. After the tool status indicator and before the bottom ref, render active agent activities:

```tsx
import { AgentActivityCard } from "./AgentActivityCard";

// In the component:
const agentActivities = useChatStore((s) => s.agentActivities);
```

Add after the `toolStatus` block (around line 54-58):

```tsx
{agentActivities.filter(a => a.state !== "done" && a.state !== "failed").map((activity) => (
  <div key={activity.commandId} className="pl-0">
    <AgentActivityCard activity={activity} />
  </div>
))}
```

- [ ] **Step 2: Add smart auto-scroll (pause when user scrolls up)**

Replace `ScrollArea` with a plain `div` (shadcn's `ScrollArea` wraps Radix's viewport and does not forward scroll event handlers). Update the component:

```tsx
const scrollContainerRef = useRef<HTMLDivElement>(null);
const isUserScrolledUp = useRef(false);

const handleScroll = useCallback(() => {
  const el = scrollContainerRef.current;
  if (!el) return;
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 100;
  isUserScrolledUp.current = !atBottom;
}, []);

useEffect(() => {
  if (!isUserScrolledUp.current) {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }
}, [messages, toolStatus, agentActivities]);
```

Replace the `<ScrollArea>` wrapper in the JSX with:

```tsx
<div
  ref={scrollContainerRef}
  onScroll={handleScroll}
  className="flex-1 overflow-y-auto px-4"
>
```

Remove the `ScrollArea` import since it's no longer used in this component.

- [ ] **Step 3: Verify build**

```bash
cd desktop-v2 && npx tsc --noEmit
```

- [ ] **Step 4: Commit**

```bash
git add desktop-v2/src/components/chat/MessageList.tsx
git commit -m "feat(desktop): add agent activities to message flow and smart scroll"
```

---

## Task 8: Create AgentPanel component

**Files:**
- Create: `desktop-v2/src/components/chat/AgentPanel.tsx`

- [ ] **Step 1: Implement AgentPanel**

This is a collapsible right sidebar in ChatPage showing all registered agents and their current tasks. It fetches data from `/api/agents` and `/api/agents/active`.

```tsx
import { useState, useEffect, useCallback } from "react";
import { Bot, XCircle, RefreshCw, ChevronRight, ChevronLeft } from "lucide-react";
import { getAgents, cancelAgent } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { useChatStore } from "@/stores/chat";

interface AgentInfo {
  name: string;
  status: string;
  capabilities: string[];
  current_command_id: string | null;
}

const STATUS_DOT: Record<string, string> = {
  idle: "bg-green-500",
  busy: "bg-blue-500 animate-pulse",
  error: "bg-red-500",
  disabled: "bg-gray-500",
};

export function AgentPanel() {
  const [collapsed, setCollapsed] = useState(false);
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const agentActivities = useChatStore((s) => s.agentActivities);

  const fetchAgentList = useCallback(async () => {
    try {
      const data = await getAgents();
      setAgents(data.agents as AgentInfo[]);
    } catch {
      // offline or not available
    }
  }, []);

  useEffect(() => {
    fetchAgentList();
    const id = setInterval(fetchAgents, 10_000);
    return () => clearInterval(id);
  }, [fetchAgentList]);

  const handleCancel = async (agentName: string) => {
    try {
      await cancelAgent(agentName);
      fetchAgentList();
    } catch {
      // ignore
    }
  };

  if (collapsed) {
    return (
      <button
        onClick={() => setCollapsed(false)}
        className="flex items-center justify-center w-8 h-full border-l border-surface-border bg-void-100 hover:bg-surface-hover"
        title="展开 Agent 面板"
      >
        <ChevronLeft size={14} className="text-text-muted" />
      </button>
    );
  }

  // Completed activities (most recent first, max 5)
  const completedActivities = agentActivities
    .filter(a => a.state === "done" || a.state === "failed")
    .slice(-5)
    .reverse();

  return (
    <div className="w-[240px] shrink-0 border-l border-surface-border bg-void-100 flex flex-col">
      <div className="flex items-center justify-between px-3 py-2 border-b border-surface-border">
        <div className="flex items-center gap-1.5 text-sm font-medium text-text-accent">
          <Bot size={14} />
          <span>Agents</span>
          <span className="text-xs text-text-muted">({agents.length})</span>
        </div>
        <div className="flex items-center gap-1">
          <button onClick={fetchAgents} className="p-1 hover:bg-surface-hover rounded" title="刷新">
            <RefreshCw size={12} className="text-text-muted" />
          </button>
          <button onClick={() => setCollapsed(true)} className="p-1 hover:bg-surface-hover rounded" title="折叠">
            <ChevronRight size={12} className="text-text-muted" />
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-2 space-y-2">
        {agents.length === 0 && (
          <div className="text-xs text-text-muted text-center py-4">
            Agent 系统未启用
          </div>
        )}
        {agents.map((agent) => (
          <div key={agent.name} className="bg-surface border border-surface-border rounded-lg p-2">
            <div className="flex items-center gap-2">
              <div className={`w-2 h-2 rounded-full ${STATUS_DOT[agent.status] ?? STATUS_DOT.disabled}`} />
              <span className="text-sm text-text-primary font-medium">{agent.name}</span>
            </div>
            <div className="text-xs text-text-muted mt-1 truncate">
              {agent.capabilities.join(", ")}
            </div>
            {agent.status === "busy" && (
              <Button
                variant="ghost"
                size="sm"
                className="mt-1 h-6 text-xs text-red-400 hover:text-red-300 px-2"
                onClick={() => handleCancel(agent.name)}
              >
                <XCircle size={12} className="mr-1" />
                取消
              </Button>
            )}
          </div>
        ))}

        {completedActivities.length > 0 && (
          <>
            <div className="text-xs text-text-muted pt-2 pb-1">最近任务</div>
            {completedActivities.map((a) => (
              <div key={a.commandId} className="bg-surface border border-surface-border rounded p-2 text-xs">
                <div className="flex items-center gap-1.5">
                  <span className={a.state === "done" ? "text-green-400" : "text-red-400"}>
                    {a.state === "done" ? "✓" : "✗"}
                  </span>
                  <span className="text-text-primary font-medium">{a.agentName}</span>
                </div>
                {a.headline && (
                  <div className="text-text-secondary mt-0.5 line-clamp-2">{a.headline}</div>
                )}
              </div>
            ))}
          </>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify build**

```bash
cd desktop-v2 && npx tsc --noEmit
```

- [ ] **Step 3: Commit**

```bash
git add desktop-v2/src/components/chat/AgentPanel.tsx
git commit -m "feat(desktop): add AgentPanel sidebar with agent status and task history"
```

---

## Task 9: Rewrite ChatPage with full layout

**Files:**
- Rewrite: `desktop-v2/src/pages/ChatPage.tsx`

- [ ] **Step 1: Implement full ChatPage layout**

```tsx
import { useEffect, useCallback } from "react";
import { useChatStore } from "@/stores/chat";
import { useWebSocket } from "@/hooks/useWebSocket";
import { getChatHistory } from "@/lib/api";
import { MessageList } from "@/components/chat/MessageList";
import { MessageInput } from "@/components/chat/MessageInput";
import { ChatHeader } from "@/components/chat/ChatHeader";
import { AgentPanel } from "@/components/chat/AgentPanel";

export default function ChatPage() {
  const wsStatus = useChatStore((s) => s.wsStatus);
  const chatId = useChatStore((s) => s.chatId);
  const messages = useChatStore((s) => s.messages);
  const { send } = useWebSocket();

  // Load chat history on mount (only if no messages loaded yet)
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

      {/* Agent panel (right sidebar) */}
      <AgentPanel />
    </div>
  );
}
```

Note: `chatId` is set by the WebSocket handler (from the `presence_ack` or first message). If `chatId` is empty, history loading is skipped until it's available.

- [ ] **Step 2: Verify build**

```bash
cd desktop-v2 && npx tsc --noEmit
```

- [ ] **Step 3: Visual test**

Start dev server with `cd desktop-v2 && npm run dev`, open `http://localhost:1420/chat`, verify:
- ChatHeader shows at top with "Lapwing" and connection status
- Message list fills the middle
- Input bar at bottom
- AgentPanel visible on the right (shows "Agent 系统未启用" if no agents)
- Layout is responsive — main chat area takes remaining space

- [ ] **Step 4: Commit**

```bash
git add desktop-v2/src/pages/ChatPage.tsx
git commit -m "feat(desktop): rewrite ChatPage with header, agent panel layout"
```

---

## Task 10: Backend — Create agents REST API

**Files:**
- Create: `src/api/routes/agents.py`

- [ ] **Step 1: Implement agents routes**

```python
"""Agent 管理 API 端点。"""

import logging

from fastapi import APIRouter

logger = logging.getLogger("lapwing.api.routes.agents")

router = APIRouter(prefix="/api/agents", tags=["agents"])

_brain = None


def init(brain) -> None:
    global _brain
    _brain = brain


@router.get("")
async def list_agents():
    """获取所有注册 Agent 的信息。"""
    registry = getattr(_brain, "agent_registry", None)
    if registry is not None:
        return {"agents": registry.list_agents()}
    return {"agents": []}


@router.get("/active")
async def get_active_tasks():
    """获取当前活跃的 Agent 任务。"""
    dispatcher = getattr(_brain, "agent_dispatcher", None)
    if dispatcher is not None:
        return {"tasks": dispatcher.get_active_tasks()}
    return {"tasks": []}


@router.post("/{agent_name}/cancel")
async def cancel_agent_task(agent_name: str):
    """取消指定 Agent 的当前任务。"""
    dispatcher = getattr(_brain, "agent_dispatcher", None)
    if dispatcher is not None:
        success = await dispatcher.cancel_agent(agent_name)
        return {"success": success}
    return {"success": False, "error": "Agent system not available"}
```

- [ ] **Step 2: Commit**

```bash
git add src/api/routes/agents.py
git commit -m "feat(api): add /api/agents REST endpoints for agent status and control"
```

---

## Task 11: Backend — Mount agents router in server.py

**Files:**
- Modify: `src/api/server.py`

- [ ] **Step 1: Mount the agents router**

In `create_app()` at `src/api/server.py`, after the existing router mounts (around line 66-70), add:

```python
from src.api.routes import agents as _agents_routes
_agents_routes.init(brain)
app.include_router(_agents_routes.router)
```

Insert after `app.include_router(_logs_routes.router)` (line 70) and before the browser routes conditional block (line 73).

- [ ] **Step 2: Commit**

```bash
git add src/api/server.py
git commit -m "feat(api): mount agents router in FastAPI server"
```

---

## Task 12: Backend — Forward agent events through WebSocket

**Files:**
- Modify: `src/api/routes/chat_ws.py`
- Modify: `src/app/container.py`

- [ ] **Step 1: Add agent event forwarding in chat_ws.py**

Add a module-level dict to track WebSocket connections by chat_id, and add functions to forward agent events:

After the global variables at line 14-15, add:

```python
# chat_id → WebSocket 映射，用于 Agent 事件推送
_chat_ws_map: dict[str, WebSocket] = {}


async def forward_agent_progress(chat_id: str, emit) -> None:
    """将 AgentEmit 推送到对应的 WebSocket 客户端。"""
    ws = _chat_ws_map.get(chat_id)
    if ws is None:
        return
    try:
        await ws.send_json({
            "type": "agent_emit",
            "agent_name": emit.agent_name,
            "ref_id": emit.ref_id,
            "state": emit.state.value if hasattr(emit.state, "value") else str(emit.state),
            "progress": emit.progress,
            "note": emit.note,
        })
    except Exception:
        pass


async def forward_agent_result(chat_id: str, notify) -> None:
    """将 AgentNotify 推送到对应的 WebSocket 客户端。"""
    ws = _chat_ws_map.get(chat_id)
    if ws is None:
        return
    try:
        await ws.send_json({
            "type": "agent_notify",
            "agent_name": notify.agent_name,
            "kind": notify.kind.value if hasattr(notify.kind, "value") else str(notify.kind),
            "headline": notify.headline,
            "detail": notify.detail,
            "ref_command_id": notify.ref_command_id,
        })
    except Exception:
        pass
```

In the `websocket_chat` function, register the WebSocket in `_chat_ws_map` when `chat_id` is determined (after line 69), and clean up in the `finally` block:

After `chat_id` is computed (around line 67-69):
```python
_chat_ws_map[chat_id] = ws
```

In the `finally` block (after line 117), add:
```python
# 清理 chat_id → ws 映射
for cid, w in list(_chat_ws_map.items()):
    if w is ws:
        del _chat_ws_map[cid]
```

Note: `chat_id` is computed inside the `if msg_type == "message":` block, so the map registration should happen there. But since the WS connection might handle multiple messages with the same chat_id, just update it each time.

- [ ] **Step 2: Wire callbacks in container.py**

In `src/app/container.py`, in the `AGENT_TEAM_ENABLED` block (around line 317-347), after creating the `AgentDispatcher`, set the progress/result callbacks:

```python
from src.api.routes.chat_ws import forward_agent_progress, forward_agent_result

self.brain.agent_dispatcher = AgentDispatcher(
    registry=agent_registry,
    task_runtime=self.brain.task_runtime,
    on_progress=forward_agent_progress,
    on_result=forward_agent_result,
)
```

This replaces the existing `AgentDispatcher(...)` call that has no callbacks.

- [ ] **Step 3: Verify the backend starts**

```bash
python -c "from src.api.routes.agents import router; print('agents routes OK')"
python -c "from src.api.routes.chat_ws import forward_agent_progress; print('ws forwarding OK')"
```

- [ ] **Step 4: Commit**

```bash
git add src/api/routes/chat_ws.py src/app/container.py
git commit -m "feat(api): wire agent progress/result forwarding through WebSocket"
```

---

## Task 13: Frontend visual integration test

**Files:** No new files — verification only.

- [ ] **Step 1: Start the dev server**

```bash
cd desktop-v2 && npm run dev
```

- [ ] **Step 2: Verify ChatPage layout**

Open `http://localhost:1420/chat` and verify:
1. ChatHeader visible at top with "Lapwing" branding and connection indicator
2. Message area fills center with proper scrolling
3. Input area at bottom, Enter sends, Shift+Enter newline
4. AgentPanel visible on right (can collapse/expand)
5. Markdown renders in assistant messages (bold, code blocks, lists)
6. Tool calls show as collapsible chips below assistant messages

- [ ] **Step 3: Verify type correctness**

```bash
cd desktop-v2 && npx tsc --noEmit
```

Expected: No type errors.

- [ ] **Step 4: Final commit if any adjustments needed**

```bash
git add -A
git commit -m "fix(desktop): visual polish for ChatPage integration"
```

---

## Summary

| Task | What | Files |
|------|------|-------|
| 1 | Install deps | package.json |
| 2 | Types + store | types/chat.ts, stores/chat.ts |
| 3 | WebSocket hook | hooks/useWebSocket.ts |
| 4 | ChatHeader | components/chat/ChatHeader.tsx |
| 5 | Markdown + tools | components/chat/MessageBubble.tsx |
| 6 | AgentActivityCard | components/chat/AgentActivityCard.tsx |
| 7 | MessageList update | components/chat/MessageList.tsx |
| 8 | AgentPanel | components/chat/AgentPanel.tsx |
| 9 | ChatPage rewrite | pages/ChatPage.tsx |
| 10 | Agents REST API | api/routes/agents.py |
| 11 | Mount router | api/server.py |
| 12 | WS forwarding | api/routes/chat_ws.py, app/container.py |
| 13 | Integration test | (verification) |
