import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { TaskV2 } from "@/types/tasks-v2";
import { AgentMessageList } from "./AgentMessageList";

const STATUS_STYLE: Record<string, { dot: string; text: string }> = {
  queued: { dot: "bg-gray-400", text: "text-text-muted" },
  running: { dot: "bg-blue-400 animate-pulse", text: "text-blue-400" },
  done: { dot: "bg-green-400", text: "text-green-400" },
  failed: { dot: "bg-red-400", text: "text-red-400" },
  cancelled: { dot: "bg-gray-500", text: "text-gray-500" },
};

function timeAgo(ts: string): string {
  try {
    const diff = Date.now() - new Date(ts).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    return `${Math.floor(hours / 24)}d ago`;
  } catch {
    return "";
  }
}

export function TaskCard({ task }: { task: TaskV2 }) {
  const [expanded, setExpanded] = useState(false);
  const style = STATUS_STYLE[task.status] ?? STATUS_STYLE.queued;

  return (
    <div className="bg-surface border border-surface-border rounded-md">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 w-full text-left px-2.5 py-2 hover:bg-surface-hover rounded-md transition-colors"
      >
        {expanded ? (
          <ChevronDown size={12} className="text-text-muted shrink-0" />
        ) : (
          <ChevronRight size={12} className="text-text-muted shrink-0" />
        )}
        <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${style.dot}`} />
        <span className="text-xs text-text-primary truncate flex-1">{task.title}</span>
        <span className="text-[10px] text-text-muted shrink-0">
          {timeAgo(task.updated_at ?? task.created_at)}
        </span>
      </button>

      {expanded && (
        <div className="px-2.5 pb-2 border-t border-surface-border">
          <div className="flex items-center gap-2 py-1.5 text-[11px]">
            {task.agent_name && (
              <span className="text-text-secondary">{task.agent_name}</span>
            )}
            <span className={style.text}>{task.status}</span>
          </div>
          <AgentMessageList taskId={task.task_id} />
        </div>
      )}
    </div>
  );
}
