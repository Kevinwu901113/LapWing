import { useEffect, useState } from "react";

import {
  API_BASE,
  deleteMemory,
  DesktopEvent,
  evolvePrompt,
  getChats,
  getInterests,
  getLearnings,
  getMemory,
  getStatus,
  InterestItem,
  LearningItem,
  MemoryItem,
  reloadPersona,
  StatusResponse,
  ChatSummary,
} from "./api";

function formatDate(value: string | null) {
  if (!value) {
    return "暂无";
  }
  return new Date(value).toLocaleString("zh-CN");
}

export default function App() {
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [selectedChatId, setSelectedChatId] = useState("");
  const [interests, setInterests] = useState<InterestItem[]>([]);
  const [memoryItems, setMemoryItems] = useState<MemoryItem[]>([]);
  const [learnings, setLearnings] = useState<LearningItem[]>([]);
  const [events, setEvents] = useState<DesktopEvent[]>([]);
  const [eventConnected, setEventConnected] = useState(false);
  const [busyAction, setBusyAction] = useState<"reload" | "evolve" | null>(null);

  useEffect(() => {
    void loadOverview();
    void loadLearnings();

    if ("Notification" in window && Notification.permission === "default") {
      void Notification.requestPermission();
    }

    const stream = new EventSource(`${API_BASE}/api/events/stream`);
    stream.onopen = () => setEventConnected(true);
    stream.onerror = () => setEventConnected(false);
    stream.onmessage = (message) => {
      const event = JSON.parse(message.data) as DesktopEvent;
      setEvents((previous) => [event, ...previous].slice(0, 5));

      if ("Notification" in window && Notification.permission === "granted") {
        const title = event.type === "interest_proactive" ? "Lapwing 主动分享" : "Lapwing 主动消息";
        const suffix = event.payload.topic ? `\n主题：${event.payload.topic}` : "";
        new Notification(title, {
          body: `${event.payload.text}${suffix}`,
        });
      }
    };

    return () => {
      stream.close();
    };
  }, []);

  useEffect(() => {
    if (!selectedChatId) {
      setInterests([]);
      setMemoryItems([]);
      return;
    }
    void loadChatData(selectedChatId);
  }, [selectedChatId]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      void loadOverview(false);
      if (selectedChatId) {
        void loadChatData(selectedChatId);
      }
    }, 30000);

    return () => {
      window.clearInterval(timer);
    };
  }, [selectedChatId]);

  async function loadOverview(selectDefaultChat = true) {
    const [nextStatus, nextChats] = await Promise.all([getStatus(), getChats()]);
    setStatus(nextStatus);
    setChats(nextChats);

    if (selectDefaultChat && !selectedChatId && nextChats.length > 0) {
      setSelectedChatId(nextChats[0].chat_id);
    }
  }

  async function loadChatData(chatId: string) {
    const [interestResponse, memoryResponse] = await Promise.all([
      getInterests(chatId),
      getMemory(chatId),
    ]);
    setInterests(interestResponse.items);
    setMemoryItems(memoryResponse.items);
  }

  async function loadLearnings() {
    const response = await getLearnings();
    setLearnings(response.items);
  }

  async function handleDeleteMemory(factKey: string) {
    if (!selectedChatId) {
      return;
    }
    await deleteMemory(selectedChatId, factKey);
    await loadChatData(selectedChatId);
  }

  async function handleReload() {
    setBusyAction("reload");
    try {
      await reloadPersona();
    } finally {
      setBusyAction(null);
    }
  }

  async function handleEvolve() {
    setBusyAction("evolve");
    try {
      await evolvePrompt();
    } finally {
      setBusyAction(null);
    }
  }

  return (
    <div className="shell">
      <header className="hero">
        <div>
          <p className="eyebrow">Lapwing Desktop</p>
          <h1>本地观测台</h1>
          <p className="subtitle">
            连接 Telegram 后端，查看记忆、兴趣、学习日志和主动消息。
          </p>
        </div>
        <div className="hero-actions">
          <button onClick={handleReload} disabled={busyAction !== null}>
            {busyAction === "reload" ? "重载中..." : "重载人格"}
          </button>
          <button className="secondary" onClick={handleEvolve} disabled={busyAction !== null}>
            {busyAction === "evolve" ? "进化中..." : "触发进化"}
          </button>
        </div>
      </header>

      <section className="toolbar">
        <label>
          当前 Chat
          <select
            value={selectedChatId}
            onChange={(event) => setSelectedChatId(event.target.value)}
          >
            {chats.length === 0 ? <option value="">暂无 chat</option> : null}
            {chats.map((chat) => (
              <option key={chat.chat_id} value={chat.chat_id}>
                {chat.chat_id}
              </option>
            ))}
          </select>
        </label>
        <div className="status-pill">
          <span className={status?.online ? "dot online" : "dot offline"} />
          后端 {status?.online ? "在线" : "离线"}
        </div>
        <div className="status-pill">
          <span className={eventConnected ? "dot online" : "dot offline"} />
          事件流 {eventConnected ? "已连接" : "未连接"}
        </div>
      </section>

      <main className="grid">
        <section className="panel">
          <div className="panel-head">
            <h2>状态</h2>
          </div>
          <div className="stats">
            <article>
              <span>Chat 数量</span>
              <strong>{status?.chat_count ?? 0}</strong>
            </article>
            <article>
              <span>最后活跃</span>
              <strong>{formatDate(status?.last_interaction ?? null)}</strong>
            </article>
            <article>
              <span>服务启动</span>
              <strong>{formatDate(status?.started_at ?? null)}</strong>
            </article>
          </div>
        </section>

        <section className="panel">
          <div className="panel-head">
            <h2>兴趣图谱</h2>
          </div>
          <div className="interest-list">
            {interests.length === 0 ? <p className="empty">这个 chat 还没有明显兴趣记录。</p> : null}
            {interests.map((item) => (
              <article key={item.topic} className="interest-item">
                <div className="interest-row">
                  <span>{item.topic}</span>
                  <strong>{item.weight.toFixed(1)}</strong>
                </div>
                <div className="bar">
                  <div
                    className="bar-fill"
                    style={{ width: `${Math.min(item.weight * 12, 100)}%` }}
                  />
                </div>
              </article>
            ))}
          </div>
        </section>

        <section className="panel span-2">
          <div className="panel-head">
            <h2>记忆管理</h2>
          </div>
          <div className="memory-list">
            {memoryItems.length === 0 ? <p className="empty">当前没有可见记忆。</p> : null}
            {memoryItems.map((item) => (
              <article key={item.fact_key} className="memory-item">
                <div>
                  <p className="memory-key">
                    #{item.index} [{item.fact_key}]
                  </p>
                  <p className="memory-value">{item.fact_value}</p>
                  <span className="muted">更新于 {formatDate(item.updated_at)}</span>
                </div>
                <button className="danger" onClick={() => void handleDeleteMemory(item.fact_key)}>
                  删除
                </button>
              </article>
            ))}
          </div>
        </section>

        <section className="panel">
          <div className="panel-head">
            <h2>主动消息</h2>
          </div>
          <div className="event-list">
            {events.length === 0 ? <p className="empty">等待来自 SSE 的主动消息事件。</p> : null}
            {events.map((event, index) => (
              <article key={`${event.timestamp}-${index}`} className="event-item">
                <span className="event-type">{event.type}</span>
                <p>{event.payload.text}</p>
                <span className="muted">
                  {event.payload.chat_id} · {formatDate(event.timestamp)}
                </span>
              </article>
            ))}
          </div>
        </section>

        <section className="panel span-2">
          <div className="panel-head">
            <h2>学习日志</h2>
          </div>
          <div className="learning-list">
            {learnings.length === 0 ? <p className="empty">`data/learnings/` 里还没有日志。</p> : null}
            {learnings.map((item) => (
              <article key={item.filename} className="learning-item">
                <div className="learning-head">
                  <strong>{item.date}</strong>
                  <span className="muted">{formatDate(item.updated_at)}</span>
                </div>
                <pre>{item.content}</pre>
              </article>
            ))}
          </div>
        </section>
      </main>
    </div>
  );
}
