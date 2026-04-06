import { useEffect, useState, useCallback } from "react";
import {
  getScheduledTasks,
  deleteScheduledTask,
  getTaskFlows,
  cancelTaskFlow,
  type ScheduledTask,
  type TaskFlowItem,
} from "../api";
import TaskItem from "../components/TaskItem";
import TaskFlowCard from "../components/TaskFlowCard";

type Filter = "all" | "pending" | "completed";
type Tab = "scheduled" | "flows";

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
  const [tab, setTab] = useState<Tab>("scheduled");

  // ── 定时任务 ──────────────────────────────────────────────────────────────
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [filter, setFilter] = useState<Filter>("all");

  const loadTasks = useCallback(() => {
    void getScheduledTasks().then((r) => setTasks(r.tasks));
  }, []);

  useEffect(() => {
    if (tab !== "scheduled") return;
    loadTasks();
    const id = setInterval(loadTasks, 30_000);
    return () => clearInterval(id);
  }, [tab, loadTasks]);

  const handleDelete = useCallback(
    (id: string) => void deleteScheduledTask(id).then(() => loadTasks()),
    [loadTasks]
  );

  // ── 任务流 ────────────────────────────────────────────────────────────────
  const [flows, setFlows] = useState<TaskFlowItem[]>([]);

  const loadFlows = useCallback(() => {
    void getTaskFlows().then((r) => setFlows(r.flows));
  }, []);

  useEffect(() => {
    if (tab !== "flows") return;
    loadFlows();
    const id = setInterval(loadFlows, 10_000);
    return () => clearInterval(id);
  }, [tab, loadFlows]);

  const handleCancel = useCallback(
    (flowId: string) => void cancelTaskFlow(flowId).then(() => loadFlows()),
    [loadFlows]
  );

  // ── 汇总徽章 ─────────────────────────────────────────────────────────────
  const activeScheduled = tasks.filter(
    (t) => t.status === "pending" || t.status === "running"
  ).length;
  const activeFlows = flows.filter(
    (f) => f.status === "running" || f.status === "pending"
  ).length;

  const visible = tasks.filter((t) => matchesFilter(t, filter));

  return (
    <div className="tab-page animate-in">
      <header className="page-header">
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <h1 className="page-title">任务</h1>
          {(activeScheduled + activeFlows) > 0 && (
            <span className="badge badge-warning">{activeScheduled + activeFlows}</span>
          )}
        </div>
      </header>

      {/* Tab switcher */}
      <div style={{ display: "flex", gap: 4, marginBottom: 16 }}>
        <button
          className={`btn btn-sm${tab === "scheduled" ? " btn-primary" : ""}`}
          onClick={() => setTab("scheduled")}
        >
          定时任务{activeScheduled > 0 ? ` (${activeScheduled})` : ""}
        </button>
        <button
          className={`btn btn-sm${tab === "flows" ? " btn-primary" : ""}`}
          onClick={() => setTab("flows")}
        >
          任务流{activeFlows > 0 ? ` (${activeFlows})` : ""}
        </button>
      </div>

      {/* 定时任务 */}
      {tab === "scheduled" && (
        <>
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
              <p className="empty-hint">暂无定时任务</p>
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
        </>
      )}

      {/* 任务流 */}
      {tab === "flows" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {flows.length === 0 ? (
            <div className="card">
              <p className="empty-hint">暂无任务流</p>
            </div>
          ) : (
            flows.map((flow) => (
              <TaskFlowCard key={flow.flow_id} flow={flow} onCancel={handleCancel} />
            ))
          )}
        </div>
      )}
    </div>
  );
}
