import { Trash2 } from "lucide-react";
import type { ReminderItem } from "@/types/api";

interface Props {
  reminders: ReminderItem[];
  onDelete?: (id: number) => void;
}

export function ReminderList({ reminders, onDelete }: Props) {
  if (reminders.length === 0) {
    return <div className="text-sm text-text-muted">暂无提醒</div>;
  }

  return (
    <div className="space-y-2">
      {reminders.map((r) => (
        <div key={r.id} className="flex items-start gap-2 text-sm group">
          <span className="w-1.5 h-1.5 rounded-full bg-lapwing mt-1.5 shrink-0" />
          <div className="flex-1 min-w-0">
            <div className="text-text-primary truncate">{r.content}</div>
            <div className="text-xs text-text-muted">
              {new Date(r.trigger_at).toLocaleString("zh-CN", {
                month: "short", day: "numeric",
                hour: "2-digit", minute: "2-digit",
              })}
            </div>
          </div>
          {onDelete && (
            <button
              onClick={() => onDelete(r.id)}
              className="opacity-0 group-hover:opacity-100 text-text-muted hover:text-red-400 transition-opacity"
            >
              <Trash2 size={14} />
            </button>
          )}
        </div>
      ))}
    </div>
  );
}
