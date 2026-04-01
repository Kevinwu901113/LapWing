import { useEffect, useRef, useState } from "react";
import { getRecentLogs } from "../api";
import type { LogLine } from "../api";
import { useLogStream } from "../hooks/useLogStream";
import SearchInput from "../components/SearchInput";

const LEVELS = ["全部", "DEBUG", "INFO", "WARNING", "ERROR"] as const;
type Level = (typeof LEVELS)[number];

function levelClass(level: string): string {
  switch (level.toUpperCase()) {
    case "DEBUG":
      return "log-level-DEBUG";
    case "INFO":
      return "log-level-INFO";
    case "WARNING":
      return "log-level-WARNING";
    case "ERROR":
      return "log-level-ERROR";
    default:
      return "";
  }
}

function formatLine(line: LogLine): string {
  const ts = line.timestamp ? line.timestamp.slice(11, 19) : "??:??:??";
  return `[${ts}] [${line.level.toUpperCase()}] ${line.logger} — ${line.message}`;
}

export default function LogsPage() {
  const [levelFilter, setLevelFilter] = useState<Level>("全部");
  const [moduleFilter, setModuleFilter] = useState("");
  const [search, setSearch] = useState("");
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);

  const { lines, live, setLive, clear } = useLogStream(levelFilter, moduleFilter);

  // Historical lines when not live
  const [histLines, setHistLines] = useState<LogLine[]>([]);

  useEffect(() => {
    if (!live) {
      void getRecentLogs(500, levelFilter !== "全部" ? levelFilter : undefined).then((r) =>
        setHistLines(r.lines),
      );
    }
  }, [live, levelFilter]);

  const allLines = live ? lines : histLines;

  // Client-side filtering
  const visible = allLines.filter((line) => {
    if (levelFilter !== "全部" && line.level.toUpperCase() !== levelFilter) return false;
    if (moduleFilter && !line.logger.includes(moduleFilter)) return false;
    if (search && !line.message.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  // Auto-scroll when live
  const logContainerRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (live && logContainerRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight;
    }
  }, [visible.length, live]);

  return (
    <div className="tab-page animate-in" style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 80px)" }}>
      <header className="page-header">
        <h1 className="page-title">日志</h1>
      </header>

      {/* Control bar */}
      <div
        style={{
          display: "inline-flex",
          flexWrap: "wrap",
          alignItems: "center",
          gap: 8,
          marginBottom: 12,
        }}
      >
        {/* Level dropdown */}
        <select
          value={levelFilter}
          onChange={(e) => setLevelFilter(e.target.value as Level)}
          style={{
            background: "var(--bg-card)",
            border: "1px solid var(--border)",
            borderRadius: 6,
            padding: "6px 10px",
            fontSize: 13,
            color: "var(--text-primary)",
            cursor: "pointer",
          }}
        >
          {LEVELS.map((l) => (
            <option key={l} value={l}>
              {l}
            </option>
          ))}
        </select>

        {/* Module filter */}
        <input
          type="text"
          value={moduleFilter}
          onChange={(e) => setModuleFilter(e.target.value)}
          placeholder="模块过滤…"
          style={{
            background: "var(--bg-card)",
            border: "1px solid var(--border)",
            borderRadius: 6,
            padding: "6px 10px",
            fontSize: 13,
            color: "var(--text-primary)",
            outline: "none",
            width: 160,
          }}
        />

        {/* Search */}
        <SearchInput value={search} onChange={setSearch} placeholder="搜索消息…" />

        {/* Live toggle */}
        <button
          className="btn btn-sm"
          onClick={() => setLive(!live)}
          style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
        >
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              background: live ? "var(--green, #4ade80)" : "var(--text-muted)",
              flexShrink: 0,
              animation: live ? "pulse 1.5s ease-in-out infinite" : "none",
            }}
          />
          {live ? "实时" : "历史"}
        </button>

        {/* Clear */}
        <button className="btn btn-sm" onClick={clear}>
          清空
        </button>
      </div>

      {/* Log area */}
      <div className="log-viewer" ref={logContainerRef}>
        {visible.length === 0 ? (
          <p className="empty-hint">暂无日志</p>
        ) : (
          visible.map((line, idx) => {
            const expanded = expandedIdx === idx;
            return (
              <div
                key={idx}
                className={`log-line${expanded ? " expanded" : ""}`}
                onClick={() => setExpandedIdx(expanded ? null : idx)}
              >
                <span className="log-ts">{line.timestamp ? line.timestamp.slice(11, 19) : ""}</span>
                <span className={`log-level ${levelClass(line.level)}`}>{line.level.toUpperCase()}</span>
                <span className="log-logger">{line.logger}</span>
                <span className={`log-message${expanded ? " wrap" : ""}`}>
                  {expanded ? formatLine(line).split(" — ").slice(1).join(" — ") : line.message}
                </span>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
