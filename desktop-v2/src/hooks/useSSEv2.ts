import { useEffect, useRef, useCallback, useState } from "react";
import { getApiBase } from "@/lib/api";
import { useTasksStore } from "@/stores/tasks";
import { useStatusStore } from "@/stores/status";
import type { SSEEvent } from "@/types/events";

const RECONNECT_DELAY_MS = 3000;
const MAX_EVENTS = 200;

export function useSSEv2() {
  const [connected, setConnected] = useState(false);
  const [events, setEvents] = useState<SSEEvent[]>([]);
  const esRef = useRef<EventSource | null>(null);
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const unmountedRef = useRef(false);

  const dispatch = useCallback((event: SSEEvent) => {
    // Route events to appropriate stores
    const type = event.event_type;

    if (type.startsWith("agent.")) {
      const tasksStore = useTasksStore.getState();
      const payload = event.payload as Record<string, unknown>;
      // Step 4 M5 起 SSE 不再携带 top-level task_id/actor——consumer
      // 从 payload 读。Step 6 的 agent.* mutations 在 payload 里统一带上。
      const taskId = (payload.task_id as string | undefined) ?? event.task_id;

      if (type === "agent.task_queued" || type === "agent.task_started" ||
          type === "agent.task_done" || type === "agent.task_failed") {
        const status = type === "agent.task_queued" ? "queued"
          : type === "agent.task_started" ? "running"
          : type === "agent.task_done" ? "done"
          : "failed";
        tasksStore.upsertTask({
          task_id: taskId ?? "",
          parent_task_id: payload.parent_task_id as string | undefined,
          title: (payload.title as string) ?? (payload.request as string) ?? "",
          status,
          agent_name: payload.agent_name as string | undefined,
          created_at: event.timestamp,
          updated_at: event.timestamp,
        });
      }
      if (taskId && (type === "agent.message" || type === "agent.tool_called")) {
        tasksStore.addAgentMessage(taskId, {
          event_id: event.event_id,
          timestamp: event.timestamp,
          actor: (payload.actor as string) ?? event.actor ?? "unknown",
          content: (payload.content as string) ?? (payload.summary as string) ?? "",
          event_type: type,
          tool_name: payload.tool_name as string | undefined,
          tool_args: payload.tool_args as Record<string, unknown> | undefined,
        });
      }
    }

    if (type === "status.changed") {
      const statusStore = useStatusStore.getState();
      const payload = event.payload as Record<string, unknown>;
      if (payload.state) {
        statusStore.setState(payload.state as "idle" | "thinking" | "working" | "browsing");
      }
      if (payload.current_task_id !== undefined) {
        statusStore.setCurrentTask(
          payload.current_task_id as string | null,
          payload.current_task_request as string | null,
        );
      }
      if (payload.active_agents) {
        statusStore.setActiveAgents(payload.active_agents as string[]);
      }
    }
  }, []);

  const connect = useCallback(() => {
    if (unmountedRef.current) return;
    const token = localStorage.getItem("lapwing_desktop_token") ?? "";
    const base = getApiBase();
    // Token in query param (EventSource can't set custom headers).
    // Last-Event-ID is automatically sent by the browser on reconnect.
    const url = `${base}/api/v2/events?token=${encodeURIComponent(token)}`;

    const es = new EventSource(url);
    esRef.current = es;

    es.onopen = () => setConnected(true);

    es.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data as string) as SSEEvent;
        setEvents((prev) => {
          const next = [...prev, event];
          return next.length > MAX_EVENTS ? next.slice(-MAX_EVENTS) : next;
        });
        dispatch(event);
      } catch {
        // ignore parse errors
      }
    };

    es.onerror = () => {
      setConnected(false);
      es.close();
      esRef.current = null;
      if (!unmountedRef.current) {
        retryRef.current = setTimeout(connect, RECONNECT_DELAY_MS);
      }
    };
  }, [dispatch]);

  useEffect(() => {
    unmountedRef.current = false;
    connect();
    return () => {
      unmountedRef.current = true;
      if (retryRef.current) clearTimeout(retryRef.current);
      esRef.current?.close();
    };
  }, [connect]);

  return { events, connected };
}
