type HeatmapBarProps = {
  segments: number[];
  label: string;
  enabled?: boolean;
};

export default function HeatmapBar({ segments, label, enabled = true }: HeatmapBarProps) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        opacity: enabled ? 1 : 0.4,
      }}
    >
      <span
        style={{
          width: 120,
          flexShrink: 0,
          fontSize: 12,
          color: "var(--text-secondary)",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {label}
      </span>
      <div style={{ display: "flex", gap: 4 }}>
        {segments.map((intensity, i) => (
          <div
            key={i}
            style={{
              width: 12,
              height: 20,
              borderRadius: 2,
              background:
                intensity === 0
                  ? "var(--bg-hover, rgba(255,255,255,0.05))"
                  : `rgba(168, 212, 240, ${Math.min(intensity, 1)})`,
            }}
            title={`Hour ${i}: ${(intensity * 100).toFixed(0)}%`}
          />
        ))}
      </div>
    </div>
  );
}
