import { useEffect, useState } from "react";
import { Outlet } from "react-router-dom";
import Sidebar from "./Sidebar";
import { getStatus } from "../api";

export default function AppShell() {
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
    <div style={{ display: "flex", minHeight: "100vh" }}>
      <Sidebar online={online} />
      <main className="app-main">
        <Outlet />
      </main>
    </div>
  );
}
