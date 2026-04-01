import { useEffect, useRef, useState, useCallback } from "react";
import { API_BASE } from "../api";
import type { ChatMessage, ToolStatusInfo } from "../api";

type WsStatus = "connecting" | "connected" | "disconnected";

function getWsUrl(): string {
  // Read server URL from localStorage first, then fall back to API_BASE
  const stored = localStorage.getItem("lapwing_server_url");
  const base = stored || API_BASE || "http://127.0.0.1:8765";
  // Transform http → ws, https → wss
  return base.replace(/^http/, "ws") + "/ws/chat";
}

function getAuthToken(): string {
  return localStorage.getItem("lapwing_desktop_token") ?? "";
}

export function useWebSocket() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [status, setStatus] = useState<WsStatus>("disconnected");
  const [toolStatus, setToolStatus] = useState<ToolStatusInfo | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const retryDelay = useRef(1000);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const token = getAuthToken();
    const url = getWsUrl() + (token ? `?token=${encodeURIComponent(token)}` : "");

    setStatus("connecting");
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      setStatus("connected");
      retryDelay.current = 1000; // Reset backoff
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data as string);

        if (msg.type === "reply" || msg.type === "message") {
          const chatMsg: ChatMessage = {
            id: msg.id ?? crypto.randomUUID(),
            role: "assistant",
            content: msg.content ?? "",
            timestamp: msg.timestamp ?? new Date().toISOString(),
            toolCalls: msg.tool_calls,
          };
          setMessages(prev => {
            const updated = [...prev, chatMsg];
            return updated.length > 500 ? updated.slice(-500) : updated;
          });
          setToolStatus(null); // Clear tool status when reply arrives
        } else if (msg.type === "interim") {
          // Streaming interim response - update last assistant message or add new
          setMessages(prev => {
            const last = prev[prev.length - 1];
            if (last?.role === "assistant" && last.id === msg.id) {
              return [...prev.slice(0, -1), { ...last, content: msg.content ?? "" }];
            }
            return [...prev, {
              id: msg.id ?? crypto.randomUUID(),
              role: "assistant" as const,
              content: msg.content ?? "",
              timestamp: new Date().toISOString(),
            }];
          });
        } else if (msg.type === "status") {
          setToolStatus({
            phase: msg.phase ?? "executing",
            text: msg.text ?? "",
            toolName: msg.tool_name,
          });
        } else if (msg.type === "typing") {
          setToolStatus({ phase: "thinking", text: "思考中…" });
        } else if (msg.type === "pong") {
          // heartbeat response, no action needed
        } else if (msg.type === "error") {
          const errMsg: ChatMessage = {
            id: crypto.randomUUID(),
            role: "system" as const,
            content: `错误: ${msg.message ?? "未知错误"}`,
            timestamp: new Date().toISOString(),
          };
          setMessages(prev => [...prev, errMsg]);
        }
      } catch {
        // Non-JSON message, ignore
      }
    };

    ws.onerror = () => {
      setStatus("disconnected");
    };

    ws.onclose = () => {
      setStatus("disconnected");
      wsRef.current = null;
      // Exponential backoff reconnect
      const delay = retryDelay.current;
      retryDelay.current = Math.min(delay * 2, 30_000);
      retryRef.current = setTimeout(connect, delay);
    };
  }, []);

  const send = useCallback((content: string) => {
    if (wsRef.current?.readyState !== WebSocket.OPEN) return;
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
      wsRef.current?.close();
    };
  }, [connect]);

  return { messages, status, toolStatus, send, reconnect };
}
