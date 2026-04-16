export interface SystemInfo {
  uptime_seconds: number;
  cpu_percent: number;
  memory: { total: number; available: number; percent: number };
  disk: { total: number; free: number; percent: number };
  consciousness?: {
    current_interval: number | null;
    idle_streak: number;
    next_tick_at: string | null;
  };
  channels: Record<string, string | boolean>;
}

export interface SystemEvent {
  event_id: string;
  timestamp: string;
  event_type: string;
  actor: string;
  task_id?: string;
  payload: Record<string, unknown>;
}
