import { useCallback, useEffect, useRef } from "react";
import { useChatStore } from "@/stores/chat";
import { MessageBubble } from "./MessageBubble";
import { ToolCallIndicator } from "./ToolCallIndicator";
import { AgentActivityCard } from "./AgentActivityCard";

function shouldShowTimeSeparator(prev: string | undefined, curr: string): boolean {
  if (!prev) return true;
  try {
    const diff = new Date(curr).getTime() - new Date(prev).getTime();
    return diff > 5 * 60 * 1000; // 5 minutes
  } catch {
    return false;
  }
}

function formatDateSeparator(ts: string): string {
  try {
    const d = new Date(ts);
    const today = new Date();
    if (d.toDateString() === today.toDateString()) {
      return d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
    }
    return d.toLocaleDateString("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

export function MessageList() {
  const messages = useChatStore((s) => s.messages);
  const toolStatus = useChatStore((s) => s.toolStatus);
  const agentActivities = useChatStore((s) => s.agentActivities);
  const bottomRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const isUserScrolledUp = useRef(false);

  const handleScroll = useCallback(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 100;
    isUserScrolledUp.current = !atBottom;
  }, []);

  useEffect(() => {
    if (!isUserScrolledUp.current) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, toolStatus, agentActivities]);

  return (
    <div
      ref={scrollContainerRef}
      onScroll={handleScroll}
      className="flex-1 overflow-y-auto px-4"
    >
      <div className="py-4 space-y-3">
        {messages.map((msg, i) => (
          <div key={msg.id}>
            {shouldShowTimeSeparator(messages[i - 1]?.timestamp, msg.timestamp) && (
              <div className="flex justify-center py-2">
                <span className="text-[11px] text-text-muted">
                  {formatDateSeparator(msg.timestamp)}
                </span>
              </div>
            )}
            <MessageBubble message={msg} />
          </div>
        ))}
        {toolStatus && (
          <div className="pl-10">
            <ToolCallIndicator status={toolStatus} />
          </div>
        )}
        {agentActivities.filter(a => a.state !== "done" && a.state !== "failed").map((activity) => (
          <div key={activity.commandId} className="pl-0">
            <AgentActivityCard activity={activity} />
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
