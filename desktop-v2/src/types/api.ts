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

export interface LearningItem {
  filename: string;
  date: string;
  preview: string;
}

export interface MemoryItem {
  id: number;
  key: string;
  value: string;
  source: string;
  created_at: string;
}

export interface MemoryHealth {
  total_entries: number;
  journal_count: number;
  knowledge_count: number;
  health_score: number;
}

export interface PersonaFile {
  name: string;
  content: string;
  readonly: boolean;
}

export interface ChangelogEntry {
  timestamp: string;
  changes: string;
}
