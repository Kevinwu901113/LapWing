import { NavLink } from "react-router-dom";
import {
  LayoutDashboard,
  Brain,
  Sparkles,
  ListTodo,
  Radio,
  Shield,
  Settings,
  PanelLeftClose,
  PanelLeft,
} from "lucide-react";
import StatusDot from "./StatusDot";

type SidebarProps = {
  collapsed: boolean;
  onToggle: () => void;
  online?: boolean;
};

const NAV_ITEMS: { to: string; icon: typeof LayoutDashboard; label: string; end?: boolean }[] = [
  { to: "/", icon: LayoutDashboard, label: "总览", end: true },
  { to: "/memory", icon: Brain, label: "记忆" },
  { to: "/persona", icon: Sparkles, label: "人格" },
  { to: "/tasks", icon: ListTodo, label: "任务" },
  { to: "/events", icon: Radio, label: "事件" },
  { to: "/auth", icon: Shield, label: "认证" },
  { to: "/settings", icon: Settings, label: "设置" },
];

export default function Sidebar({ collapsed, onToggle, online = false }: SidebarProps) {
  return (
    <aside className={`sidebar ${collapsed ? "sidebar--collapsed" : ""}`}>
      {/* 顶部 Logo 区域 */}
      <div className="sidebar-header">
        {!collapsed && <span className="sidebar-logo">Lapwing</span>}
        <button className="sidebar-toggle btn-icon" onClick={onToggle}>
          {collapsed ? <PanelLeft size={18} /> : <PanelLeftClose size={18} />}
        </button>
      </div>

      {/* 导航项 */}
      <nav className="sidebar-nav">
        {NAV_ITEMS.map(({ to, icon: Icon, label, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              `sidebar-item ${isActive ? "sidebar-item--active" : ""}`
            }
            title={collapsed ? label : undefined}
          >
            <Icon size={20} strokeWidth={1.8} />
            {!collapsed && <span>{label}</span>}
          </NavLink>
        ))}
      </nav>

      {/* 底部状态 */}
      <div className="sidebar-footer">
        {!collapsed && (
          <div className="sidebar-status">
            <StatusDot online={online} />
            <span>{online ? "后端在线" : "后端离线"}</span>
          </div>
        )}
      </div>
    </aside>
  );
}
