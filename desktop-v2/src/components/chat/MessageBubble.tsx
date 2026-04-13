import { useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ChevronDown, ChevronRight, Wrench } from "lucide-react";
import type { ChatMessage, ToolCall } from "@/types/chat";

function formatTime(ts: string): string {
  try {
    return new Date(ts).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

function ToolCallChip({ toolCall }: { toolCall: ToolCall }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="rounded bg-void border border-surface-border text-xs">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1.5 px-2 py-1 w-full text-left text-text-secondary hover:text-text-primary"
      >
        <Wrench size={12} className="text-lapwing shrink-0" />
        <span className="truncate">{toolCall.name}</span>
        {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
      </button>
      {expanded && (
        <div className="px-2 pb-1.5 text-text-muted border-t border-surface-border">
          <pre className="overflow-x-auto mt-1 whitespace-pre-wrap">
            {JSON.stringify(toolCall.arguments, null, 2)}
          </pre>
          {toolCall.result && (
            <div className="mt-1 pt-1 border-t border-surface-border text-text-secondary">
              {toolCall.result.slice(0, 200)}{toolCall.result.length > 200 ? "..." : ""}
            </div>
          )}
        </div>
      )}
    </div>
  );
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
            className={`px-3 py-2 rounded-lg text-sm leading-relaxed break-words ${
              isUser
                ? "bg-lapwing-muted border border-lapwing-border text-text-primary whitespace-pre-wrap"
                : "bg-surface border border-surface-border text-text-primary"
            }`}
          >
            {isUser ? (
              segment
            ) : (
              <Markdown
                remarkPlugins={[remarkGfm]}
                components={{
                  p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
                  a: ({ href, children }) => (
                    <a href={href} className="text-lapwing hover:underline" target="_blank" rel="noopener noreferrer">
                      {children}
                    </a>
                  ),
                  pre: ({ children }) => (
                    <pre className="bg-void rounded p-2 my-2 overflow-x-auto text-xs">{children}</pre>
                  ),
                  code: ({ children }) => (
                    <code className="bg-void rounded px-1 py-0.5 text-xs">{children}</code>
                  ),
                  ul: ({ children }) => <ul className="list-disc pl-4 mb-2">{children}</ul>,
                  ol: ({ children }) => <ol className="list-decimal pl-4 mb-2">{children}</ol>,
                  li: ({ children }) => <li className="mb-0.5">{children}</li>,
                  blockquote: ({ children }) => (
                    <blockquote className="border-l-2 border-lapwing-border pl-3 my-2 text-text-secondary italic">
                      {children}
                    </blockquote>
                  ),
                  h1: ({ children }) => <h3 className="text-base font-semibold text-text-accent mb-1">{children}</h3>,
                  h2: ({ children }) => <h3 className="text-base font-semibold text-text-accent mb-1">{children}</h3>,
                  h3: ({ children }) => <h4 className="text-sm font-semibold text-text-accent mb-1">{children}</h4>,
                }}
              >
                {segment}
              </Markdown>
            )}
            {!isUser && message.tool_calls && message.tool_calls.length > 0 && (
              <div className="mt-2 space-y-1">
                {message.tool_calls.map((tc, j) => (
                  <ToolCallChip key={j} toolCall={tc} />
                ))}
              </div>
            )}
          </div>
        </div>
      ))}
      <span className={`text-[11px] text-text-muted ${isUser ? "pr-1" : "pl-10"}`}>
        {formatTime(message.timestamp)}
      </span>
    </div>
  );
}
