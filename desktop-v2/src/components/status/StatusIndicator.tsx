import { useStatusStore } from "@/stores/status";

const STATUS_CONFIG = {
  idle: { color: "bg-green-400", label: "idle" },
  thinking: { color: "bg-yellow-400 animate-pulse", label: "thinking" },
  working: { color: "bg-blue-400 animate-pulse", label: "working" },
  browsing: { color: "bg-purple-400 animate-pulse", label: "browsing" },
} as const;

export function StatusIndicator() {
  const state = useStatusStore((s) => s.status.state);
  const config = STATUS_CONFIG[state] ?? STATUS_CONFIG.idle;

  return (
    <div className="flex items-center gap-2 px-3 py-2 text-xs text-text-secondary">
      <span className={`w-2 h-2 rounded-full ${config.color}`} />
      <span>{config.label}</span>
    </div>
  );
}
