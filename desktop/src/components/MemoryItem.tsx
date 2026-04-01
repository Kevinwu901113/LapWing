import { Trash2 } from "lucide-react";

type MemoryItemProps = {
  factKey: string;
  factValue: string;
  category?: string;
  createdAt?: string;
  onDelete?: () => void;
};

const CATEGORY_COLORS: Record<string, string> = {
  kevin: "var(--blue)",
  self: "var(--accent)",
  fact: "var(--green)",
  interest: "var(--amber)",
};

function getCategoryColor(category?: string): string {
  if (!category) return "var(--border)";
  return CATEGORY_COLORS[category] ?? "var(--border)";
}

export default function MemoryItem({
  factKey,
  factValue,
  category,
  createdAt,
  onDelete,
}: MemoryItemProps) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "stretch",
        gap: 12,
        padding: "8px 0",
        borderBottom: "1px solid var(--border)",
      }}
    >
      <div
        style={{
          width: 4,
          flexShrink: 0,
          borderRadius: 2,
          background: getCategoryColor(category),
          alignSelf: "stretch",
        }}
      />
      <div style={{ flex: 1, minWidth: 0 }}>
        <p
          style={{
            margin: 0,
            fontSize: 13,
            fontWeight: 600,
            color: "var(--text-primary)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {factKey}
        </p>
        <p
          style={{
            margin: "2px 0 0",
            fontSize: 13,
            color: "var(--text-secondary)",
          }}
        >
          {factValue}
        </p>
      </div>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "flex-end",
          gap: 4,
          flexShrink: 0,
        }}
      >
        {createdAt && (
          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{createdAt}</span>
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
