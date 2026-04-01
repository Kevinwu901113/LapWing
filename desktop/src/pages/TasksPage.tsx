import { useEffect, useState } from "react";
import {
  getChats, getTasks, getTask,
  type ChatSummary, type TaskSummary, type TaskDetail,
} from "../api";

function formatDate(v: string | null) {
  return v ? new Date(v).toLocaleString("zh-CN") : "—";
}

export default function TasksPage() {
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [chatId, setChatId] = useState("");
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<TaskDetail | null>(null);

  useEffect(() => {
    void getChats().then((c) => {
      setChats(c);
      if (c.length > 0 && !chatId) setChatId(c[0].chat_id);
    });
  }, []);

  useEffect(() => {
    if (!chatId) return;
    void getTasks(chatId, undefined, 20).then((r) => {
      setTasks(r.items);
      if (r.items.length > 0) setSelectedId(r.items[0].task_id);
    });
  }, [chatId]);

  useEffect(() => {
    if (!selectedId) { setDetail(null); return; }
    void getTask(selectedId).then(setDetail);
  }, [selectedId]);

  return (
    <div className="page">
      <header className="page-header animate-in">
        <div>
          <h1 className="page-title">任务</h1>
          <p className="page-subtitle">Agent 团队的任务执行记录</p>
        </div>
        <select
          className="chat-selector"
          value={chatId}
          onChange={(e) => setChatId(e.target.value)}
        >
          {chats.map((c) => (
            <option key={c.chat_id} value={c.chat_id}>{c.chat_id}</option>
          ))}
        </select>
      </header>

      <div className="stat-grid-2">
        <div className="card">
          <p className="card-title">任务列表 ({tasks.length})</p>
          {tasks.length === 0 ? (
            <p className="empty-hint">暂无任务记录。</p>
          ) : (
            <div>
              {tasks.map((task) => (
                <div
                  key={task.task_id}
                  onClick={() => setSelectedId(task.task_id)}
                  style={{
                    padding: "8px 0",
                    borderBottom: "1px solid var(--border)",
                    cursor: "pointer",
                    background: selectedId === task.task_id ? "var(--bg-hover, rgba(255,255,255,0.05))" : "transparent",
                  }}
                >
                  <p style={{ margin: 0, fontSize: 12, color: "var(--text-muted)" }}>{task.task_id}</p>
                  <p style={{ margin: "2px 0", fontSize: 13, color: "var(--text-primary)" }}>{task.text || "（无文本）"}</p>
                  <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                    {task.status} · {formatDate(task.updated_at ?? null)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="card">
          <p className="card-title">任务详情</p>
          {!detail ? (
            <p className="empty-hint">选择左侧任务查看详情。</p>
          ) : (
            <div>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
                <strong style={{ fontSize: 13, color: "var(--text-primary)" }}>{detail.task_id}</strong>
                <span className="badge badge-accent">{detail.status}</span>
              </div>
              <pre style={{ margin: 0, fontSize: 11, color: "var(--text-secondary)", whiteSpace: "pre-wrap", overflowY: "auto", maxHeight: 400 }}>
                {JSON.stringify(detail.events, null, 2)}
              </pre>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
