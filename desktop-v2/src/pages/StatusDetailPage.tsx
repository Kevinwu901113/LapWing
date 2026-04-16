import { useStatusStore } from "@/stores/status";
import { useTasksStore } from "@/stores/tasks";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Activity, Clock, Users, ListTodo } from "lucide-react";

const STATUS_CONFIG = {
  idle: { color: "bg-green-400", label: "Idle" },
  thinking: { color: "bg-yellow-400 animate-pulse", label: "Thinking" },
  working: { color: "bg-blue-400 animate-pulse", label: "Working" },
  browsing: { color: "bg-purple-400 animate-pulse", label: "Browsing" },
} as const;

function formatTime(ts: string | null): string {
  if (!ts) return "—";
  try {
    return new Date(ts).toLocaleString("zh-CN", {
      month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return ts;
  }
}

export default function StatusDetailPage() {
  const status = useStatusStore((s) => s.status);
  const tasks = useTasksStore((s) => s.tasks);
  const config = STATUS_CONFIG[status.state] ?? STATUS_CONFIG.idle;

  const currentTask = status.current_task_id ? tasks.get(status.current_task_id) : null;

  return (
    <div className="h-full flex flex-col">
      <div className="p-4 border-b border-surface-border">
        <h1 className="text-lg font-medium text-text-accent">Status</h1>
      </div>

      <ScrollArea className="flex-1">
        <div className="p-4 space-y-4">
          {/* Current State */}
          <div className="bg-surface border border-surface-border rounded-lg p-4">
            <div className="flex items-center gap-3">
              <span className={`w-4 h-4 rounded-full ${config.color}`} />
              <span className="text-xl font-medium text-text-accent">{config.label}</span>
            </div>
          </div>

          {/* Current Task */}
          {(currentTask || status.current_task_request) && (
            <div className="bg-surface border border-surface-border rounded-lg p-4">
              <div className="flex items-center gap-2 text-text-muted text-xs mb-2">
                <ListTodo size={12} /> Current Task
              </div>
              <div className="text-sm text-text-primary">
                {currentTask?.title ?? status.current_task_request ?? "—"}
              </div>
              {currentTask && (
                <div className="mt-2 flex items-center gap-2 text-xs">
                  {currentTask.agent_name && (
                    <span className="text-text-secondary">{currentTask.agent_name}</span>
                  )}
                  <span className="text-text-muted">{currentTask.status}</span>
                </div>
              )}
            </div>
          )}

          {/* Last Interaction */}
          <div className="bg-surface border border-surface-border rounded-lg p-4">
            <div className="flex items-center gap-2 text-text-muted text-xs mb-2">
              <Clock size={12} /> Last Interaction
            </div>
            <div className="text-sm text-text-primary">
              {formatTime(status.last_interaction)}
            </div>
          </div>

          {/* Heartbeat */}
          {status.heartbeat_next && (
            <div className="bg-surface border border-surface-border rounded-lg p-4">
              <div className="flex items-center gap-2 text-text-muted text-xs mb-2">
                <Activity size={12} /> Next Heartbeat
              </div>
              <div className="text-sm text-text-primary">
                {formatTime(status.heartbeat_next)}
              </div>
            </div>
          )}

          {/* Active Agents */}
          {status.active_agents.length > 0 && (
            <div className="bg-surface border border-surface-border rounded-lg p-4">
              <div className="flex items-center gap-2 text-text-muted text-xs mb-2">
                <Users size={12} /> Active Agents
              </div>
              <div className="flex flex-wrap gap-2">
                {status.active_agents.map((agent) => (
                  <span
                    key={agent}
                    className="px-2 py-1 bg-void-50 border border-surface-border rounded text-xs text-text-primary"
                  >
                    {agent}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}
