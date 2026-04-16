import { createBrowserRouter, Navigate } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import ChatPage from "@/pages/ChatPage";
import SettingsPage from "@/pages/SettingsPage";

// P3-P6 pages — use existing pages as placeholders for now
import NotesPage from "@/pages/NotesPage";
import IdentityPage from "@/pages/IdentityPage";
import DashboardPage from "@/pages/DashboardPage";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <Navigate to="/chat" replace /> },
      { path: "chat", element: <ChatPage /> },
      { path: "notes", element: <NotesPage /> },
      { path: "identity", element: <IdentityPage /> },
      { path: "system", element: <DashboardPage /> },
      { path: "settings", element: <SettingsPage /> },
    ],
  },
]);
