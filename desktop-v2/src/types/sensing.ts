export interface AppUsageRecord {
  process_name: string;
  app_display_name: string;
  total_seconds: number;
  category: string;
}

export interface WindowEvent {
  timestamp: string;
  process_name: string;
  window_title: string;
  duration_seconds: number;
}

export interface SessionEvent {
  timestamp: string;
  event_type: "boot" | "shutdown" | "lock" | "unlock" | "game_start" | "game_end";
  detail?: string;
}

export interface SilenceState {
  active: boolean;
  game_name?: string;
  started_at?: string;
}
