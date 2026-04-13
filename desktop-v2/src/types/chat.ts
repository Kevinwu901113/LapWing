export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: string;
  session_id?: string;
  tool_calls?: ToolCall[];
}

export interface ToolCall {
  name: string;
  arguments: Record<string, unknown>;
  result?: string;
}

export interface ToolStatusInfo {
  phase: string;
  text: string;
  toolName?: string;
}

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
