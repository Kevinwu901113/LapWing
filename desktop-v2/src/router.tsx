import { createBrowserRouter, Navigate } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import ChatPage from "@/pages/ChatPage";
import TaskCenterPage from "@/pages/TaskCenterPage";
import DashboardPage from "@/pages/DashboardPage";
import SensingPage from "@/pages/SensingPage";
import MemoryPage from "@/pages/MemoryPage";
import PersonaPage from "@/pages/PersonaPage";
import ModelRoutingPage from "@/pages/ModelRoutingPage";
import SettingsPage from "@/pages/SettingsPage";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <Navigate to="/chat" replace /> },
      { path: "chat", element: <ChatPage /> },
      { path: "tasks", element: <TaskCenterPage /> },
      { path: "dashboard", element: <DashboardPage /> },
      { path: "sensing", element: <SensingPage /> },
      { path: "memory", element: <MemoryPage /> },
      { path: "persona", element: <PersonaPage /> },
      { path: "model-routing", element: <ModelRoutingPage /> },
      { path: "settings", element: <SettingsPage /> },
    ],
  },
]);
