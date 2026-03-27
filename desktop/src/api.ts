const DEFAULT_API_BASE =
  typeof window !== "undefined" && ["http:", "https:"].includes(window.location.protocol)
    ? ""
    : "http://127.0.0.1:8765";

export const API_BASE = (import.meta.env.VITE_LAPWING_API_BASE as string | undefined) ?? DEFAULT_API_BASE;
const REQUEST_CREDENTIALS: RequestCredentials = API_BASE ? "include" : "same-origin";

export type StatusResponse = {
  online: boolean;
  started_at: string;
  chat_count: number;
  last_interaction: string | null;
  latency_monitor?: {
    backend?: {
      tool_loop?: {
        shell_local?: {
          p95_ms?: number | null;
          samples?: number;
          threshold_ms?: number;
          slo_exceeded?: boolean;
          no_data?: boolean;
          enough_samples?: boolean;
          last_updated?: string | null;
          long_command_cutoff_ms?: number;
          long_command_excluded?: number;
        };
        web_search?: {
          p95_ms?: number | null;
          samples?: number;
          threshold_ms?: number;
          slo_exceeded?: boolean;
          no_data?: boolean;
          enough_samples?: boolean;
          last_updated?: string | null;
        };
      };
      event_pipeline?: {
        publish_to_sse?: {
          p95_ms?: number | null;
          samples?: number;
          threshold_ms?: number;
          slo_exceeded?: boolean;
          no_data?: boolean;
          enough_samples?: boolean;
          last_updated?: string | null;
        };
        update_throttle_ms?: number;
      };
    };
    frontend?: {
      tool_execution_start_to_ui?: {
        p95_ms?: number | null;
        samples?: number;
        threshold_ms?: number;
        slo_exceeded?: boolean;
        no_data?: boolean;
        enough_samples?: boolean;
        last_updated?: string | null;
      };
    };
    last_updated?: string | null;
  } | null;
};

export type AuthProfileSummary = {
  profileId: string;
  provider: string;
  type: string;
  expiresAt: string | null;
  status: string;
  reasonCode: string | null;
};

export type AuthStatusResponse = {
  profiles: AuthProfileSummary[];
  bindings: Record<string, string>;
  routes?: Record<
    string,
    {
      provider?: string | null;
      baseUrl: string;
      model: string;
      apiType: string;
      source: string;
      bindingPurpose?: string | null;
      bindingProfileId?: string | null;
      bindingProvider?: string | null;
      bindingMismatch?: boolean;
    }
  >;
  serviceAuth: {
    protected: boolean;
    host: string;
    cookieName: string;
  };
};

export type OAuthLoginSession = {
  loginId: string;
  provider: string;
  status: "pending" | "completing" | "completed" | "failed" | "expired";
  authorizeUrl: string;
  profileIdHint?: string | null;
  resolvedProfileId?: string | null;
  error?: string | null;
  createdAt: string;
  updatedAt: string;
  completionMessage?: string | null;
  profile?: AuthProfileSummary | null;
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
    chat_id?: string;
    text?: string;
    task_id?: string;
    phase?: string;
    tool_name?: string;
    round?: number;
    command?: string;
    reason?: string;
    topic?: string;
    toolCallId?: string;
    toolName?: string;
    argsHash?: string;
    stdoutBytes?: number;
    stderrBytes?: number;
    isError?: boolean;
    durationMs?: number;
    turn_tool_index?: number;
    turn_tool_total?: number;
  };
};

export type TaskSummary = {
  task_id: string;
  chat_id: string;
  status: string;
  phase: string;
  text: string;
  tool_name?: string | null;
  round?: number | null;
  command?: string | null;
  reason?: string | null;
  started_at?: string | null;
  updated_at?: string | null;
  completed_at?: string | null;
  failed_at?: string | null;
  blocked_at?: string | null;
};

export type TaskEventItem = {
  type: string;
  timestamp: string;
  phase: string;
  text: string;
  tool_name?: string | null;
  round?: number | null;
  command?: string | null;
  reason?: string | null;
  toolCallId?: string | null;
  toolName?: string | null;
  argsHash?: string | null;
  stdoutBytes?: number | null;
  stderrBytes?: number | null;
  isError?: boolean | null;
  durationMs?: number | null;
  turn_tool_index?: number | null;
  turn_tool_total?: number | null;
};

export type TaskDetail = TaskSummary & {
  events: TaskEventItem[];
};

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
    },
    credentials: REQUEST_CREDENTIALS,
    ...init,
  });

  if (!response.ok) {
    let detail = `Request failed: ${response.status}`;
    try {
      const payload = (await response.json()) as { detail?: string };
      if (typeof payload.detail === "string" && payload.detail.trim().length > 0) {
        detail = payload.detail;
      }
    } catch {
      // ignore non-json error payload
    }
    throw new Error(detail);
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

export async function getTasks(chatId?: string, status?: string, limit = 20) {
  const params = new URLSearchParams();
  if (chatId) {
    params.set("chat_id", chatId);
  }
  if (status) {
    params.set("status", status);
  }
  params.set("limit", String(limit));
  return fetchJson<{ items: TaskSummary[] }>(`/api/tasks?${params.toString()}`);
}

export async function getTask(taskId: string) {
  return fetchJson<TaskDetail>(`/api/tasks/${encodeURIComponent(taskId)}`);
}

export async function postLatencyTelemetry(payload: {
  metric: string;
  samples_ms: number[];
  client_timestamp?: string;
}) {
  return fetchJson<{ success: boolean; accepted_samples: number; metric: string; reason?: string }>(
    "/api/telemetry/latency",
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
  );
}

export async function createApiSession(bootstrapToken: string) {
  return fetchJson<{ success: boolean }>("/api/auth/session", {
    method: "POST",
    body: JSON.stringify({ bootstrap_token: bootstrapToken }),
  });
}

export function getAuthStatus() {
  return fetchJson<AuthStatusResponse>("/api/auth/status");
}

export async function importCodexCache(path?: string, profileId?: string) {
  return fetchJson<{ success: boolean; profile_id: string }>(
    "/api/auth/import/codex-cache",
    {
      method: "POST",
      body: JSON.stringify({
        path,
        profile_id: profileId,
      }),
    },
  );
}

export async function startOpenAICodexOAuth(returnTo?: string, profileId?: string) {
  return fetchJson<OAuthLoginSession>("/api/auth/oauth/openai-codex/start", {
    method: "POST",
    body: JSON.stringify({
      return_to: returnTo,
      profile_id: profileId,
    }),
  });
}

export function getOAuthLoginSession(loginId: string) {
  return fetchJson<OAuthLoginSession>(`/api/auth/oauth/sessions/${encodeURIComponent(loginId)}`);
}
