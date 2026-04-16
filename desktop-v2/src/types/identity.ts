export interface IdentityFile {
  filename: string;
  content: string;
}

export interface SoulSnapshot {
  snapshot_id: string;
  timestamp: string;
  actor: string;
  trigger: string;
  diff_summary?: string;
}

export interface SoulDiff {
  snapshot_id: string;
  diff: string | Record<string, unknown>;
}
