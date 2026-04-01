import { ChevronLeft, ChevronRight } from "lucide-react";

type PaginationProps = {
  page: number;
  totalPages: number;
  onChange: (page: number) => void;
};

function buildPageNumbers(page: number, totalPages: number): (number | "...")[] {
  if (totalPages <= 7) {
    return Array.from({ length: totalPages }, (_, i) => i + 1);
  }
  const pages: (number | "...")[] = [];
  if (page <= 4) {
    pages.push(1, 2, 3, 4, 5, "...", totalPages);
  } else if (page >= totalPages - 3) {
    pages.push(1, "...", totalPages - 4, totalPages - 3, totalPages - 2, totalPages - 1, totalPages);
  } else {
    pages.push(1, "...", page - 1, page, page + 1, "...", totalPages);
  }
  return pages;
}

export default function Pagination({ page, totalPages, onChange }: PaginationProps) {
  const pageNumbers = buildPageNumbers(page, totalPages);

  const btnStyle = (active: boolean, disabled: boolean): React.CSSProperties => ({
    minWidth: 32,
    height: 32,
    padding: "0 6px",
    border: "1px solid var(--border)",
    borderRadius: 4,
    background: active ? "var(--accent)" : "transparent",
    color: active ? "#fff" : disabled ? "var(--text-muted)" : "var(--text-secondary)",
    cursor: disabled ? "not-allowed" : "pointer",
    fontSize: 13,
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    opacity: disabled ? 0.5 : 1,
  });

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
      <button
        style={btnStyle(false, page <= 1)}
        disabled={page <= 1}
        onClick={() => onChange(page - 1)}
        aria-label="Previous"
      >
        <ChevronLeft size={14} />
      </button>

      {pageNumbers.map((p, i) =>
        p === "..." ? (
          <span key={`ellipsis-${i}`} style={{ padding: "0 4px", color: "var(--text-muted)", fontSize: 13 }}>
            …
          </span>
        ) : (
          <button
            key={p}
            style={btnStyle(p === page, false)}
            onClick={() => onChange(p as number)}
          >
            {p}
          </button>
        )
      )}

      <button
        style={btnStyle(false, page >= totalPages)}
        disabled={page >= totalPages}
        onClick={() => onChange(page + 1)}
        aria-label="Next"
      >
        <ChevronRight size={14} />
      </button>
    </div>
  );
}
