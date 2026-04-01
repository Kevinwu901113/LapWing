import { useEffect, useState, useCallback } from "react";
import {
  getScheduledTasks,
  deleteScheduledTask,
  type ScheduledTask,
} from "../api";
import TaskItem from "../components/TaskItem";

type Filter = "all" | "pending" | "completed";

const FILTER_LABELS: Record<Filter, string> = {
  all: "全部",
  pending: "待执行",
  completed: "已完成",
};

function matchesFilter(task: ScheduledTask, filter: Filter): boolean {
  if (filter === "all") return true;
  if (filter === "pending") return task.status === "pending" || task.status === "running";
  return task.status === "completed";
}

export default function TasksPage() {
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [filter, setFilter] = useState<Filter>("all");

  const load = useCallback(() => {
    void getScheduledTasks().then((r) => setTasks(r.tasks));
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 30_000);
    return () => clearInterval(id);
  }, [load]);

  const handleDelete = useCallback(
    (id: string) => {
      void deleteScheduledTask(id).then(() => load());
    },
    [load]
  );

  const activeCount = tasks.filter(
    (t) => t.status === "pending" || t.status === "running"
  ).length;

  const visible = tasks.filter((t) => matchesFilter(t, filter));

  return (
    <div className="tab-page animate-in">
      <header className="page-header">
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <h1 className="page-title">任务</h1>
          {activeCount > 0 && (
            <span className="badge badge-warning">{activeCount}</span>
          )}
        </div>
      </header>

      <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
        {(["all", "pending", "completed"] as Filter[]).map((f) => (
          <button
            key={f}
            className={`btn btn-sm${filter === f ? " btn-primary" : ""}`}
            onClick={() => setFilter(f)}
          >
            {FILTER_LABELS[f]}
          </button>
        ))}
      </div>

      <div className="card">
        {visible.length === 0 ? (
          <p className="empty-hint">暂无任务</p>
        ) : (
          visible.map((task) => (
            <TaskItem
              key={task.id}
              id={task.id}
              title={task.title}
              status={task.status}
              scheduledAt={task.scheduled_at}
              recurrence={task.recurrence}
              onDelete={() => handleDelete(task.id)}
            />
          ))
        )}
      </div>
    </div>
  );
}
