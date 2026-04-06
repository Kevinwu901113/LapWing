import type { TaskFlowItem } from "../api";

const STATUS_ICONS: Record<string, string> = {
  completed: "✅",
  running: "🔄",
  pending: "⏳",
  failed: "❌",
  cancelled: "🚫",
};

type Props = {
  flow: TaskFlowItem;
  onCancel: (flowId: string) => void;
};

export default function TaskFlowCard({ flow, onCancel }: Props) {
  return (
    <div style={{
      border: "1px solid var(--border)",
      borderRadius: "var(--radius-md)",
      padding: "14px 16px",
      background: "var(--surface)",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 14, fontWeight: 500 }}>{flow.title}</span>
          <span style={{
            fontSize: 11,
            padding: "2px 6px",
            borderRadius: 4,
            background: flow.status === "running" ? "var(--blue-dim)" : "var(--surface-raised)",
            color: flow.status === "running" ? "var(--blue)" : "var(--text-muted)",
          }}>
            {flow.status}
          </span>
        </div>
        {flow.status === "running" && (
          <button
            onClick={() => onCancel(flow.flow_id)}
            style={{
              fontSize: 12,
              color: "var(--red)",
              background: "none",
              border: "1px solid var(--red)",
              borderRadius: 4,
              padding: "2px 8px",
              cursor: "pointer",
            }}
          >
            取消
          </button>
        )}
      </div>

      {/* Progress bar */}
      <div style={{ background: "var(--border)", borderRadius: 4, height: 6, marginBottom: 10 }}>
        <div style={{
          width: `${flow.progress_pct}%`,
          height: "100%",
          background: flow.status === "failed" ? "var(--red)" : "var(--blue)",
          borderRadius: 4,
          transition: "width 0.3s ease",
        }} />
      </div>

      {/* Steps */}
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {flow.steps.map(step => (
          <div key={step.step_id} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12 }}>
            <span style={{ flexShrink: 0 }}>{STATUS_ICONS[step.status] ?? "⏳"}</span>
            <span style={{ color: step.status === "completed" ? "var(--text-secondary)" : "var(--text-primary)" }}>
              {step.description}
            </span>
          </div>
        ))}
      </div>

      <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 8 }}>
        {flow.progress_pct}% · {flow.flow_id}
      </div>
    </div>
  );
}
