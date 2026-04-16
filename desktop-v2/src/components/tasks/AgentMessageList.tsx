import { useEffect } from "react";
import { useTasksStore } from "@/stores/tasks";
import type { AgentMessage } from "@/types/tasks-v2";
import { Wrench } from "lucide-react";

function MessageRow({ msg }: { msg: AgentMessage }) {
  const isToolCall = msg.event_type === "agent.tool_called";

  return (
    <div className="flex gap-2 py-1.5">
      <div className="w-px bg-surface-border shrink-0 ml-2" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          {isToolCall && <Wrench size={10} className="text-yellow-400 shrink-0" />}
          <span className="text-[11px] font-medium text-text-accent truncate">
            {msg.actor}
            {isToolCall && msg.tool_name ? ` calls ${msg.tool_name}` : ""}
          </span>
          <span className="text-[10px] text-text-muted ml-auto shrink-0">
            {formatTime(msg.timestamp)}
          </span>
        </div>
        {msg.content && (
          <p className="text-xs text-text-secondary mt-0.5 line-clamp-3 break-words">
            {msg.content}
          </p>
        )}
      </div>
    </div>
  );
}

function formatTime(ts: string): string {
  try {
    return new Date(ts).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

export function AgentMessageList({ taskId }: { taskId: string }) {
  const messages = useTasksStore((s) => s.agentMessages.get(taskId));
  const loadTaskMessages = useTasksStore((s) => s.loadTaskMessages);

  useEffect(() => {
    if (!messages) loadTaskMessages(taskId);
  }, [taskId, messages, loadTaskMessages]);

  if (!messages || messages.length === 0) {
    return (
      <div className="text-[11px] text-text-muted py-2 pl-4">
        loading...
      </div>
    );
  }

  return (
    <div className="pl-1">
      {messages.map((msg) => (
        <MessageRow key={msg.event_id} msg={msg} />
      ))}
    </div>
  );
}
