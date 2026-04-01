import { Pencil, Trash2 } from "lucide-react";

type ProviderCardProps = {
  name: string;
  apiType: string;
  baseUrl: string;
  models: string[];
  onEdit?: () => void;
  onDelete?: () => void;
};

export default function ProviderCard({
  name,
  apiType,
  baseUrl,
  models,
  onEdit,
  onDelete,
}: ProviderCardProps) {
  return (
    <div className="card" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span
            className="card-title"
            style={{ fontSize: 14, fontWeight: 600 }}
          >
            {name}
          </span>
          <span className="badge badge-accent" style={{ fontSize: 11 }}>
            {apiType}
          </span>
        </div>
        <div style={{ display: "flex", gap: 4 }}>
          {onEdit && (
            <button className="btn btn-icon btn-sm btn-ghost" onClick={onEdit} title="编辑">
              <Pencil size={13} />
            </button>
          )}
          {onDelete && (
            <button className="btn btn-icon btn-sm btn-danger" onClick={onDelete} title="删除">
              <Trash2 size={13} />
            </button>
          )}
        </div>
      </div>
      <p
        style={{
          margin: 0,
          fontSize: 12,
          color: "var(--text-muted)",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {baseUrl}
      </p>
      {models.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
          {models.map((m) => (
            <span
              key={m}
              className="badge"
              style={{ fontSize: 11, color: "var(--text-secondary)" }}
            >
              {m}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
