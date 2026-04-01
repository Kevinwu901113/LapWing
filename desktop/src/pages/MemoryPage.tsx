import { useEffect, useState } from "react";
import { Trash2 } from "lucide-react";
import {
  getChats, getInterests, getMemory, deleteMemory,
  type ChatSummary, type InterestItem, type MemoryItem,
} from "../api";

function formatDate(v: string | null) {
  return v ? new Date(v).toLocaleString("zh-CN") : "暂无";
}

export default function MemoryPage() {
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [chatId, setChatId] = useState("");
  const [interests, setInterests] = useState<InterestItem[]>([]);
  const [memory, setMemory] = useState<MemoryItem[]>([]);

  useEffect(() => {
    void getChats().then((c) => {
      setChats(c);
      if (c.length > 0 && !chatId) setChatId(c[0].chat_id);
    });
  }, []);

  useEffect(() => {
    if (!chatId) return;
    void Promise.all([getInterests(chatId), getMemory(chatId)]).then(([i, m]) => {
      setInterests(i.items);
      setMemory(m.items);
    });
  }, [chatId]);

  async function handleDelete(factKey: string) {
    await deleteMemory(chatId, factKey);
    const res = await getMemory(chatId);
    setMemory(res.items);
  }

  return (
    <div className="page">
      <header className="page-header animate-in">
        <div>
          <h1 className="page-title">记忆</h1>
          <p className="page-subtitle">管理 Lapwing 的记忆和兴趣图谱</p>
        </div>
        <select
          value={chatId}
          onChange={(e) => setChatId(e.target.value)}
          style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: 4, padding: "4px 8px", fontSize: 13, color: "var(--text-primary)" }}
        >
          {chats.map((c) => (
            <option key={c.chat_id} value={c.chat_id}>{c.chat_id}</option>
          ))}
        </select>
      </header>

      <div className="stat-grid-2">
        <div className="card">
          <p className="card-title">兴趣图谱</p>
          {interests.length === 0 ? (
            <p className="empty-hint">暂无兴趣记录。</p>
          ) : (
            <div>
              {interests.map((item) => (
                <div key={item.topic} style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 0" }}>
                  <span style={{ flex: 1, fontSize: 13, color: "var(--text-primary)" }}>{item.topic}</span>
                  <span style={{ fontSize: 12, color: "var(--text-muted)" }}>{item.weight}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="card">
          <p className="card-title">记忆 ({memory.length})</p>
          {memory.length === 0 ? (
            <p className="empty-hint">当前没有可见记忆。</p>
          ) : (
            <div>
              {memory.map((item) => (
                <div key={item.fact_key} style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 0", borderBottom: "1px solid var(--border)" }}>
                  <div style={{ flex: 1 }}>
                    <p style={{ margin: 0, fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>#{item.index} [{item.fact_key}]</p>
                    <p style={{ margin: "2px 0 0", fontSize: 13, color: "var(--text-secondary)" }}>{item.fact_value}</p>
                    <span style={{ fontSize: 11, color: "var(--text-muted)" }}>更新于 {formatDate(item.updated_at)}</span>
                  </div>
                  <button
                    className="btn btn-danger btn-sm btn-icon"
                    onClick={() => void handleDelete(item.fact_key)}
                    title="删除"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
