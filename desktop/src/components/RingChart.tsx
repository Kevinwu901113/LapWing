type RingChartProps = {
  value: number;
  max: number;
  label: string;
  color?: string;
  size?: number;
  unit?: string;
};

export default function RingChart({
  value,
  max,
  label,
  color = "var(--accent)",
  size = 100,
  unit = "",
}: RingChartProps) {
  const strokeWidth = 8;
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const pct = max > 0 ? Math.min(value / max, 1) : 0;
  const dashoffset = circumference * (1 - pct);
  const cx = size / 2;
  const cy = size / 2;

  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 6 }}>
      <svg width={size} height={size} style={{ transform: "rotate(-90deg)" }}>
        <circle
          cx={cx}
          cy={cy}
          r={radius}
          fill="none"
          stroke="var(--border)"
          strokeWidth={strokeWidth}
          strokeDasharray={circumference}
          strokeDashoffset={0}
        />
        <circle
          cx={cx}
          cy={cy}
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth={strokeWidth}
          strokeDasharray={circumference}
          strokeDashoffset={dashoffset}
          strokeLinecap="round"
        />
        <text
          x={cx}
          y={cy - 6}
          textAnchor="middle"
          dominantBaseline="middle"
          style={{
            transform: "rotate(90deg)",
            transformOrigin: `${cx}px ${cy - 6}px`,
            fontSize: 18,
            fontWeight: 600,
            fill: "var(--text-primary)",
          }}
        >
          {value}{unit}
        </text>
        <text
          x={cx}
          y={cy + 12}
          textAnchor="middle"
          dominantBaseline="middle"
          style={{
            transform: "rotate(90deg)",
            transformOrigin: `${cx}px ${cy + 12}px`,
            fontSize: 11,
            fill: "var(--text-muted)",
          }}
        >
          {label}
        </text>
      </svg>
    </div>
  );
}
