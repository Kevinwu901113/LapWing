export const API_BASE = "";

export type StatusResponse = {
  online: boolean;
  started_at: string;
  chat_count: number;
  last_interaction: string | null;
};

export type ChatSummary = {
  chat_id: string;
  last_interaction: string | null;
};

export type InterestItem = {
  topic: string;
  weight: number;
  last_seen: string;
};

export type MemoryItem = {
  index: number;
  fact_key: string;
  fact_value: string;
  updated_at: string | null;
};

export type LearningItem = {
  filename: string;
  date: string;
  updated_at: string;
  content: string;
};

export type DesktopEvent = {
  type: string;
  timestamp: string;
  payload: {
    chat_id: string;
    text: string;
    topic?: string;
  };
};

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
    },
    ...init,
  });

  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function getStatus() {
  return fetchJson<StatusResponse>("/api/status");
}

export function getChats() {
  return fetchJson<ChatSummary[]>("/api/chats");
}

export async function getInterests(chatId: string) {
  return fetchJson<{ chat_id: string; items: InterestItem[] }>(
    `/api/interests?chat_id=${encodeURIComponent(chatId)}`,
  );
}

export async function getMemory(chatId: string) {
  return fetchJson<{ chat_id: string; items: MemoryItem[] }>(
    `/api/memory?chat_id=${encodeURIComponent(chatId)}`,
  );
}

export async function deleteMemory(chatId: string, factKey: string) {
  return fetchJson<{ success: boolean }>("/api/memory/delete", {
    method: "POST",
    body: JSON.stringify({ chat_id: chatId, fact_key: factKey }),
  });
}

export async function getLearnings() {
  return fetchJson<{ items: LearningItem[] }>("/api/learnings");
}

export async function reloadPersona() {
  return fetchJson<{ success: boolean }>("/api/reload", {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export async function evolvePrompt() {
  return fetchJson<{ success: boolean; changes_summary?: string; error?: string }>(
    "/api/evolve",
    {
      method: "POST",
      body: JSON.stringify({}),
    },
  );
}
