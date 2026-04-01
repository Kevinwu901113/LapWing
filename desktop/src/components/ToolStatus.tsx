type ToolStatusProps = {
  phase: "thinking" | "searching" | "executing" | "done";
  text: string;
  toolName?: string;
};

const PHASE_COLORS: Record<ToolStatusProps["phase"], string> = {
  thinking: "var(--text-muted)",
  searching: "var(--blue)",
  executing: "var(--amber)",
  done: "var(--green)",
};

export default function ToolStatus({ phase, text, toolName }: ToolStatusProps) {
  const color = PHASE_COLORS[phase];

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        gap: 8,
        padding: "6px 0",
      }}
    >
      <span
        className="pulse"
        style={{
          display: "inline-block",
          width: 7,
          height: 7,
          borderRadius: "50%",
          background: color,
          flexShrink: 0,
        }}
      />
      <span style={{ fontSize: 12, color }}>
        {text}
        {toolName && (
          <span style={{ color: "var(--text-muted)", marginLeft: 4 }}>({toolName})</span>
        )}
      </span>
    </div>
  );
}
