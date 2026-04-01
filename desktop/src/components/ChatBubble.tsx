import { CheckCircle2, XCircle, Loader2 } from "lucide-react";

type ToolCallInfo = {
  name: string;
  status: "running" | "done" | "error";
  duration_ms?: number;
};

type ChatBubbleProps = {
  role: "user" | "assistant" | "system";
  content: string;
  timestamp?: string;
  toolCalls?: ToolCallInfo[];
};

function ToolCallPill({ call }: { call: ToolCallInfo }) {
  const icon =
    call.status === "done" ? (
      <CheckCircle2 size={12} style={{ color: "var(--green)" }} />
    ) : call.status === "error" ? (
      <XCircle size={12} style={{ color: "var(--red)" }} />
    ) : (
      <Loader2 size={12} style={{ color: "var(--accent)", animation: "spin 1s linear infinite" }} />
    );

  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: "2px 8px",
        borderRadius: 12,
        background: "var(--bg-card)",
        border: "1px solid var(--border)",
        fontSize: 11,
        color: "var(--text-secondary)",
      }}
    >
      {icon}
      {call.name}
      {call.duration_ms != null && (
        <span style={{ color: "var(--text-muted)" }}>{call.duration_ms}ms</span>
      )}
    </span>
  );
}

export default function ChatBubble({ role, content, timestamp, toolCalls }: ChatBubbleProps) {
  if (role === "system") {
    return (
      <div style={{ textAlign: "center", padding: "4px 0" }}>
        <span style={{ fontSize: 11, color: "var(--text-muted)", fontStyle: "italic" }}>
          {content}
        </span>
      </div>
    );
  }

  const isUser = role === "user";

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: isUser ? "flex-end" : "flex-start",
        gap: 4,
        padding: "4px 0",
      }}
    >
      <div
        style={{
          maxWidth: "75%",
          background: isUser ? "var(--accent)" : "var(--bg-card)",
          border: isUser ? "none" : "1px solid var(--border)",
          borderRadius: isUser ? "12px 12px 4px 12px" : "12px 12px 12px 4px",
          padding: "8px 12px",
          fontSize: 13,
          color: isUser ? "#fff" : "var(--text-primary)",
          lineHeight: 1.5,
          wordBreak: "break-word",
        }}
      >
        {content}
      </div>
      {toolCalls && toolCalls.length > 0 && (
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: 4,
            justifyContent: isUser ? "flex-end" : "flex-start",
          }}
        >
          {toolCalls.map((call, i) => (
            <ToolCallPill key={i} call={call} />
          ))}
        </div>
      )}
      {timestamp && (
        <span style={{ fontSize: 10, color: "var(--text-muted)" }}>{timestamp}</span>
      )}
    </div>
  );
}
