import type { HeartbeatStatus } from "@/types/api";

function formatRelative(ts: string): string {
  try {
    const diff = Date.now() - new Date(ts).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return "刚刚";
    if (mins < 60) return `${mins}分钟前`;
    return `${Math.floor(mins / 60)}小时前`;
  } catch {
    return ts;
  }
}

export function HeartbeatCard({ status }: { status: HeartbeatStatus | null }) {
  if (!status) {
    return (
      <div className="text-sm text-text-muted">心跳引擎未就绪</div>
    );
  }

  return (
    <div className="space-y-2 text-sm">
      <div className="flex justify-between">
        <span className="text-text-secondary">上次快心跳</span>
        <span className="text-text-primary">{formatRelative(status.last_fast_tick)}</span>
      </div>
      <div className="flex justify-between">
        <span className="text-text-secondary">上次动作</span>
        <span className="text-text-primary">{status.last_action || "无"}</span>
      </div>
    </div>
  );
}
