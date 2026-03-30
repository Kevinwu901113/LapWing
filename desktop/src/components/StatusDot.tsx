export default function StatusDot({ online }: { online: boolean }) {
  return (
    <span
      className={`status-dot ${online ? "status-dot--online" : "status-dot--offline"}`}
    />
  );
}
