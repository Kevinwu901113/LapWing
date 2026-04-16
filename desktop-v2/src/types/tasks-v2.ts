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
  actor: string;
  content: string;
  event_type: string;
  tool_name?: string;
  tool_args?: Record<string, unknown>;
}
