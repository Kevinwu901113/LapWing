import { useEffect, useState } from "react";
import { RefreshCw, Zap } from "lucide-react";
import {
  getStatus, getChats, reloadPersona, evolvePrompt,
  type StatusResponse, type ChatSummary,
} from "../api";
import StatCard from "../components/StatCard";
import DataCard from "../components/DataCard";

function formatDate(v: string | null) {
  return v ? new Date(v).toLocaleString("zh-CN") : "暂无";
}

export default function OverviewPage() {
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [busy, setBusy] = useState<"reload" | "evolve" | null>(null);

  useEffect(() => {
    void Promise.all([getStatus(), getChats()]).then(([s, c]) => {
      setStatus(s);
      setChats(c);
    });
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
      {/* 页头 */}
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
          <button className="btn btn-soft" onClick={handleEvolve} disabled={busy !== null}>
            <Zap size={16} />
            {busy === "evolve" ? "进化中…" : "触发进化"}
          </button>
        </div>
      </header>

      {/* 状态卡片组 */}
      <div className="stat-grid animate-in stagger-1">
        <StatCard label="Chat 数量" value={status?.chat_count ?? 0} />
        <StatCard label="最后活跃" value={formatDate(status?.last_interaction ?? null)} />
        <StatCard label="服务启动" value={formatDate(status?.started_at ?? null)} />
        <StatCard
          label="后端状态"
          value={status?.online ? "在线" : "离线"}
        />
      </div>

      {/* 最近 Chat 列表 */}
      <DataCard title="最近对话" className="stagger-2">
        {chats.length === 0 ? (
          <p className="empty-state">暂无对话记录。</p>
        ) : (
          <div className="list-stack">
            {chats.slice(0, 8).map((chat) => (
              <div key={chat.chat_id} className="list-row">
                <span className="list-row-key">{chat.chat_id}</span>
                <span className="list-row-muted">{formatDate(chat.last_interaction)}</span>
              </div>
            ))}
          </div>
        )}
      </DataCard>
    </div>
  );
}
