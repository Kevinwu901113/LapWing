import { useEffect, useState } from "react";
import { Sparkles, RefreshCw } from "lucide-react";
import {
  getLearnings, evolvePrompt, reloadPersona,
  type LearningItem,
} from "../api";
import DataCard from "../components/DataCard";
import EmptyState from "../components/EmptyState";

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
          <button className="btn btn-soft" onClick={handleReload} disabled={busy !== null}>
            <RefreshCw size={16} />
            {busy === "reload" ? "重载中…" : "重载人格"}
          </button>
        </div>
      </header>

      <DataCard title="学习日志" className="stagger-1">
        {learnings.length === 0 ? (
          <EmptyState message="data/memory/journal/ 中暂无日志。" />
        ) : (
          <div className="list-stack">
            {learnings.map((item) => (
              <div key={item.filename} className="learning-entry">
                <div className="learning-entry-head">
                  <strong>{item.date}</strong>
                  <span className="list-row-muted">{formatDate(item.updated_at)}</span>
                </div>
                <pre className="learning-entry-body">{item.content}</pre>
              </div>
            ))}
          </div>
        )}
      </DataCard>
    </div>
  );
}
