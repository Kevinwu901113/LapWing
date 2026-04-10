import type { ChatMessage } from "@/types/chat";

function formatTime(ts: string): string {
  try {
    return new Date(ts).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

export function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  const isSystem = message.role === "system";

  if (isSystem) {
    return (
      <div className="flex justify-center py-1">
        <span className="text-xs text-text-muted px-3 py-1 rounded bg-surface">
          {message.content}
        </span>
      </div>
    );
  }

  // Handle [SPLIT] in assistant messages
  const segments = isUser
    ? [message.content]
    : message.content.split("[SPLIT]").map((s) => s.trim()).filter(Boolean);

  return (
    <div className={`flex flex-col gap-1 ${isUser ? "items-end" : "items-start"}`}>
      {segments.map((segment, i) => (
        <div
          key={i}
          className={`flex gap-2 max-w-[75%] ${isUser ? "flex-row-reverse" : "flex-row"}`}
        >
          {!isUser && i === 0 && (
            <div className="w-8 h-8 rounded-full bg-gradient-to-br from-lapwing-light to-lapwing-dark flex items-center justify-center text-void text-xs font-bold shrink-0 mt-0.5">
              L
            </div>
          )}
          {!isUser && i > 0 && <div className="w-8 shrink-0" />}
          <div
            className={`px-3 py-2 rounded-lg text-sm leading-relaxed whitespace-pre-wrap break-words ${
              isUser
                ? "bg-lapwing-muted border border-lapwing-border text-text-primary"
                : "bg-surface border border-surface-border text-text-primary"
            }`}
          >
            {segment}
          </div>
        </div>
      ))}
      <span className={`text-[11px] text-text-muted ${isUser ? "pr-1" : "pl-10"}`}>
        {formatTime(message.timestamp)}
      </span>
    </div>
  );
}
