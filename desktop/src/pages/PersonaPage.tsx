import { useEffect, useState } from "react";
import { Sparkles, RefreshCw } from "lucide-react";
import {
  getLearnings, evolvePrompt, reloadPersona,
  type LearningItem,
} from "../api";

function formatDate(v: string) {
  return new Date(v).toLocaleString("zh-CN");
}

export default function PersonaPage() {
  const [learnings, setLearnings] = useState<LearningItem[]>([]);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    void getLearnings().then((r) => setLearnings(r.items));
  }, []);

  async function handleEvolve() {
    setBusy("evolve");
    try { await evolvePrompt(); } finally { setBusy(null); }
  }

  async function handleReload() {
    setBusy("reload");
    try { await reloadPersona(); } finally { setBusy(null); }
  }

  return (
    <div className="page">
      <header className="page-header animate-in">
        <div>
          <h1 className="page-title">人格</h1>
          <p className="page-subtitle">Lapwing 的自省日志和人格进化</p>
        </div>
        <div className="page-header-actions">
          <button className="btn btn-primary" onClick={handleEvolve} disabled={busy !== null}>
            <Sparkles size={16} />
            {busy === "evolve" ? "进化中…" : "触发进化"}
          </button>
          <button className="btn btn-ghost" onClick={handleReload} disabled={busy !== null}>
            <RefreshCw size={16} />
            {busy === "reload" ? "重载中…" : "重载人格"}
          </button>
        </div>
      </header>

      <div className="card">
        <p className="card-title">学习日志</p>
        {learnings.length === 0 ? (
          <p className="empty-hint">data/memory/journal/ 中暂无日志。</p>
        ) : (
          <div>
            {learnings.map((item) => (
              <div key={item.filename} style={{ padding: "10px 0", borderBottom: "1px solid var(--border)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                  <strong style={{ fontSize: 13, color: "var(--text-primary)" }}>{item.date}</strong>
                  <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{formatDate(item.updated_at)}</span>
                </div>
                <pre style={{ margin: 0, fontSize: 12, color: "var(--text-secondary)", whiteSpace: "pre-wrap" }}>{item.content}</pre>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
