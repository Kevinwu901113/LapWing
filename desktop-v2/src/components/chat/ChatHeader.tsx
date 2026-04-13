import { useChatStore } from "@/stores/chat";
import { Loader2, Wifi, WifiOff, Bot, Wrench } from "lucide-react";

const STATUS_CONFIG = {
  idle: { icon: Bot, text: "Lapwing", className: "text-lapwing" },
  thinking: { icon: Loader2, text: "思考中...", className: "text-lapwing animate-spin" },
  using_tool: { icon: Wrench, text: "", className: "text-yellow-400" },
  delegating: { icon: Bot, text: "委派 Agent...", className: "text-blue-400" },
} as const;

export function ChatHeader() {
  const wsStatus = useChatStore((s) => s.wsStatus);
  const lapwingStatus = useChatStore((s) => s.lapwingStatus);
  const toolStatus = useChatStore((s) => s.toolStatus);

  const config = STATUS_CONFIG[lapwingStatus];
  const Icon = config.icon;
  const statusText = lapwingStatus === "using_tool"
    ? (toolStatus?.toolName ?? toolStatus?.text ?? "使用工具中...")
    : config.text;

  return (
    <div className="flex items-center justify-between px-4 py-2 border-b border-surface-border bg-void-100">
      <div className="flex items-center gap-2">
        <div className="w-7 h-7 rounded-full bg-gradient-to-br from-lapwing-light to-lapwing-dark flex items-center justify-center text-void text-xs font-bold">
          L
        </div>
        <div>
          <div className="text-sm font-medium text-text-accent">Lapwing</div>
          <div className="flex items-center gap-1 text-xs text-text-muted">
            <Icon size={12} className={config.className} />
            <span>{statusText}</span>
          </div>
        </div>
      </div>
      <div className="flex items-center gap-1 text-xs text-text-muted">
        {wsStatus === "connected" ? (
          <Wifi size={14} className="text-green-500" />
        ) : (
          <WifiOff size={14} className="text-red-400" />
        )}
        <span>{wsStatus === "connected" ? "已连接" : wsStatus === "connecting" ? "连接中..." : "已断开"}</span>
      </div>
    </div>
  );
}
