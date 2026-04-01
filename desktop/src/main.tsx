import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router-dom";
import { router } from "./router";
import "./styles/globals.css";

// Apply theme before React mounts to avoid flash
const storedTheme = localStorage.getItem("lapwing_theme") ?? "dark";
document.documentElement.dataset.theme = storedTheme;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
