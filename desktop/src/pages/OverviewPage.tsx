import { useEffect, useState } from "react";
import { RefreshCw, Zap } from "lucide-react";
import {
  getStatus, getChats, reloadPersona, evolvePrompt,
  type StatusResponse, type ChatSummary,
} from "../api";

function formatDate(v: string | null) {
  return v ? new Date(v).toLocaleString("zh-CN") : "暂无";
}

export default function OverviewPage() {
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [busy, setBusy] = useState<"reload" | "evolve" | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const [s, c] = await Promise.all([getStatus(), getChats()]);
        if (!cancelled) { setStatus(s); setChats(c); }
      } catch {}
    }

    void load();
    const timer = setInterval(load, 30_000);
    return () => { cancelled = true; clearInterval(timer); };
  }, []);

  async function handleReload() {
    setBusy("reload");
    try { await reloadPersona(); } finally { setBusy(null); }
  }

  async function handleEvolve() {
    setBusy("evolve");
    try { await evolvePrompt(); } finally { setBusy(null); }
  }

  return (
    <div className="page">
      <header className="page-header animate-in">
        <div>
          <h1 className="page-title">总览</h1>
          <p className="page-subtitle">Lapwing 运行状态一览</p>
        </div>
        <div className="page-header-actions">
          <button className="btn btn-primary" onClick={handleReload} disabled={busy !== null}>
            <RefreshCw size={16} />
            {busy === "reload" ? "重载中…" : "重载人格"}
          </button>
          <button className="btn btn-ghost" onClick={handleEvolve} disabled={busy !== null}>
            <Zap size={16} />
            {busy === "evolve" ? "进化中…" : "触发进化"}
          </button>
        </div>
      </header>

      <div className="stat-grid-4 animate-in">
        {[
          { label: "Chat 数量", value: String(status?.chat_count ?? 0) },
          { label: "最后活跃", value: formatDate(status?.last_interaction ?? null) },
          { label: "服务启动", value: formatDate(status?.started_at ?? null) },
          { label: "后端状态", value: status?.online ? "在线" : "离线" },
        ].map((s) => (
          <div key={s.label} className="card">
            <p style={{ margin: 0, fontSize: 11, color: "var(--text-muted)" }}>{s.label}</p>
            <p style={{ margin: "4px 0 0", fontSize: 20, fontWeight: 600, color: "var(--text-primary)" }}>{s.value}</p>
          </div>
        ))}
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <p className="card-title">最近对话</p>
        {chats.length === 0 ? (
          <p className="empty-hint">暂无对话记录。</p>
        ) : (
          <div>
            {chats.slice(0, 8).map((chat) => (
              <div key={chat.chat_id} style={{ display: "flex", justifyContent: "space-between", padding: "6px 0", borderBottom: "1px solid var(--border)" }}>
                <span style={{ fontSize: 13, color: "var(--text-primary)" }}>{chat.chat_id}</span>
                <span style={{ fontSize: 12, color: "var(--text-muted)" }}>{formatDate(chat.last_interaction)}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
