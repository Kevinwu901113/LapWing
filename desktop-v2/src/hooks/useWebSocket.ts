import { useEffect, useRef, useCallback } from "react";
import { getApiBase } from "@/lib/api";
import { useChatStore } from "@/stores/chat";

function getWsUrl(): string {
  const base = getApiBase() || "http://127.0.0.1:8765";
  return base.replace(/^http/, "ws") + "/ws/chat";
}

function getAuthToken(): string {
  return localStorage.getItem("lapwing_desktop_token") ?? "";
}

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null);
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const retryDelay = useRef(1000);
  const interimIdRef = useRef<string | null>(null);
  const pingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const {
    addMessage, updateInterim, setWsStatus, setToolStatus,
    addToolCall, completeToolCall, upsertAgentActivity,
    setIsStreaming, setLapwingStatus, clearToolCalls,
  } = useChatStore.getState();

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const token = getAuthToken();
    const url = getWsUrl() + (token ? `?token=${encodeURIComponent(token)}` : "");

    setWsStatus("connecting");
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      setWsStatus("connected");
      retryDelay.current = 1000;
      // Heartbeat ping every 30s
      pingRef.current = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "ping" }));
        }
      }, 30_000);
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data as string);

        if (msg.type === "reply" || msg.type === "message") {
          const isFinalSignal = msg.final === true && interimIdRef.current !== null;
          if (!isFinalSignal) {
            addMessage({
              id: msg.id ?? crypto.randomUUID(),
              role: "assistant",
              content: msg.content ?? "",
              timestamp: msg.timestamp ?? new Date().toISOString(),
              tool_calls: msg.tool_calls,
            });
          }
          setToolStatus(null);
          setIsStreaming(false);
          setLapwingStatus("idle");
          clearToolCalls();
          interimIdRef.current = null;
        } else if (msg.type === "interim") {
          const interimId = interimIdRef.current ?? (interimIdRef.current = crypto.randomUUID());
          updateInterim(interimId, msg.content ?? "");
        } else if (msg.type === "status") {
          setToolStatus({
            phase: msg.phase ?? "executing",
            text: msg.text ?? "",
            toolName: msg.tool_name,
          });
        } else if (msg.type === "typing") {
          setToolStatus({ phase: "thinking", text: "思考中..." });
          setLapwingStatus("thinking");
        } else if (msg.type === "tool_call") {
          addToolCall({
            id: msg.call_id ?? crypto.randomUUID(),
            name: msg.name,
            arguments: msg.arguments ?? {},
            startedAt: Date.now(),
          });
          setLapwingStatus("using_tool");
          setToolStatus({
            phase: "executing",
            text: msg.name,
            toolName: msg.name,
          });
        } else if (msg.type === "tool_result") {
          completeToolCall(msg.call_id ?? "", msg.result_preview ?? "", msg.success ?? true);
        } else if (msg.type === "agent_emit") {
          upsertAgentActivity({
            commandId: msg.ref_id ?? msg.command_id ?? "",
            agentName: msg.agent_name,
            state: msg.state,
            progress: msg.progress ?? null,
            note: msg.note ?? null,
            headline: null,
            startedAt: Date.now(),
          });
          setLapwingStatus("delegating");
        } else if (msg.type === "agent_notify") {
          upsertAgentActivity({
            commandId: msg.ref_command_id ?? "",
            agentName: msg.agent_name,
            state: msg.kind === "error" ? "failed" : "done",
            progress: 1,
            note: null,
            headline: msg.headline ?? null,
            startedAt: Date.now(),
          });
        } else if (msg.type === "error") {
          addMessage({
            id: crypto.randomUUID(),
            role: "system",
            content: `错误: ${msg.message ?? "未知错误"}`,
            timestamp: new Date().toISOString(),
          });
        }
        // pong: no action
      } catch {
        // Non-JSON, ignore
      }
    };

    ws.onerror = () => setWsStatus("disconnected");

    ws.onclose = () => {
      setWsStatus("disconnected");
      wsRef.current = null;
      if (pingRef.current) clearInterval(pingRef.current);
      const delay = retryDelay.current;
      retryDelay.current = Math.min(delay * 2, 30_000);
      retryRef.current = setTimeout(connect, delay);
    };
  }, [addMessage, updateInterim, setWsStatus, setToolStatus, addToolCall, completeToolCall, upsertAgentActivity, setIsStreaming, setLapwingStatus, clearToolCalls]);

  const send = useCallback((content: string) => {
    if (wsRef.current?.readyState !== WebSocket.OPEN) return;
    // Add user message locally
    useChatStore.getState().addMessage({
      id: crypto.randomUUID(),
      role: "user",
      content,
      timestamp: new Date().toISOString(),
    });
    wsRef.current.send(JSON.stringify({ type: "message", content }));
  }, []);

  const reconnect = useCallback(() => {
    if (retryRef.current) clearTimeout(retryRef.current);
    retryDelay.current = 1000;
    wsRef.current?.close();
    connect();
  }, [connect]);

  useEffect(() => {
    connect();
    return () => {
      if (retryRef.current) clearTimeout(retryRef.current);
      if (pingRef.current) clearInterval(pingRef.current);
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [connect]);

  return { send, reconnect };
}
