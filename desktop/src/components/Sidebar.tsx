import { NavLink } from "react-router-dom";
import {
  LayoutDashboard,
  MessageSquare,
  Brain,
  Sparkles,
  ListTodo,
  ScrollText,
  Settings,
} from "lucide-react";
import StatusDot from "./StatusDot";
import ThemeToggle from "./ThemeToggle";

type SidebarProps = {
  online?: boolean;
};

const NAV_ITEMS = [
  { to: "/", icon: LayoutDashboard, label: "仪表盘", end: true },
  { to: "/chat", icon: MessageSquare, label: "对话" },
  { to: "/memory", icon: Brain, label: "记忆" },
  { to: "/persona", icon: Sparkles, label: "人格" },
  { to: "/tasks", icon: ListTodo, label: "任务" },
  { to: "/logs", icon: ScrollText, label: "日志" },
];

export default function Sidebar({ online = false }: SidebarProps) {
  return (
    <aside className="sidebar">
      {/* Logo */}
      <div className="sidebar-header">
        <span className="sidebar-logo">Lapwing</span>
      </div>

      {/* Nav */}
      <nav className="sidebar-nav">
        {NAV_ITEMS.map(({ to, icon: Icon, label, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              `sidebar-item ${isActive ? "sidebar-item--active" : ""}`
            }
          >
            <Icon size={20} strokeWidth={1.8} />
            <span>{label}</span>
          </NavLink>
        ))}
      </nav>

      {/* Footer */}
      <div className="sidebar-footer">
        <ThemeToggle />
        <div className="sidebar-status">
          <StatusDot online={online} />
          <span>{online ? "后端在线" : "后端离线"}</span>
        </div>
        <NavLink
          to="/settings"
          className={({ isActive }) =>
            `sidebar-item ${isActive ? "sidebar-item--active" : ""}`
          }
        >
          <Settings size={20} strokeWidth={1.8} />
          <span>设置</span>
        </NavLink>
      </div>
    </aside>
  );
}
