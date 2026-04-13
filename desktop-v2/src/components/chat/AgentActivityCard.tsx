import { Bot, Loader2, CheckCircle2, XCircle } from "lucide-react";
import type { AgentActivity } from "@/types/chat";

const STATE_STYLES: Record<string, { icon: typeof Bot; color: string; bg: string }> = {
  queued: { icon: Loader2, color: "text-yellow-400", bg: "bg-yellow-900/20 border-yellow-800/30" },
  working: { icon: Loader2, color: "text-blue-400", bg: "bg-blue-900/20 border-blue-800/30" },
  done: { icon: CheckCircle2, color: "text-green-400", bg: "bg-green-900/20 border-green-800/30" },
  failed: { icon: XCircle, color: "text-red-400", bg: "bg-red-900/20 border-red-800/30" },
  blocked: { icon: XCircle, color: "text-orange-400", bg: "bg-orange-900/20 border-orange-800/30" },
  cancelled: { icon: XCircle, color: "text-text-muted", bg: "bg-surface border-surface-border" },
};

export function AgentActivityCard({ activity }: { activity: AgentActivity }) {
  const style = STATE_STYLES[activity.state] ?? STATE_STYLES.queued;
  const Icon = style.icon;
  const isAnimated = activity.state === "queued" || activity.state === "working";

  return (
    <div className={`flex items-start gap-3 px-3 py-2 rounded-lg border text-sm ${style.bg} ml-10`}>
      <Icon size={16} className={`${style.color} shrink-0 mt-0.5 ${isAnimated ? "animate-spin" : ""}`} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-medium text-text-accent">{activity.agentName}</span>
          <span className={`text-xs ${style.color}`}>{activity.state}</span>
        </div>
        {activity.note && (
          <div className="text-xs text-text-secondary mt-0.5 truncate">{activity.note}</div>
        )}
        {activity.headline && (
          <div className="text-xs text-text-primary mt-1">{activity.headline}</div>
        )}
        {activity.progress != null && activity.state === "working" && (
          <div className="mt-1.5 h-1 rounded-full bg-void overflow-hidden">
            <div
              className="h-full rounded-full bg-blue-400 transition-all duration-300"
              style={{ width: `${Math.round(activity.progress * 100)}%` }}
            />
          </div>
        )}
      </div>
    </div>
  );
}
