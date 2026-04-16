import { NavLink } from "react-router-dom";
import {
  MessageSquare, BookOpen, Fingerprint,
  Monitor, Settings,
} from "lucide-react";
import { useServerStore } from "@/stores/server";
import { StatusIndicator } from "@/components/status/StatusIndicator";

const NAV_ITEMS = [
  { to: "/chat", icon: MessageSquare, label: "Chat" },
  { to: "/notes", icon: BookOpen, label: "Notes" },
  { to: "/identity", icon: Fingerprint, label: "Identity" },
  { to: "/system", icon: Monitor, label: "System" },
  { to: "/settings", icon: Settings, label: "Settings" },
] as const;

export function Sidebar() {
  const connected = useServerStore((s) => s.connected);

  return (
    <aside className="w-[200px] h-full flex flex-col bg-void-100 border-r border-surface-border shrink-0">
      {/* Header */}
      <div className="px-4 pt-5 pb-3 flex items-center gap-3">
        <div className="w-9 h-9 rounded-full bg-gradient-to-br from-lapwing-light to-lapwing-dark flex items-center justify-center text-void font-bold text-sm">
          L
        </div>
        <div>
          <div className="text-text-accent font-medium text-sm">Lapwing</div>
          <div className="flex items-center gap-1.5 text-[11px] text-text-secondary">
            <span className={`w-1.5 h-1.5 rounded-full ${connected ? "bg-green-400" : "bg-gray-500"}`} />
            {connected ? "online" : "offline"}
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

      {/* Status indicator */}
      <div className="border-t border-surface-border">
        <StatusIndicator />
      </div>
    </aside>
  );
}
