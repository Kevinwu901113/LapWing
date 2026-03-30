import { useEffect, useState } from "react";
import { Trash2 } from "lucide-react";
import {
  getChats, getInterests, getMemory, deleteMemory,
  type ChatSummary, type InterestItem, type MemoryItem,
} from "../api";
import DataCard from "../components/DataCard";
import BarMeter from "../components/BarMeter";
import EmptyState from "../components/EmptyState";

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
          className="chat-selector"
          value={chatId}
          onChange={(e) => setChatId(e.target.value)}
        >
          {chats.map((c) => (
            <option key={c.chat_id} value={c.chat_id}>{c.chat_id}</option>
          ))}
        </select>
      </header>

      {/* 双列布局 */}
      <div className="two-col">
        {/* 兴趣图谱 */}
        <DataCard title="兴趣图谱" className="stagger-1">
          {interests.length === 0 ? (
            <EmptyState message="暂无兴趣记录。" />
          ) : (
            <div className="list-stack">
              {interests.map((item) => (
                <BarMeter
                  key={item.topic}
                  label={item.topic}
                  value={item.weight}
                  max={8}
                />
              ))}
            </div>
          )}
        </DataCard>

        {/* 记忆列表 */}
        <DataCard title={`记忆 (${memory.length})`} className="stagger-2">
          {memory.length === 0 ? (
            <EmptyState message="当前没有可见记忆。" />
          ) : (
            <div className="list-stack">
              {memory.map((item) => (
                <div key={item.fact_key} className="memory-row">
                  <div className="memory-row-content">
                    <p className="memory-row-key">#{item.index} [{item.fact_key}]</p>
                    <p className="memory-row-value">{item.fact_value}</p>
                    <span className="list-row-muted">
                      更新于 {formatDate(item.updated_at)}
                    </span>
                  </div>
                  <button
                    className="btn btn-danger-soft btn-sm btn-icon"
                    onClick={() => void handleDelete(item.fact_key)}
                    title="删除"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
            </div>
          )}
        </DataCard>
      </div>
    </div>
  );
}
