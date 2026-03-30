import type { ReactNode } from "react";

type DataCardProps = {
  title: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
};

export default function DataCard({ title, actions, children, className = "" }: DataCardProps) {
  return (
    <section className={`data-card animate-in ${className}`}>
      <div className="data-card-head">
        <h2>{title}</h2>
        {actions && <div className="data-card-actions">{actions}</div>}
      </div>
      <div className="data-card-body">{children}</div>
    </section>
  );
}
