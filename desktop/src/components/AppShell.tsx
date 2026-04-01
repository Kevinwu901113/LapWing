import { Outlet } from "react-router-dom";
import Sidebar from "./Sidebar";
import { useServerStatus } from "../hooks/useServerStatus";

export default function AppShell() {
  const { online } = useServerStatus();

  return (
    <div style={{ display: "flex", minHeight: "100vh" }}>
      <Sidebar online={online} />
      <main className="app-main">
        <Outlet />
      </main>
    </div>
  );
}
