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

function TypingIndicator() {
  return (
    <div className="flex items-center gap-2 pl-10">
      <div className="flex items-center gap-1 px-3 py-2 rounded-lg bg-surface border border-surface-border">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="block w-1.5 h-1.5 rounded-full bg-text-muted animate-bounce"
            style={{ animationDelay: `${i * 0.15}s`, animationDuration: "1.2s" }}
          />
        ))}
      </div>
    </div>
  );
}

export function MessageList() {
  const messages = useChatStore((s) => s.messages);
  const toolStatus = useChatStore((s) => s.toolStatus);
  const lapwingStatus = useChatStore((s) => s.lapwingStatus);
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
  }, [messages, toolStatus, lapwingStatus, agentActivities]);

  const isThinking = lapwingStatus === "thinking" && !toolStatus;

  return (
    <div
      ref={scrollContainerRef}
      onScroll={handleScroll}
      className="flex-1 overflow-y-auto px-4"
    >
      <div className="py-4 space-y-3">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full pt-24 text-text-muted">
            <div className="w-12 h-12 rounded-full bg-gradient-to-br from-lapwing-light to-lapwing-dark flex items-center justify-center text-void text-lg font-bold mb-3">
              L
            </div>
            <span className="text-sm">开始和 Lapwing 聊天吧</span>
          </div>
        )}
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
        {isThinking && <TypingIndicator />}
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
