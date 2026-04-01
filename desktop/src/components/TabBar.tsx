type Tab = { id: string; label: string };

type TabBarProps = {
  tabs: Tab[];
  activeTab: string;
  onChange: (id: string) => void;
  orientation?: "horizontal" | "vertical";
};

export default function TabBar({
  tabs,
  activeTab,
  onChange,
  orientation = "horizontal",
}: TabBarProps) {
  if (orientation === "vertical") {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        {tabs.map((tab) => {
          const isActive = tab.id === activeTab;
          return (
            <button
              key={tab.id}
              onClick={() => onChange(tab.id)}
              style={{
                background: isActive ? "var(--bg-hover, rgba(255,255,255,0.07))" : "transparent",
                border: "none",
                borderLeft: isActive ? "2px solid var(--accent)" : "2px solid transparent",
                borderRadius: "0 4px 4px 0",
                padding: "8px 14px",
                textAlign: "left",
                fontSize: 13,
                color: isActive ? "var(--text-primary)" : "var(--text-secondary)",
                cursor: "pointer",
                transition: "all 0.15s",
              }}
            >
              {tab.label}
            </button>
          );
        })}
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "row", gap: 4, borderBottom: "1px solid var(--border)" }}>
      {tabs.map((tab) => {
        const isActive = tab.id === activeTab;
        return (
          <button
            key={tab.id}
            onClick={() => onChange(tab.id)}
            style={{
              background: "transparent",
              border: "none",
              borderBottom: isActive ? "2px solid var(--accent)" : "2px solid transparent",
              padding: "8px 14px",
              fontSize: 13,
              color: isActive ? "var(--text-primary)" : "var(--text-secondary)",
              cursor: "pointer",
              marginBottom: -1,
              transition: "color 0.15s, border-color 0.15s",
            }}
          >
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}
