import { useEffect, useState } from "react";
import TabBar from "../components/TabBar";
import MarkdownEditor from "../components/MarkdownEditor";
import {
  getPersonaFiles,
  updatePersonaFile,
  reloadPersona,
  evolvePrompt,
  getLearnings,
  getChangelog,
  type LearningItem,
  type ChangelogEntry,
} from "../api";

// ── Tabs ────────────────────────────────────────────────────────────────────

const TABS = [
  { id: "soul", label: "核心人格" },
  { id: "voice", label: "说话方式" },
  { id: "constitution", label: "宪法" },
  { id: "changelog", label: "进化历史" },
  { id: "journal", label: "自省日志" },
];

// ── Shared editor+sidebar layout ─────────────────────────────────────────────

type PersonaEditorLayoutProps = {
  filename: string;
  content: string;
  onChange: (v: string) => void;
  readOnly?: boolean;
  onSave?: () => Promise<void>;
  saveLabel?: string;
  extraActions?: React.ReactNode;
  sidebarNotice?: React.ReactNode;
};

function PersonaEditorLayout({
  filename,
  content,
  onChange,
  readOnly = false,
  onSave,
  saveLabel = "保存并重载",
  extraActions,
  sidebarNotice,
}: PersonaEditorLayoutProps) {
  const [saving, setSaving] = useState(false);
  const [savedMsg, setSavedMsg] = useState("");

  async function handleSave() {
    if (!onSave) return;
    setSaving(true);
    try {
      await onSave();
      setSavedMsg("✓ 已保存");
      setTimeout(() => setSavedMsg(""), 2000);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="editor-layout">
      <div className="editor-main">
        <MarkdownEditor
          value={content}
          onChange={onChange}
          readOnly={readOnly}
          height="100%"
        />
      </div>
      <div className="editor-sidebar">
        <div className="card" style={{ padding: "14px 16px" }}>
          <p className="section-title" style={{ marginBottom: 8 }}>文件</p>
          <code style={{ fontSize: 12, color: "var(--text-secondary)" }}>{filename}</code>
        </div>

        {sidebarNotice}

        {onSave && (
          <button
            className="btn btn-primary btn-sm"
            onClick={() => void handleSave()}
            disabled={saving}
          >
            {saving ? "保存中…" : saveLabel}
          </button>
        )}

        {savedMsg && (
          <p style={{ fontSize: 12, color: "var(--accent)", margin: 0 }}>{savedMsg}</p>
        )}

        {extraActions}
      </div>
    </div>
  );
}

// ── Main page ────────────────────────────────────────────────────────────────

export default function PersonaPage() {
  const [activeTab, setActiveTab] = useState("soul");

  // Editor file contents
  const [soul, setSoul] = useState("");
  const [voice, setVoice] = useState("");
  const [constitution, setConstitution] = useState("");
  const [constitutionLocked, setConstitutionLocked] = useState(true);

  // Data for read-only tabs
  const [changelog, setChangelog] = useState<ChangelogEntry[]>([]);
  const [learnings, setLearnings] = useState<LearningItem[]>([]);

  // Evolve state
  const [evolving, setEvolving] = useState(false);
  const [evolveMsg, setEvolveMsg] = useState("");

  // Expandable rows
  const [expandedChangelog, setExpandedChangelog] = useState<string | null>(null);
  const [expandedJournal, setExpandedJournal] = useState<string | null>(null);

  useEffect(() => {
    void Promise.allSettled([
      getPersonaFiles().then((files) => {
        setSoul(files.soul ?? "");
        setVoice(files.voice ?? "");
        setConstitution(files.constitution ?? "");
      }),
      getChangelog().then((r) => setChangelog(r.entries)),
      getLearnings().then((r) => setLearnings(r.items)),
    ]);
  }, []);

  async function saveAndReload(name: string, content: string) {
    await updatePersonaFile(name, content);
    await reloadPersona();
  }

  async function handleEvolve() {
    setEvolving(true);
    setEvolveMsg("");
    try {
      const result = await evolvePrompt();
      if (result.success) {
        setEvolveMsg(result.summary ?? "进化完成");
      } else {
        setEvolveMsg(result.error ?? "进化失败");
      }
    } catch {
      setEvolveMsg("进化请求失败");
    } finally {
      setEvolving(false);
    }
  }

  return (
    <div className="tab-page animate-in">
      <div className="page-header">
        <h1 className="page-title">人格</h1>
      </div>

      <TabBar tabs={TABS} activeTab={activeTab} onChange={setActiveTab} />

      <div className="tab-content">
        {/* ── 核心人格 ── */}
        {activeTab === "soul" && (
          <PersonaEditorLayout
            filename="soul.md"
            content={soul}
            onChange={setSoul}
            onSave={() => saveAndReload("soul", soul)}
            extraActions={
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <button
                  className="btn btn-sm"
                  style={{ background: "var(--bg-surface)", border: "1px solid var(--border)" }}
                  onClick={() => void handleEvolve()}
                  disabled={evolving}
                >
                  {evolving ? "进化中…" : "触发进化"}
                </button>
                {evolveMsg && (
                  <p style={{ fontSize: 12, color: "var(--text-secondary)", margin: 0, whiteSpace: "pre-wrap" }}>
                    {evolveMsg}
                  </p>
                )}
              </div>
            }
          />
        )}

        {/* ── 说话方式 ── */}
        {activeTab === "voice" && (
          <PersonaEditorLayout
            filename="voice.md"
            content={voice}
            onChange={setVoice}
            onSave={() => saveAndReload("voice", voice)}
          />
        )}

        {/* ── 宪法 ── */}
        {activeTab === "constitution" && (
          <PersonaEditorLayout
            filename="constitution.md"
            content={constitution}
            onChange={setConstitution}
            readOnly={constitutionLocked}
            onSave={constitutionLocked ? undefined : () => saveAndReload("constitution", constitution)}
            sidebarNotice={
              <div className="card" style={{ padding: "12px 16px" }}>
                {constitutionLocked ? (
                  <>
                    <p style={{ fontSize: 12, color: "var(--text-muted)", margin: "0 0 8px" }}>
                      🔒 只读模式
                    </p>
                    <button
                      className="btn btn-sm btn-danger"
                      onClick={() => setConstitutionLocked(false)}
                    >
                      解锁编辑
                    </button>
                  </>
                ) : (
                  <p style={{ fontSize: 12, color: "var(--amber)", margin: 0 }}>
                    已解锁编辑
                  </p>
                )}
              </div>
            }
          />
        )}

        {/* ── 进化历史 ── */}
        {activeTab === "changelog" && (
          <div>
            {changelog.length === 0 ? (
              <p className="empty-hint">暂无进化记录</p>
            ) : (
              <div className="timeline">
                {changelog.map((entry) => {
                  const key = entry.date;
                  const expanded = expandedChangelog === key;
                  return (
                    <div key={key} className="timeline-item">
                      <div className="timeline-date">{entry.date}</div>
                      <div
                        className="timeline-content"
                        style={{ cursor: "pointer" }}
                        onClick={() => setExpandedChangelog(expanded ? null : key)}
                      >
                        <p style={{ margin: 0, fontSize: 13, color: "var(--text-primary)" }}>
                          {entry.summary}
                        </p>
                        {expanded && (
                          <pre
                            style={{
                              marginTop: 10,
                              fontSize: 12,
                              color: "var(--text-secondary)",
                              whiteSpace: "pre-wrap",
                              wordBreak: "break-word",
                            }}
                          >
                            {entry.content}
                          </pre>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {/* ── 自省日志 ── */}
        {activeTab === "journal" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {learnings.length === 0 ? (
              <p className="empty-hint">暂无自省日志</p>
            ) : (
              learnings.map((item) => {
                const key = item.filename;
                const expanded = expandedJournal === key;
                const preview = item.content.slice(0, 150);
                const hasMore = item.content.length > 150;
                return (
                  <div
                    key={key}
                    className="card"
                    style={{ cursor: "pointer", padding: "14px 16px" }}
                    onClick={() => setExpandedJournal(expanded ? null : key)}
                  >
                    <p
                      className="section-title"
                      style={{ marginBottom: 6, fontSize: 12, color: "var(--text-muted)" }}
                    >
                      {item.date}
                    </p>
                    <pre
                      style={{
                        margin: 0,
                        fontSize: 12,
                        color: "var(--text-secondary)",
                        whiteSpace: "pre-wrap",
                        wordBreak: "break-word",
                      }}
                    >
                      {expanded ? item.content : preview + (hasMore && !expanded ? "…" : "")}
                    </pre>
                  </div>
                );
              })
            )}
          </div>
        )}
      </div>
    </div>
  );
}
