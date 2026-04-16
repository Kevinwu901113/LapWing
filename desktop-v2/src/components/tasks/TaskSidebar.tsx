import { useState } from "react";
import { ChevronLeft, ChevronRight, ListTodo } from "lucide-react";
import { useTasksStore } from "@/stores/tasks";
import { TaskCard } from "./TaskCard";
import type { TaskV2 } from "@/types/tasks-v2";

export function TaskSidebar() {
  const [collapsed, setCollapsed] = useState(false);
  const tasks = useTasksStore((s) => s.tasks);

  if (collapsed) {
    return (
      <button
        onClick={() => setCollapsed(false)}
        className="flex items-center justify-center w-8 h-full border-l border-surface-border bg-void-100 hover:bg-surface-hover"
        title="Show tasks"
      >
        <ChevronLeft size={14} className="text-text-muted" />
      </button>
    );
  }

  const allTasks = Array.from(tasks.values());
  const activeTasks = allTasks.filter(
    (t) => t.status === "queued" || t.status === "running"
  );
  const recentDone = allTasks
    .filter((t) => t.status === "done" || t.status === "failed")
    .sort((a, b) => (b.updated_at ?? b.created_at).localeCompare(a.updated_at ?? a.created_at))
    .slice(0, 5);

  return (
    <div className="w-[260px] shrink-0 border-l border-surface-border bg-void-100 flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-surface-border">
        <div className="flex items-center gap-1.5 text-sm font-medium text-text-accent">
          <ListTodo size={14} />
          <span>Tasks</span>
          {activeTasks.length > 0 && (
            <span className="text-xs text-blue-400">({activeTasks.length})</span>
          )}
        </div>
        <button
          onClick={() => setCollapsed(true)}
          className="p-1 hover:bg-surface-hover rounded"
          title="Collapse"
        >
          <ChevronRight size={12} className="text-text-muted" />
        </button>
      </div>

      {/* Task list */}
      <div className="flex-1 overflow-y-auto p-2 space-y-1.5">
        {activeTasks.length === 0 && recentDone.length === 0 && (
          <div className="text-xs text-text-muted text-center py-8">
            No active tasks
          </div>
        )}

        {activeTasks.length > 0 && (
          <>
            <div className="text-[11px] text-text-muted uppercase tracking-wider px-1 pb-1">
              Active
            </div>
            {activeTasks.map((t) => (
              <TaskCard key={t.task_id} task={t} />
            ))}
          </>
        )}

        {recentDone.length > 0 && (
          <>
            <div className="text-[11px] text-text-muted uppercase tracking-wider px-1 pt-2 pb-1">
              Recent
            </div>
            {recentDone.map((t) => (
              <TaskCard key={t.task_id} task={t} />
            ))}
          </>
        )}
      </div>
    </div>
  );
}
