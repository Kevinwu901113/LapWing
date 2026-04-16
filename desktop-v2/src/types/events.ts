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
