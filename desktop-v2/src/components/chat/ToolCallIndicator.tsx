import { Loader2 } from "lucide-react";
import type { ToolStatusInfo } from "@/types/chat";

export function ToolCallIndicator({ status }: { status: ToolStatusInfo }) {
  return (
    <div className="flex items-center gap-2 px-3 py-1.5 rounded-md bg-surface text-text-secondary text-sm">
      <Loader2 size={14} className="animate-spin text-lapwing" />
      <span>
        {status.toolName ? `${status.toolName}` : status.text || status.phase}
      </span>
    </div>
  );
}
