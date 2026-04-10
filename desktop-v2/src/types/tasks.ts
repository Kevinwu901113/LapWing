export interface TaskFlow {
  flow_id: string;
  title: string;
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  steps: TaskStep[];
  created_at: string;
}

export interface TaskStep {
  step_id: string;
  description: string;
  status: string;
  tool_name?: string;
  result?: Record<string, unknown>;
}
