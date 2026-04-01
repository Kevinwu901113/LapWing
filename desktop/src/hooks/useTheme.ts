import { useState, useEffect } from "react";

type Theme = "dark" | "light";

function applyTheme(t: Theme) {
  document.documentElement.dataset.theme = t;
  localStorage.setItem("lapwing_theme", t);
}

function getStoredTheme(): Theme {
  return localStorage.getItem("lapwing_theme") === "light" ? "light" : "dark";
}

const THEME_CHANGE_EVENT = "lapwing:themechange";

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(getStoredTheme);

  useEffect(() => {
    const handler = (e: CustomEvent<Theme>) => setTheme(e.detail);
    window.addEventListener(THEME_CHANGE_EVENT, handler as EventListener);
    return () => window.removeEventListener(THEME_CHANGE_EVENT, handler as EventListener);
  }, []);

  const toggle = () => {
    const next: Theme = getStoredTheme() === "dark" ? "light" : "dark";
    applyTheme(next);
    window.dispatchEvent(new CustomEvent(THEME_CHANGE_EVENT, { detail: next }));
    setTheme(next);
  };

  return { theme, toggle };
}
