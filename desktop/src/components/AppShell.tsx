import { useEffect, useState } from "react";
import { Outlet } from "react-router-dom";
import Sidebar from "./Sidebar";
import AuthGuard from "./AuthGuard";
import { getStatus } from "../api";

export default function AppShell() {
  const [collapsed, setCollapsed] = useState(false);
  const [online, setOnline] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        const s = await getStatus();
        if (!cancelled) setOnline(s.online);
      } catch {
        if (!cancelled) setOnline(false);
      }
    }

    void poll();
    const timer = setInterval(poll, 30_000);
    return () => { cancelled = true; clearInterval(timer); };
  }, []);

  return (
    <AuthGuard>
      <div style={{
        display: "flex",
        minHeight: "100vh",
      }}>
        <Sidebar collapsed={collapsed} onToggle={() => setCollapsed(!collapsed)} online={online} />
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
