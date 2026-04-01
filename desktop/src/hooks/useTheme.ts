import { useState } from "react";

type Theme = "dark" | "light";

function getInitialTheme(): Theme {
  const stored = localStorage.getItem("lapwing_theme");
  return (stored === "light" ? "light" : "dark") as Theme;
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(() => {
    const t = getInitialTheme();
    document.documentElement.dataset.theme = t;
    return t;
  });

  const toggle = () => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("lapwing_theme", next);
    setTheme(next);
  };

  return { theme, toggle };
}
