import { Outlet } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { useSSEv2 } from "@/hooks/useSSEv2";
import { useStatus } from "@/hooks/useStatus";

export function AppShell() {
  // Initialize global SSE connection and status polling
  useSSEv2();
  useStatus();

  return (
    <div className="flex h-screen w-screen bg-void">
      <Sidebar />
      <main className="flex-1 overflow-hidden">
        <Outlet />
      </main>
    </div>
  );
}
