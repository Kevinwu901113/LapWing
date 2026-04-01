import { Search } from "lucide-react";

type SearchInputProps = {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  className?: string;
};

export default function SearchInput({ value, onChange, placeholder = "搜索…", className }: SearchInputProps) {
  return (
    <div style={{ position: "relative", display: "inline-flex", alignItems: "center" }} className={className}>
      <Search
        size={14}
        style={{
          position: "absolute",
          left: 10,
          color: "var(--text-muted)",
          pointerEvents: "none",
        }}
      />
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        style={{
          background: "var(--bg-card)",
          border: "1px solid var(--border)",
          borderRadius: 6,
          padding: "6px 10px 6px 30px",
          fontSize: 13,
          color: "var(--text-primary)",
          outline: "none",
          width: "100%",
        }}
      />
    </div>
  );
}
