import { createHashRouter, Navigate } from "react-router-dom";
import AppShell from "./components/AppShell";
import ConnectionPage from "./pages/ConnectionPage";
import DashboardPage from "./pages/DashboardPage";
import ChatPage from "./pages/ChatPage";
import MemoryPage from "./pages/MemoryPage";
import PersonaPage from "./pages/PersonaPage";
import TasksPage from "./pages/TasksPage";
import LogsPage from "./pages/LogsPage";
import SettingsPage from "./pages/SettingsPage";

function RequireConnection({ children }: { children: React.ReactNode }) {
  const token = localStorage.getItem("lapwing_token");
  if (!token) return <Navigate to="/connect" replace />;
  return <>{children}</>;
}

export const router = createHashRouter([
  {
    path: "/connect",
    element: <ConnectionPage />,
  },
  {
    path: "/",
    element: (
      <RequireConnection>
        <AppShell />
      </RequireConnection>
    ),
    children: [
      { index: true, element: <DashboardPage /> },
      { path: "chat", element: <ChatPage /> },
      { path: "memory", element: <MemoryPage /> },
      { path: "persona", element: <PersonaPage /> },
      { path: "tasks", element: <TasksPage /> },
      { path: "logs", element: <LogsPage /> },
      { path: "settings", element: <SettingsPage /> },
    ],
  },
]);
