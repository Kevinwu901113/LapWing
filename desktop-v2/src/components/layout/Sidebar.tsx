import { NavLink } from "react-router-dom";
import {
  MessageSquare, Activity, LayoutDashboard, Eye,
  Brain, Pen, GitBranch, Settings,
} from "lucide-react";
import { useServerStore } from "@/stores/server";
import { StatusBar } from "./StatusBar";

const NAV_ITEMS = [
  { to: "/chat", icon: MessageSquare, label: "对话" },
  { to: "/tasks", icon: Activity, label: "任务中心" },
  { to: "/dashboard", icon: LayoutDashboard, label: "仪表盘" },
  { to: "/sensing", icon: Eye, label: "环境感知" },
  { to: "/memory", icon: Brain, label: "记忆" },
  { to: "/persona", icon: Pen, label: "人格" },
  { to: "/model-routing", icon: GitBranch, label: "模型路由" },
  { to: "/settings", icon: Settings, label: "设置" },
] as const;

export function Sidebar() {
  const connected = useServerStore((s) => s.connected);

  return (
    <aside className="w-[240px] h-full flex flex-col bg-void-100 border-r border-surface-border shrink-0">
      {/* Header */}
      <div className="px-4 pt-5 pb-3 flex items-center gap-3">
        <div className="w-10 h-10 rounded-full bg-gradient-to-br from-lapwing-light to-lapwing-dark flex items-center justify-center text-void font-bold text-lg">
          L
        </div>
        <div>
          <div className="text-text-accent font-medium text-sm">Lapwing</div>
          <div className="flex items-center gap-1.5 text-[12px] text-text-secondary">
            <span className={`w-1.5 h-1.5 rounded-full ${connected ? "bg-green-400" : "bg-gray-500"}`} />
            {connected ? "在线" : "离线"}
          </div>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-2 py-2 space-y-0.5 overflow-y-auto">
        {NAV_ITEMS.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors ${
                isActive
                  ? "bg-surface-active text-text-accent"
                  : "text-text-secondary hover:bg-surface-hover hover:text-text-primary"
              }`
            }
          >
            <Icon size={18} />
            <span>{label}</span>
          </NavLink>
        ))}
      </nav>

      {/* Status bar */}
      <div className="border-t border-surface-border">
        <StatusBar />
      </div>
    </aside>
  );
}
