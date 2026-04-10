import type { TaskStep } from "@/types/tasks";

export function ToolCallDetail({ step }: { step: TaskStep }) {
  return (
    <div className="border-l-2 border-surface-border pl-3 py-2">
      <div className="flex items-center gap-2">
        <span className={`w-2 h-2 rounded-full ${
          step.status === "completed" ? "bg-green-400" :
          step.status === "running" ? "bg-blue-400 animate-pulse" :
          "bg-gray-500"
        }`} />
        <span className="text-sm text-text-primary">
          {step.tool_name ?? step.description}
        </span>
      </div>
      {step.result && (
        <pre className="mt-1 text-xs text-text-secondary bg-void-50 rounded p-2 overflow-auto max-h-32">
          {JSON.stringify(step.result, null, 2)}
        </pre>
      )}
    </div>
  );
}
