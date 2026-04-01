import { useEffect, useRef, useState } from "react";
import { API_BASE, type DesktopEvent } from "../api";
import StatusDot from "../components/StatusDot";

const TOOL_UPDATE_THROTTLE_MS = 500;
const NOTIFIABLE_TYPES = ["interest_proactive", "proactive_message", "reminder_message"];

function formatDate(v: string) {
  return new Date(v).toLocaleString("zh-CN");
}

export default function EventsPage() {
  const [events, setEvents] = useState<DesktopEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const streamRef = useRef<EventSource | null>(null);
  const toolUpdateLastSeen = useRef<Record<string, number>>({});

  useEffect(() => {
    if ("Notification" in window && Notification.permission === "default") {
      void Notification.requestPermission();
    }

    const stream = new EventSource(`${API_BASE}/api/events/stream`, {
      withCredentials: API_BASE.length > 0,
    });
    streamRef.current = stream;

    stream.onopen = () => setConnected(true);
    stream.onerror = () => setConnected(false);
    stream.onmessage = (msg) => {
      const event = JSON.parse(msg.data) as DesktopEvent;

      if (event.type === "task.tool_execution_update") {
        const key = event.payload.toolCallId ?? "__global__";
        const now = Date.now();
        const last = toolUpdateLastSeen.current[key] ?? 0;
        if (now - last < TOOL_UPDATE_THROTTLE_MS) return;
        toolUpdateLastSeen.current[key] = now;
      }

      setEvents((prev) => [event, ...prev].slice(0, 50));

      if (
        NOTIFIABLE_TYPES.includes(event.type) &&
        "Notification" in window &&
        Notification.permission === "granted"
      ) {
        const title = event.type === "interest_proactive" ? "Lapwing 主动分享" : "Lapwing 主动消息";
        const suffix = event.payload.topic ? `\n主题：${event.payload.topic}` : "";
        new Notification(title, {
          body: `${event.payload.text ?? "收到新消息"}${suffix}`,
        });
      }
    };

    return () => { stream.close(); setConnected(false); };
  }, []);

  return (
    <div className="page">
      <header className="page-header animate-in">
        <div>
          <h1 className="page-title">事件流</h1>
          <p className="page-subtitle">来自后端的实时 SSE 事件</p>
        </div>
        <div className="page-header-actions">
          <div className="connection-pill">
            <StatusDot online={connected} />
            <span>{connected ? "已连接" : "未连接"}</span>
          </div>
        </div>
      </header>

      <div className="card">
        <p className="card-title">最近事件 ({events.length})</p>
        {events.length === 0 ? (
          <p className="empty-hint">等待来自 SSE 的事件…</p>
        ) : (
          <div>
            {events.map((event, i) => (
              <div key={`${event.timestamp}-${i}`} style={{ padding: "8px 0", borderBottom: "1px solid var(--border)" }}>
                <span className="badge badge-accent" style={{ fontSize: 11 }}>{event.type}</span>
                <p style={{ margin: "4px 0 2px", fontSize: 13, color: "var(--text-primary)" }}>{event.payload.text ?? "（无文本）"}</p>
                <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                  {event.payload.chat_id ?? "unknown"} · {formatDate(event.timestamp)}
                  {event.payload.task_id ? ` · ${event.payload.task_id}` : ""}
                  {event.payload.tool_name ? ` · ${event.payload.tool_name}` : ""}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
