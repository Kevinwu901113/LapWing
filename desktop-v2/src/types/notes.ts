export interface NoteTreeEntry {
  name: string;
  path: string;
  type: "file" | "directory";
  children?: NoteTreeEntry[];
}

export interface NoteContent {
  meta: Record<string, unknown>;
  content: string;
  file_path: string;
}

export interface NoteSearchResult {
  note_id?: string;
  content: string;
  score?: number;
  note_type?: string;
  file_path: string;
}
