import { useEffect, useState, useCallback } from "react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Save, RotateCcw, FileText, History, ChevronDown, ChevronRight } from "lucide-react";
import {
  getIdentityFile, updateIdentityFile,
  getSoulHistory, getSoulDiff, rollbackSoul,
} from "@/lib/api-v2";
import type { SoulSnapshot } from "@/types/identity";

const IDENTITY_FILES = ["soul.md", "constitution.md", "voice.md"] as const;
const READONLY_FILES = new Set(["constitution.md"]);

function formatTs(ts: string): string {
  try {
    return new Date(ts).toLocaleString("zh-CN", {
      month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return ts;
  }
}

export default function IdentityPage() {
  const [selected, setSelected] = useState<string>("soul.md");
  const [content, setContent] = useState("");
  const [originalContent, setOriginalContent] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveResult, setSaveResult] = useState<{ ok: boolean; msg: string } | null>(null);

  // Soul history
  const [snapshots, setSnapshots] = useState<SoulSnapshot[]>([]);
  const [expandedSnapshot, setExpandedSnapshot] = useState<string | null>(null);
  const [diffContent, setDiffContent] = useState<string | null>(null);
  const [rolling, setRolling] = useState(false);

  const isReadonly = READONLY_FILES.has(selected);
  const hasChanges = content !== originalContent;

  const loadFile = useCallback(async (filename: string) => {
    try {
      const data = await getIdentityFile(filename);
      setContent(data.content);
      setOriginalContent(data.content);
      setSaveResult(null);
    } catch {
      setContent("");
      setOriginalContent("");
    }
  }, []);

  const loadHistory = useCallback(async () => {
    try {
      const data = await getSoulHistory();
      setSnapshots(data.snapshots ?? []);
    } catch {
      setSnapshots([]);
    }
  }, []);

  useEffect(() => { loadFile(selected); }, [selected, loadFile]);
  useEffect(() => { loadHistory(); }, [loadHistory]);

  const handleSelect = (filename: string) => {
    if (hasChanges && !confirm("Discard unsaved changes?")) return;
    setSelected(filename);
    setExpandedSnapshot(null);
    setDiffContent(null);
  };

  const handleSave = async () => {
    setSaving(true);
    setSaveResult(null);
    try {
      const result = await updateIdentityFile(selected, content);
      if (result.success) {
        setSaveResult({ ok: true, msg: "Saved" });
        setOriginalContent(content);
        if (selected === "soul.md") loadHistory();
      } else {
        setSaveResult({ ok: false, msg: result.reason ?? "Save failed" });
      }
    } catch (e) {
      setSaveResult({ ok: false, msg: `Error: ${e}` });
    }
    setSaving(false);
  };

  const handleViewDiff = async (snapshotId: string) => {
    if (expandedSnapshot === snapshotId) {
      setExpandedSnapshot(null);
      setDiffContent(null);
      return;
    }
    setExpandedSnapshot(snapshotId);
    try {
      const data = await getSoulDiff(snapshotId);
      setDiffContent(typeof data.diff === "string" ? data.diff : JSON.stringify(data.diff, null, 2));
    } catch {
      setDiffContent("Failed to load diff");
    }
  };

  const handleRollback = async (snapshotId: string) => {
    if (!confirm("Rollback soul.md to this snapshot?")) return;
    setRolling(true);
    try {
      const result = await rollbackSoul(snapshotId);
      if (result.success) {
        await loadFile("soul.md");
        await loadHistory();
        setExpandedSnapshot(null);
        setDiffContent(null);
      } else {
        alert(result.reason ?? "Rollback failed");
      }
    } catch (e) {
      alert(`Rollback error: ${e}`);
    }
    setRolling(false);
  };

  return (
    <div className="h-full flex flex-col">
      <div className="p-4 border-b border-surface-border">
        <h1 className="text-lg font-medium text-text-accent">Identity</h1>
      </div>

      <div className="flex-1 flex min-h-0">
        {/* Left sidebar: file list + soul history */}
        <div className="w-[220px] shrink-0 border-r border-surface-border flex flex-col">
          {/* File list */}
          <div className="p-2 space-y-0.5">
            {IDENTITY_FILES.map((f) => (
              <button
                key={f}
                onClick={() => handleSelect(f)}
                className={`flex items-center gap-2 w-full text-left px-3 py-1.5 rounded text-sm transition-colors ${
                  selected === f
                    ? "bg-surface-active text-text-accent"
                    : "text-text-secondary hover:bg-surface-hover"
                }`}
              >
                <FileText size={14} />
                <span>{f}</span>
                {READONLY_FILES.has(f) && (
                  <span className="text-[10px] text-text-muted ml-auto">readonly</span>
                )}
              </button>
            ))}
          </div>

          {/* Soul history */}
          {selected === "soul.md" && (
            <div className="flex-1 border-t border-surface-border flex flex-col min-h-0">
              <div className="flex items-center gap-1.5 px-3 py-2 text-xs text-text-muted">
                <History size={12} />
                <span>Soul History</span>
              </div>
              <ScrollArea className="flex-1">
                <div className="px-2 pb-2 space-y-1">
                  {snapshots.length === 0 ? (
                    <div className="text-[11px] text-text-muted text-center py-4">No snapshots</div>
                  ) : (
                    snapshots.map((s) => (
                      <div key={s.snapshot_id} className="bg-surface border border-surface-border rounded text-xs">
                        <button
                          onClick={() => handleViewDiff(s.snapshot_id)}
                          className="flex items-center gap-1.5 w-full text-left px-2 py-1.5 hover:bg-surface-hover rounded transition-colors"
                        >
                          {expandedSnapshot === s.snapshot_id ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
                          <span className="text-text-primary">{formatTs(s.timestamp)}</span>
                          <span className="text-text-muted ml-auto">{s.actor}</span>
                        </button>
                        {expandedSnapshot === s.snapshot_id && (
                          <div className="px-2 pb-2 border-t border-surface-border">
                            <div className="text-[10px] text-text-muted mt-1">
                              {s.trigger}
                              {s.diff_summary && ` — ${s.diff_summary}`}
                            </div>
                            {diffContent && (
                              <pre className="mt-1 text-[10px] text-text-secondary bg-void-50 rounded p-1.5 overflow-x-auto max-h-32 whitespace-pre-wrap">
                                {diffContent}
                              </pre>
                            )}
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() => handleRollback(s.snapshot_id)}
                              disabled={rolling}
                              className="mt-1.5 h-6 text-[10px] gap-1"
                            >
                              <RotateCcw size={10} /> Rollback
                            </Button>
                          </div>
                        )}
                      </div>
                    ))
                  )}
                </div>
              </ScrollArea>
            </div>
          )}
        </div>

        {/* Right: Editor */}
        <div className="flex-1 flex flex-col p-4 min-w-0">
          <Textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            disabled={isReadonly}
            className="flex-1 bg-void-50 border-surface-border text-text-primary font-mono text-sm resize-none"
          />
          <div className="flex items-center gap-3 mt-3">
            <Button
              onClick={handleSave}
              disabled={saving || isReadonly || !hasChanges}
              className="bg-lapwing text-void hover:bg-lapwing-dark gap-1.5"
            >
              <Save size={14} />
              {saving ? "Saving..." : "Save"}
            </Button>
            {hasChanges && (
              <Button
                variant="outline"
                onClick={() => { setContent(originalContent); setSaveResult(null); }}
              >
                Discard
              </Button>
            )}
            {saveResult && (
              <span className={`text-xs ${saveResult.ok ? "text-green-400" : "text-red-400"}`}>
                {saveResult.msg}
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
