import { createHashRouter } from "react-router-dom";
import AppShell from "./components/AppShell";
import DashboardPage from "./pages/DashboardPage";
import MemoryPage from "./pages/MemoryPage";
import PersonaPage from "./pages/PersonaPage";
import TasksPage from "./pages/TasksPage";
import AuthPage from "./pages/AuthPage";
import SettingsPage from "./pages/SettingsPage";
import ChatPage from "./pages/ChatPage";

export const router = createHashRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: "chat", element: <ChatPage /> },
      { path: "memory", element: <MemoryPage /> },
      { path: "persona", element: <PersonaPage /> },
      { path: "tasks", element: <TasksPage /> },
      { path: "auth", element: <AuthPage /> },
      { path: "settings", element: <SettingsPage /> },
    ],
  },
]);
