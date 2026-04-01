import { useEffect, useState, useMemo } from "react";
import TabBar from "../components/TabBar";
import SearchInput from "../components/SearchInput";
import MemoryItem from "../components/MemoryItem";
import Pagination from "../components/Pagination";
import MarkdownEditor from "../components/MarkdownEditor";
import {
  getChats,
  getMemory,
  deleteMemory,
  getInterests,
  getPersonaFiles,
  updatePersonaFile,
  getConversationSummaries,
  getKnowledgeNotes,
  deleteKnowledgeNote,
  type ChatSummary,
  type MemoryItem as MemoryItemType,
  type InterestItem,
  type ConversationSummary,
  type KnowledgeNote,
} from "../api";

const TABS = [
  { id: "entries", label: "记忆条目" },
  { id: "identity", label: "身份笔记" },
  { id: "interests", label: "兴趣图谱" },
  { id: "summaries", label: "对话摘要" },
  { id: "knowledge", label: "知识笔记" },
];

const PAGE_SIZE = 15;

export default function MemoryPage() {
  const [activeTab, setActiveTab] = useState("entries");
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [chatId, setChatId] = useState("");

  // Tab 1: entries
  const [memories, setMemories] = useState<MemoryItemType[]>([]);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);

  // Tab 2: identity
  const [kevinContent, setKevinContent] = useState("");
  const [selfContent, setSelfContent] = useState("");
  const [kevinSaved, setKevinSaved] = useState(false);
  const [selfSaved, setSelfSaved] = useState(false);

  // Tab 3: interests
  const [interests, setInterests] = useState<InterestItem[]>([]);

  // Tab 4: summaries
  const [summaries, setSummaries] = useState<ConversationSummary[]>([]);
  const [expandedSummary, setExpandedSummary] = useState<string | null>(null);

  // Tab 5: knowledge
  const [knowledgeNotes, setKnowledgeNotes] = useState<KnowledgeNote[]>([]);
  const [expandedNote, setExpandedNote] = useState<string | null>(null);

  // Load chats on mount
  useEffect(() => {
    void getChats().then((c) => {
      setChats(c);
      if (c.length > 0) setChatId(c[0].chat_id);
    });
  }, []);

  // Load per-chat data when chatId changes
  useEffect(() => {
    if (!chatId) return;
    void getMemory(chatId).then((r) => setMemories(r.items));
    void getInterests(chatId).then((r) => setInterests(r.items));
  }, [chatId]);

  // Load global data on mount
  useEffect(() => {
    void getPersonaFiles().then((files) => {
      setKevinContent(files["kevin"] ?? "");
      setSelfContent(files["self"] ?? "");
    });
    void getConversationSummaries().then((r) => setSummaries(r.items));
    void getKnowledgeNotes().then((r) => setKnowledgeNotes(r.items));
  }, []);

  // Filtered + paginated memories
  const filteredMemories = useMemo(() => {
    return memories.filter((m) => {
      const q = search.toLowerCase();
      return (
        !search ||
        m.fact_key.toLowerCase().includes(q) ||
        m.fact_value.toLowerCase().includes(q)
      );
    });
  }, [memories, search]);

  const totalPages = Math.max(1, Math.ceil(filteredMemories.length / PAGE_SIZE));
  const pagedMemories = filteredMemories.slice(
    (page - 1) * PAGE_SIZE,
    page * PAGE_SIZE,
  );

  async function handleDeleteMemory(factKey: string) {
    await deleteMemory(chatId, factKey);
    const r = await getMemory(chatId);
    setMemories(r.items);
  }

  async function handleSaveKevin() {
    await updatePersonaFile("kevin", kevinContent);
    setKevinSaved(true);
    setTimeout(() => setKevinSaved(false), 2000);
  }

  async function handleSaveSelf() {
    await updatePersonaFile("self", selfContent);
    setSelfSaved(true);
    setTimeout(() => setSelfSaved(false), 2000);
  }

  async function handleDeleteKnowledge(topic: string) {
    await deleteKnowledgeNote(topic);
    const r = await getKnowledgeNotes();
    setKnowledgeNotes(r.items);
  }

  const chatSelector = (
    <select
      value={chatId}
      onChange={(e) => setChatId(e.target.value)}
      style={{
        fontSize: 12,
        padding: "4px 8px",
        borderRadius: "var(--radius-sm)",
        background: "var(--bg-card)",
        border: "1px solid var(--border)",
        color: "var(--text-primary)",
      }}
    >
      {chats.map((c) => (
        <option key={c.chat_id} value={c.chat_id}>
          {c.chat_id}
        </option>
      ))}
    </select>
  );

  return (
    <div className="tab-page animate-in">
      <div className="page-header">
        <h1 className="page-title">记忆</h1>
      </div>

      <TabBar
        tabs={TABS}
        activeTab={activeTab}
        onChange={(tab) => {
          setActiveTab(tab);
          setPage(1);
        }}
      />

      <div className="tab-content">
        {/* Tab 1: 记忆条目 */}
        {activeTab === "entries" && (
          <div>
            <div
              style={{
                display: "flex",
                gap: 10,
                marginBottom: 16,
                alignItems: "center",
              }}
            >
              {chatSelector}
              <SearchInput
                value={search}
                onChange={(v) => {
                  setSearch(v);
                  setPage(1);
                }}
                placeholder="搜索记忆…"
              />
            </div>

            {pagedMemories.length === 0 ? (
              <p className="empty-hint">暂无记忆条目</p>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {pagedMemories.map((m) => (
                  <MemoryItem
                    key={m.fact_key}
                    factKey={m.fact_key}
                    factValue={m.fact_value}
                    createdAt={m.updated_at ?? undefined}
                    onDelete={() => void handleDeleteMemory(m.fact_key)}
                  />
                ))}
              </div>
            )}

            <div style={{ marginTop: 16 }}>
              <Pagination page={page} totalPages={totalPages} onChange={setPage} />
            </div>
          </div>
        )}

        {/* Tab 2: 身份笔记 */}
        {activeTab === "identity" && (
          <div
            style={{
              display: "flex",
              gap: 20,
              height: "calc(100vh - 260px)",
            }}
          >
            {/* KEVIN.md */}
            <div
              style={{
                flex: 1,
                display: "flex",
                flexDirection: "column",
                gap: 8,
              }}
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                }}
              >
                <span className="section-title">KEVIN.md</span>
                <button
                  className="btn btn-primary btn-sm"
                  onClick={() => void handleSaveKevin()}
                >
                  {kevinSaved ? "✓ 已保存" : "保存"}
                </button>
              </div>
              <MarkdownEditor
                value={kevinContent}
                onChange={setKevinContent}
                height="100%"
              />
            </div>

            {/* SELF.md */}
            <div
              style={{
                flex: 1,
                display: "flex",
                flexDirection: "column",
                gap: 8,
              }}
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                }}
              >
                <span className="section-title">SELF.md</span>
                <button
                  className="btn btn-primary btn-sm"
                  onClick={() => void handleSaveSelf()}
                >
                  {selfSaved ? "✓ 已保存" : "保存"}
                </button>
              </div>
              <MarkdownEditor
                value={selfContent}
                onChange={setSelfContent}
                height="100%"
              />
            </div>
          </div>
        )}

        {/* Tab 3: 兴趣图谱 */}
        {activeTab === "interests" && (
          <div>
            <div style={{ marginBottom: 12 }}>{chatSelector}</div>
            {interests.length === 0 ? (
              <p className="empty-hint">暂无兴趣记录</p>
            ) : (
              <div
                style={{
                  display: "flex",
                  flexWrap: "wrap",
                  gap: 10,
                  padding: "16px 0",
                }}
              >
                {(() => {
                  const weights = interests.map((i) => i.weight);
                  const minW = Math.min(...weights);
                  const maxW = Math.max(...weights);
                  const range = maxW - minW || 1;
                  return interests.map((item) => {
                    const normalized = (item.weight - minW) / range;
                    const size = 12 + normalized * 10;
                    const opacity = 0.5 + normalized * 0.5;
                    return (
                      <div
                        key={item.topic}
                        title={`${item.topic} (权重: ${item.weight.toFixed(2)})`}
                        style={{
                          padding: "6px 12px",
                          borderRadius: 99,
                          background: "var(--accent-dim)",
                          border: "1px solid var(--accent-border)",
                          color: "var(--accent)",
                          fontSize: size,
                          opacity,
                          cursor: "default",
                          userSelect: "none",
                        }}
                      >
                        {item.topic}
                      </div>
                    );
                  });
                })()}
              </div>
            )}
          </div>
        )}

        {/* Tab 4: 对话摘要 */}
        {activeTab === "summaries" && (
          <div className="timeline">
            {summaries.length === 0 ? (
              <p className="empty-hint">暂无对话摘要</p>
            ) : (
              summaries.map((s) => (
                <div key={s.filename} className="timeline-item">
                  <div className="timeline-date">{s.date}</div>
                  <div className="timeline-content">
                    {expandedSummary === s.filename ? (
                      <>
                        <p
                          style={{
                            margin: 0,
                            fontSize: 13,
                            whiteSpace: "pre-wrap",
                          }}
                        >
                          {s.content}
                        </p>
                        <button
                          className="btn-icon"
                          style={{ fontSize: 12, marginTop: 8 }}
                          onClick={() => setExpandedSummary(null)}
                        >
                          收起
                        </button>
                      </>
                    ) : (
                      <>
                        <p style={{ margin: 0, fontSize: 13 }}>
                          {s.content.length > 200
                            ? s.content.slice(0, 200) + "…"
                            : s.content}
                        </p>
                        {s.content.length > 200 && (
                          <button
                            className="btn-icon"
                            style={{ fontSize: 12, marginTop: 4 }}
                            onClick={() => setExpandedSummary(s.filename)}
                          >
                            展开
                          </button>
                        )}
                      </>
                    )}
                  </div>
                </div>
              ))
            )}
          </div>
        )}

        {/* Tab 5: 知识笔记 */}
        {activeTab === "knowledge" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {knowledgeNotes.length === 0 ? (
              <p className="empty-hint">暂无知识笔记</p>
            ) : (
              knowledgeNotes.map((note) => (
                <div key={note.topic} className="card">
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "flex-start",
                      gap: 10,
                    }}
                  >
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div
                        style={{
                          fontWeight: 600,
                          fontSize: 13,
                          color: "var(--text-primary)",
                          marginBottom: 4,
                        }}
                      >
                        {note.topic}
                      </div>
                      {expandedNote === note.topic ? (
                        <p
                          style={{
                            margin: 0,
                            fontSize: 13,
                            whiteSpace: "pre-wrap",
                            color: "var(--text-secondary)",
                          }}
                        >
                          {note.content}
                        </p>
                      ) : (
                        <p
                          style={{
                            margin: 0,
                            fontSize: 13,
                            color: "var(--text-secondary)",
                          }}
                        >
                          {note.content.length > 100
                            ? note.content.slice(0, 100) + "…"
                            : note.content}
                        </p>
                      )}
                      <div
                        style={{
                          display: "flex",
                          gap: 12,
                          marginTop: 6,
                          alignItems: "center",
                        }}
                      >
                        <span
                          style={{ fontSize: 11, color: "var(--text-muted)" }}
                        >
                          {new Date(note.updated_at).toLocaleDateString(
                            "zh-CN",
                          )}
                        </span>
                        <button
                          className="btn-icon"
                          style={{ fontSize: 12 }}
                          onClick={() =>
                            setExpandedNote(
                              expandedNote === note.topic ? null : note.topic,
                            )
                          }
                        >
                          {expandedNote === note.topic ? "收起" : "展开"}
                        </button>
                      </div>
                    </div>
                    <button
                      className="btn btn-danger btn-sm"
                      onClick={() => void handleDeleteKnowledge(note.topic)}
                    >
                      删除
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
}
