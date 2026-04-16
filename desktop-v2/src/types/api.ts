export interface ServerStatus {
  online: boolean;
  started_at: string;
  chat_count: number;
  last_interaction: string | null;
}

export interface SystemStats {
  cpu_percent: number;
  memory_percent: number;
  disk_percent: number;
  disk_free_gb: number;
}

export interface ChannelInfo {
  channel: string;
  connected: boolean;
  adapter_type?: string;
}

export interface HeartbeatStatus {
  last_fast_tick: string;
  last_slow_tick: string;
  next_fast_tick: string;
  last_action: string;
}

export interface ReminderItem {
  id: number;
  content: string;
  recurrence_type: string;
  trigger_at: string;
  status: string;
}
