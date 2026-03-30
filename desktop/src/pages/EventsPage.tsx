import { useEffect, useRef, useState } from "react";
import { API_BASE, type DesktopEvent } from "../api";
import DataCard from "../components/DataCard";
import StatusDot from "../components/StatusDot";
import EventBadge from "../components/EventBadge";
import EmptyState from "../components/EmptyState";

function formatDate(v: string) {
  return new Date(v).toLocaleString("zh-CN");
}

export default function EventsPage() {
  const [events, setEvents] = useState<DesktopEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const streamRef = useRef<EventSource | null>(null);

  useEffect(() => {
    const stream = new EventSource(`${API_BASE}/api/events/stream`, {
      withCredentials: API_BASE.length > 0,
    });
    streamRef.current = stream;

    stream.onopen = () => setConnected(true);
    stream.onerror = () => setConnected(false);
    stream.onmessage = (msg) => {
      const event = JSON.parse(msg.data) as DesktopEvent;
      setEvents((prev) => [event, ...prev].slice(0, 50));
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

      <DataCard title={`最近事件 (${events.length})`} className="stagger-1">
        {events.length === 0 ? (
          <EmptyState message="等待来自 SSE 的事件…" />
        ) : (
          <div className="list-stack">
            {events.map((event, i) => (
              <div key={`${event.timestamp}-${i}`} className="event-row">
                <EventBadge type={event.type} />
                <p className="event-row-text">{event.payload.text ?? "（无文本）"}</p>
                <span className="list-row-muted">
                  {event.payload.chat_id ?? "unknown"} · {formatDate(event.timestamp)}
                  {event.payload.task_id ? ` · ${event.payload.task_id}` : ""}
                  {event.payload.tool_name ? ` · ${event.payload.tool_name}` : ""}
                </span>
              </div>
            ))}
          </div>
        )}
      </DataCard>
    </div>
  );
}
