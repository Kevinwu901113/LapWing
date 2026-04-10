import type { TaskFlow } from "@/types/tasks";
import { Badge } from "@/components/ui/badge";

const STATUS_STYLES: Record<string, string> = {
  pending: "bg-yellow-900/30 text-yellow-400 border-yellow-800",
  running: "bg-blue-900/30 text-blue-400 border-blue-800",
  completed: "bg-green-900/30 text-green-400 border-green-800",
  failed: "bg-red-900/30 text-red-400 border-red-800",
  cancelled: "bg-gray-800/30 text-gray-400 border-gray-700",
};

interface Props {
  flow: TaskFlow;
  selected: boolean;
  onClick: () => void;
}

export function TaskFlowCard({ flow, selected, onClick }: Props) {
  const completedSteps = flow.steps.filter((s) => s.status === "completed").length;
  const progress = flow.steps.length > 0 ? (completedSteps / flow.steps.length) * 100 : 0;

  return (
    <button
      onClick={onClick}
      className={`w-full text-left p-3 rounded-lg border transition-colors ${
        selected
          ? "bg-surface-active border-lapwing-border"
          : "bg-surface border-surface-border hover:bg-surface-hover"
      }`}
    >
      <div className="flex items-center justify-between mb-1">
        <span className="text-sm text-text-primary truncate">{flow.title}</span>
        <Badge variant="outline" className={STATUS_STYLES[flow.status] ?? ""}>
          {flow.status}
        </Badge>
      </div>
      {flow.status === "running" && (
        <div className="w-full h-1 bg-void-50 rounded-full mt-2">
          <div
            className="h-full bg-lapwing rounded-full transition-all duration-300"
            style={{ width: `${progress}%` }}
          />
        </div>
      )}
      <div className="text-xs text-text-muted mt-1">
        {completedSteps}/{flow.steps.length} 步骤
      </div>
    </button>
  );
}
