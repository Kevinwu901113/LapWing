import { useState } from "react";
import { Outlet } from "react-router-dom";
import Sidebar from "./Sidebar";
import AuthGuard from "./AuthGuard";

export default function AppShell() {
  const [collapsed, setCollapsed] = useState(false);

  return (
    <AuthGuard>
      <div style={{
        display: "flex",
        minHeight: "100vh",
      }}>
        <Sidebar collapsed={collapsed} onToggle={() => setCollapsed(!collapsed)} />
        <main style={{
          flex: 1,
          marginLeft: collapsed ? "var(--sidebar-collapsed-width)" : "var(--sidebar-width)",
          padding: "1.5rem 2rem 3rem",
          transition: "margin-left 0.25s ease",
          maxWidth: "1100px",
        }}>
          <Outlet />
        </main>
      </div>
    </AuthGuard>
  );
}
