import { useEffect, useState } from "react";
import {
  getChats, getTasks, getTask,
  type ChatSummary, type TaskSummary, type TaskDetail,
} from "../api";
import DataCard from "../components/DataCard";
import EmptyState from "../components/EmptyState";

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

      <div className="two-col">
        <DataCard title={`任务列表 (${tasks.length})`} className="stagger-1">
          {tasks.length === 0 ? (
            <EmptyState message="暂无任务记录。" />
          ) : (
            <div className="list-stack">
              {tasks.map((task) => (
                <div
                  key={task.task_id}
                  className={`task-row ${selectedId === task.task_id ? "task-row--active" : ""}`}
                  onClick={() => setSelectedId(task.task_id)}
                >
                  <p className="task-row-id">{task.task_id}</p>
                  <p className="task-row-text">{task.text || "（无文本）"}</p>
                  <span className="list-row-muted">
                    {task.status} · {formatDate(task.updated_at ?? null)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </DataCard>

        <DataCard title="任务详情" className="stagger-2">
          {!detail ? (
            <EmptyState message="选择左侧任务查看详情。" />
          ) : (
            <div className="task-detail">
              <div className="task-detail-header">
                <strong>{detail.task_id}</strong>
                <span className={`task-status task-status--${detail.status}`}>
                  {detail.status}
                </span>
              </div>
              <pre className="task-detail-events">
                {JSON.stringify(detail.events, null, 2)}
              </pre>
            </div>
          )}
        </DataCard>
      </div>
    </div>
  );
}
