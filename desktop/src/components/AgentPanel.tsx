import { CheckCircle2, XCircle, Loader2 } from "lucide-react";

export type ToolLogEntry = {
  toolName: string;
  status: "running" | "done" | "error";
  duration_ms?: number;
  timestamp: string;
};

type AgentPanelProps = {
  status: "connected" | "connecting" | "disconnected";
  toolLog: ToolLogEntry[];
  sessionInfo?: {
    channel?: string;
    toolCount?: number;
    contextTokens?: number;
    modelName?: string;
  };
};

const STATUS_COLORS: Record<AgentPanelProps["status"], string> = {
  connected: "var(--green)",
  connecting: "var(--amber)",
  disconnected: "var(--red)",
};

const STATUS_LABELS: Record<AgentPanelProps["status"], string> = {
  connected: "已连接",
  connecting: "连接中…",
  disconnected: "未连接",
};

function StatusIcon({ entryStatus }: { entryStatus: ToolLogEntry["status"] }) {
  if (entryStatus === "done") return <CheckCircle2 size={13} style={{ color: "var(--green)" }} />;
  if (entryStatus === "error") return <XCircle size={13} style={{ color: "var(--red)" }} />;
  return (
    <Loader2
      size={13}
      style={{ color: "var(--accent)", animation: "spin 1s linear infinite" }}
    />
  );
}

export default function AgentPanel({ status, toolLog, sessionInfo }: AgentPanelProps) {
  const dotColor = STATUS_COLORS[status];
  const label = STATUS_LABELS[status];
  const recent = toolLog.slice(-20);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 16,
        height: "100%",
      }}
    >
      {/* Connection status */}
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span
          style={{
            width: 8,
            height: 8,
            borderRadius: "50%",
            background: dotColor,
            flexShrink: 0,
          }}
        />
        <span style={{ fontSize: 13, color: "var(--text-primary)" }}>{label}</span>
      </div>

      {/* Tool log */}
      <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: 4 }}>
        {recent.length === 0 ? (
          <p className="empty-hint">暂无工具调用记录</p>
        ) : (
          recent.map((entry, i) => (
            <div
              key={`${entry.timestamp}-${entry.toolName}`}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                padding: "4px 0",
                borderBottom: "1px solid var(--border)",
              }}
            >
              <StatusIcon entryStatus={entry.status} />
              <span
                style={{
                  flex: 1,
                  fontSize: 12,
                  color: "var(--text-primary)",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {entry.toolName}
              </span>
              {entry.duration_ms != null && (
                <span style={{ fontSize: 11, color: "var(--text-muted)", flexShrink: 0 }}>
                  {entry.duration_ms}ms
                </span>
              )}
            </div>
          ))
        )}
      </div>

      {/* Session info */}
      {sessionInfo && (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 4,
            borderTop: "1px solid var(--border)",
            paddingTop: 10,
          }}
        >
          {sessionInfo.channel && (
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={{ fontSize: 11, color: "var(--text-muted)" }}>频道</span>
              <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>{sessionInfo.channel}</span>
            </div>
          )}
          {sessionInfo.toolCount != null && (
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={{ fontSize: 11, color: "var(--text-muted)" }}>工具调用</span>
              <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>{sessionInfo.toolCount}</span>
            </div>
          )}
          {sessionInfo.contextTokens != null && (
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={{ fontSize: 11, color: "var(--text-muted)" }}>Token</span>
              <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>{sessionInfo.contextTokens}</span>
            </div>
          )}
          {sessionInfo.modelName && (
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={{ fontSize: 11, color: "var(--text-muted)" }}>模型</span>
              <span
                style={{
                  fontSize: 11,
                  color: "var(--text-secondary)",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  maxWidth: 120,
                }}
              >
                {sessionInfo.modelName}
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
