import type { ServerStatus, SystemStats, ChannelInfo, HeartbeatStatus, ReminderItem } from "@/types/api";

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

export function getAuthHeaders(): HeadersInit {
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
  if (!res.ok) throw new Error(`接口错误 ${res.status}: ${res.statusText}`);
  return res.json();
}

// ── V2 API ──

// Status
export const getStatus = () => fetchJson<ServerStatus>("/api/v2/status");
export const getSystemStats = () => fetchJson<SystemStats>("/api/v2/system/stats");

// Channels
export const getChannels = () => fetchJson<{ platforms: ChannelInfo[] }>("/api/v2/system/channels");

// Heartbeat / Consciousness
export const getHeartbeatStatus = () => fetchJson<HeartbeatStatus>("/api/v2/system/consciousness");

// Reminders
export const getReminders = () =>
  fetchJson<{ reminders: ReminderItem[] }>("/api/v2/system/reminders");

// Chat History
export const getChatHistory = (chatId: string, limit = 50, before?: string) => {
  const params = new URLSearchParams({ chat_id: chatId, limit: String(limit) });
  if (before) params.set("before", before);
  return fetchJson<{ messages: import("@/types/chat").ChatMessage[]; has_more: boolean }>(
    `/api/chat/history?${params}`
  );
};

// Agents
export const getAgents = () =>
  fetchJson<{ agents: { name: string; status: string; capabilities: string[]; current_command_id: string | null }[] }>("/api/agents");
export const getActiveTasks = () =>
  fetchJson<{ tasks: { agent_name: string; command_id: string; status: string }[] }>("/api/agents/active");
export const cancelAgent = (agentName: string) =>
  fetchJson<{ success: boolean; error?: string }>(`/api/agents/${agentName}/cancel`, { method: "POST" });

// Reload
export const reloadPrompt = () => fetchJson<{ ok: boolean }>("/api/v2/system/reload", { method: "POST" });

// Auth
export const createSession = (bootstrapToken: string) =>
  fetchJson<{ ok: boolean }>("/api/auth/session", {
    method: "POST",
    body: JSON.stringify({ token: bootstrapToken }),
  });
