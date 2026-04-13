import type { ServerStatus, SystemStats, ChannelInfo, HeartbeatStatus, ReminderItem, LearningItem, MemoryItem, MemoryHealth, PersonaFile, ChangelogEntry } from "@/types/api";

export function getApiBase(): string {
  if (typeof window !== "undefined") {
    const stored = localStorage.getItem("lapwing_server_url");
    if (stored) return stored.replace(/\/$/, "");
  }
  const envBase = import.meta.env.VITE_LAPWING_API_BASE as string | undefined;
  if (envBase) return envBase;
  if (typeof window !== "undefined" && ["http:", "https:"].includes(window.location.protocol)) {
    return "";
  }
  return "http://127.0.0.1:8765";
}

function getAuthHeaders(): HeadersInit {
  const token = localStorage.getItem("lapwing_desktop_token");
  if (token) return { Authorization: `Bearer ${token}` };
  return {};
}

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
  if (!res.ok) throw new Error(`API ${res.status}: ${res.statusText}`);
  return res.json();
}

// ── Status ──
export const getStatus = () => fetchJson<ServerStatus>("/api/status");
export const getSystemStats = () => fetchJson<SystemStats>("/api/system/stats");

// ── Channels ──
export const getChannels = () => fetchJson<{ platforms: ChannelInfo[] }>("/api/config/platforms");

// ── Heartbeat ──
export const getHeartbeatStatus = () => fetchJson<HeartbeatStatus>("/api/heartbeat/status");

// ── Reminders ──
export const getReminders = (chatId?: string) =>
  fetchJson<{ reminders: ReminderItem[] }>(`/api/reminders${chatId ? `?chat_id=${chatId}` : ""}`);
export const deleteReminder = (id: number, chatId: string) =>
  fetchJson<{ ok: boolean }>(`/api/reminders/${id}?chat_id=${chatId}`, { method: "DELETE" });

// ── Memory ──
export const getMemory = (chatId?: string) =>
  fetchJson<{ facts: MemoryItem[] }>(`/api/memory${chatId ? `?chat_id=${chatId}` : ""}`);
export const getMemoryHealth = () => fetchJson<MemoryHealth>("/api/memory/health");
export const getMemorySummaries = () => fetchJson<{ summaries: string[] }>("/api/memory/summaries");

// ── Learnings ──
export const getLearnings = () => fetchJson<{ learnings: LearningItem[] }>("/api/learnings");

// ── Interests ──
export const getInterests = (chatId?: string) =>
  fetchJson<{ interests: { topic: string; weight: number }[] }>(`/api/interests${chatId ? `?chat_id=${chatId}` : ""}`);

// ── Persona ──
export const getPersonaFiles = () => fetchJson<{ files: PersonaFile[] }>("/api/persona/files");
export const updatePersonaFile = (name: string, content: string) =>
  fetchJson<{ ok: boolean }>(`/api/persona/files/${name}`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });
export const getChangelog = () => fetchJson<{ entries: ChangelogEntry[] }>("/api/persona/changelog");
export const reloadPrompt = () => fetchJson<{ ok: boolean }>("/api/reload", { method: "POST" });
export const triggerEvolve = () => fetchJson<{ ok: boolean }>("/api/evolve", { method: "POST" });

// ── Chat History ──
export const getChatHistory = (chatId: string, limit = 50, before?: string) => {
  const params = new URLSearchParams({ chat_id: chatId, limit: String(limit) });
  if (before) params.set("before", before);
  return fetchJson<{ messages: import("@/types/chat").ChatMessage[]; has_more: boolean }>(
    `/api/chat/history?${params}`
  );
};

// ── Tasks ──
export const getTasks = (limit = 20) =>
  fetchJson<{ tasks: import("@/types/tasks").TaskFlow[] }>(`/api/tasks?limit=${limit}`);
export const getTaskFlows = () =>
  fetchJson<{ flows: import("@/types/tasks").TaskFlow[] }>("/api/task-flows");
export const cancelTaskFlow = (flowId: string) =>
  fetchJson<{ ok: boolean }>(`/api/task-flows/${flowId}/cancel`, { method: "POST" });

// ── Agents ──
export const getAgents = () =>
  fetchJson<{ agents: { name: string; status: string; capabilities: string[]; current_command_id: string | null }[] }>("/api/agents");
export const getActiveTasks = () =>
  fetchJson<{ tasks: { agent_name: string; command_id: string; status: string }[] }>("/api/agents/active");
export const cancelAgent = (agentName: string) =>
  fetchJson<{ success: boolean; error?: string }>(`/api/agents/${agentName}/cancel`, { method: "POST" });

// ── Auth ──
export const createSession = (bootstrapToken: string) =>
  fetchJson<{ ok: boolean }>("/api/auth/session", {
    method: "POST",
    body: JSON.stringify({ token: bootstrapToken }),
  });
