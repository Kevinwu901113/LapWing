export interface LapwingStatus {
  state: "idle" | "thinking" | "working" | "browsing";
  current_task_id: string | null;
  current_task_request?: string | null;
  last_interaction: string | null;
  heartbeat_next?: string | null;
  active_agents: string[];
}
