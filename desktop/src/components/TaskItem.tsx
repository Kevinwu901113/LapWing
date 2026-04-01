import { Clock, Loader2, CheckCircle2, Trash2 } from "lucide-react";

type TaskItemProps = {
  id: string;
  title: string;
  status: "pending" | "running" | "completed";
  scheduledAt?: string;
  recurrence?: string;
  onDelete?: () => void;
};

const STATUS_ICONS = {
  pending: <Clock size={15} style={{ color: "var(--text-muted)" }} />,
  running: (
    <Loader2
      size={15}
      style={{
        color: "var(--accent)",
        animation: "spin 1s linear infinite",
      }}
    />
  ),
  completed: <CheckCircle2 size={15} style={{ color: "var(--green)" }} />,
};

export default function TaskItem({
  id,
  title,
  status,
  scheduledAt,
  recurrence,
  onDelete,
}: TaskItemProps) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "8px 0",
        borderBottom: "1px solid var(--border)",
      }}
    >
      <span style={{ flexShrink: 0 }}>{STATUS_ICONS[status]}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <p
          style={{
            margin: 0,
            fontSize: 13,
            color: "var(--text-primary)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {title}
        </p>
        <p style={{ margin: "2px 0 0", fontSize: 11, color: "var(--text-muted)" }}>{id}</p>
      </div>
      {recurrence && (
        <span className="badge badge-accent" style={{ fontSize: 11 }}>
          {recurrence}
        </span>
      )}
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
        {scheduledAt && (
          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{scheduledAt}</span>
        )}
        {onDelete && (
          <button
            className="btn btn-icon btn-sm btn-danger"
            onClick={onDelete}
            title="删除"
            style={{ opacity: 0.7 }}
          >
            <Trash2 size={13} />
          </button>
        )}
      </div>
    </div>
  );
}
