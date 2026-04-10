interface Props {
  label: string;
  value: string | number;
}

export function MetricCard({ label, value }: Props) {
  return (
    <div className="bg-surface border border-surface-border rounded-lg p-4">
      <div className="text-2xl font-semibold text-text-accent">{value}</div>
      <div className="text-xs text-text-secondary mt-1">{label}</div>
    </div>
  );
}
