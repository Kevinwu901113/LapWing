import { Sun, Moon } from "lucide-react";
import { useTheme } from "../hooks/useTheme";

export default function ThemeToggle() {
  const { theme, toggle } = useTheme();
  return (
    <button
      className="sidebar-item btn-icon"
      onClick={toggle}
      title={theme === "dark" ? "切换浅色" : "切换深色"}
    >
      {theme === "dark" ? (
        <Moon size={20} strokeWidth={1.8} />
      ) : (
        <Sun size={20} strokeWidth={1.8} />
      )}
      <span>{theme === "dark" ? "深色模式" : "浅色模式"}</span>
    </button>
  );
}
