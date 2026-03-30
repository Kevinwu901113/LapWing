import { createHashRouter } from "react-router-dom";
import AppShell from "./components/AppShell";
import OverviewPage from "./pages/OverviewPage";
import MemoryPage from "./pages/MemoryPage";
import PersonaPage from "./pages/PersonaPage";
import TasksPage from "./pages/TasksPage";
import EventsPage from "./pages/EventsPage";
import AuthPage from "./pages/AuthPage";
import SettingsPage from "./pages/SettingsPage";

export const router = createHashRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <OverviewPage /> },
      { path: "memory", element: <MemoryPage /> },
      { path: "persona", element: <PersonaPage /> },
      { path: "tasks", element: <TasksPage /> },
      { path: "events", element: <EventsPage /> },
      { path: "auth", element: <AuthPage /> },
      { path: "settings", element: <SettingsPage /> },
    ],
  },
]);
