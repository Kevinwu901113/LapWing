type StatCardProps = {
  label: string;
  value: string | number;
  sub?: string;
};

export default function StatCard({ label, value, sub }: StatCardProps) {
  return (
    <div className="stat-card">
      <span className="stat-card-label">{label}</span>
      <strong className="stat-card-value">{value}</strong>
      {sub && <span className="stat-card-sub">{sub}</span>}
    </div>
  );
}
