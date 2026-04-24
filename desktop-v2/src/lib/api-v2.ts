import type { LapwingStatus } from "@/types/status-v2";
import type { TaskV2 } from "@/types/tasks-v2";
import type { AgentMessage } from "@/types/tasks-v2";
import type { PermissionsResponse, PermissionDefaultsResponse } from "@/types/permissions";
import type { ModelRoutingConfig, ProviderPayload } from "@/types/models";
import type { NoteTreeEntry, NoteContent, NoteSearchResult } from "@/types/notes";
import type { IdentityFile, SoulSnapshot, SoulDiff } from "@/types/identity";
import type { SystemInfo, SystemEvent } from "@/types/system";
import { getApiBase, getAuthHeaders } from "./api";

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const base = getApiBase();
  const res = await fetch(`${base}${path}`, {
    credentials: "include",
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...getAuthHeaders(),
      ...init?.headers,
    },
  });
  if (!res.ok) throw new Error(`API error ${res.status}: ${res.statusText}`);
  return res.json();
}

// ── Status v2 ──
export const getStatusV2 = () =>
  fetchJson<LapwingStatus>("/api/v2/status");

// ── Tasks v2 ──
export const getTasksV2 = (status?: string, limit = 50) => {
  const params = new URLSearchParams({ limit: String(limit) });
  if (status) params.set("status", status);
  return fetchJson<{ tasks: TaskV2[]; count: number }>(`/api/v2/tasks?${params}`);
};

export const getTaskV2 = (taskId: string) =>
  fetchJson<TaskV2>(`/api/v2/tasks/${taskId}`);

/** Backend returns messages with payload dict. We transform to flat AgentMessage. */
export const getTaskMessages = async (taskId: string): Promise<{ messages: AgentMessage[] }> => {
  const raw = await fetchJson<{
    task_id: string;
    messages: { event_id: string; event_type: string; timestamp: string; actor: string; payload: Record<string, unknown> }[];
  }>(`/api/v2/tasks/${taskId}/messages`);

  return {
    messages: raw.messages.map((m) => ({
      event_id: m.event_id,
      timestamp: m.timestamp,
      actor: m.actor,
      content: (m.payload.content as string) ?? (m.payload.summary as string) ?? "",
      event_type: m.event_type,
      tool_name: m.payload.tool_name as string | undefined,
      tool_args: m.payload.tool_args as Record<string, unknown> | undefined,
    })),
  };
};

// ── Models v2 ──
export const getModelRouting = () =>
  fetchJson<ModelRoutingConfig>("/api/v2/models/routing");

export const updateModelRouting = (slots: Record<string, { provider_id: string; model_id: string }>) =>
  fetchJson<{ success: boolean }>("/api/v2/models/routing", {
    method: "PUT",
    body: JSON.stringify({ slots }),
  });

export const createModelProvider = (provider: ProviderPayload) =>
  fetchJson<{ status: string; provider_id: string }>("/api/v2/models/providers", {
    method: "POST",
    body: JSON.stringify(provider),
  });

export const updateModelProvider = (providerId: string, provider: Partial<ProviderPayload>) =>
  fetchJson<{ status: string }>("/api/v2/models/providers/" + encodeURIComponent(providerId), {
    method: "PUT",
    body: JSON.stringify(provider),
  });

export const deleteModelProvider = (providerId: string) =>
  fetchJson<{ status: string }>("/api/v2/models/providers/" + encodeURIComponent(providerId), {
    method: "DELETE",
  });

export const getAvailableModels = () =>
  fetchJson<{ slots: string[]; slot_definitions: Record<string, unknown>; providers: unknown[] }>("/api/v2/models/available");

// ── Permissions v2 ──
export const getPermissions = () =>
  fetchJson<PermissionsResponse>("/api/v2/permissions");

export const setPermission = (userId: string, level: number, name?: string, note?: string) =>
  fetchJson<{ success: boolean; user_id: string; level: number }>(`/api/v2/permissions/${encodeURIComponent(userId)}`, {
    method: "PUT",
    body: JSON.stringify({ level, name: name ?? "", note: note ?? "" }),
  });

export const deletePermission = (userId: string) =>
  fetchJson<{ success: boolean }>(`/api/v2/permissions/${encodeURIComponent(userId)}`, {
    method: "DELETE",
  });

export const getPermissionDefaults = () =>
  fetchJson<PermissionDefaultsResponse>("/api/v2/permissions/defaults");

// ── System v2 ──
export const getSystemInfo = () =>
  fetchJson<SystemInfo>("/api/v2/system/info");

export const getSystemEvents = (params?: { event_type?: string; task_id?: string; limit?: number }) => {
  const search = new URLSearchParams();
  if (params?.event_type) search.set("event_type", params.event_type);
  if (params?.task_id) search.set("task_id", params.task_id);
  if (params?.limit) search.set("limit", String(params.limit));
  return fetchJson<{ events: SystemEvent[] }>(`/api/v2/system/events?${search}`);
};

// ── Notes v2 ──
export const getNotesTree = (path = "") =>
  fetchJson<{ path: string; entries: NoteTreeEntry[] }>(`/api/v2/notes/tree?path=${encodeURIComponent(path)}`);

export const getNoteContent = (params: { note_id?: string; path?: string }) => {
  const search = new URLSearchParams();
  if (params.note_id) search.set("note_id", params.note_id);
  if (params.path) search.set("path", params.path);
  return fetchJson<NoteContent>(`/api/v2/notes/content?${search}`);
};

export const searchNotes = (q: string, limit = 20) =>
  fetchJson<{ query: string; results: NoteSearchResult[] }>(`/api/v2/notes/search?q=${encodeURIComponent(q)}&limit=${limit}`);

export const recallNotes = (q: string, topK = 10) =>
  fetchJson<{ query: string; results: NoteSearchResult[] }>(`/api/v2/notes/recall?q=${encodeURIComponent(q)}&top_k=${topK}`);

// ── Identity v2 ──
export const getIdentityFile = (filename: string) =>
  fetchJson<IdentityFile>(`/api/v2/identity/${encodeURIComponent(filename)}`);

export const updateIdentityFile = (filename: string, content: string) =>
  fetchJson<{ success: boolean; reason?: string }>(`/api/v2/identity/${encodeURIComponent(filename)}`, {
    method: "PUT",
    body: JSON.stringify({ content }),
  });

export const getSoulHistory = () =>
  fetchJson<{ snapshots: SoulSnapshot[] }>("/api/v2/identity/soul/history");

export const getSoulDiff = (snapshotId: string) =>
  fetchJson<SoulDiff>(`/api/v2/identity/soul/diff/${encodeURIComponent(snapshotId)}`);

export const rollbackSoul = (snapshotId: string) =>
  fetchJson<{ success: boolean; reason?: string }>(`/api/v2/identity/soul/rollback/${encodeURIComponent(snapshotId)}`, {
    method: "POST",
  });
