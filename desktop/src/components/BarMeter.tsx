type BarMeterProps = {
  label: string;
  value: number;
  max?: number;
  suffix?: string;
};

export default function BarMeter({ label, value, max = 10, suffix }: BarMeterProps) {
  const pct = Math.min((value / max) * 100, 100);
  return (
    <div className="bar-meter">
      <div className="bar-meter-row">
        <span>{label}</span>
        <strong>{value.toFixed(1)}{suffix}</strong>
      </div>
      <div className="bar-meter-track">
        <div className="bar-meter-fill" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
