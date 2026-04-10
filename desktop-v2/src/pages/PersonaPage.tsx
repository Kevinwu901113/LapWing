import { useEffect, useState, useCallback } from "react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { getPersonaFiles, updatePersonaFile, getChangelog, reloadPrompt } from "@/lib/api";
import type { PersonaFile, ChangelogEntry } from "@/types/api";

export default function PersonaPage() {
  const [files, setFiles] = useState<PersonaFile[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [content, setContent] = useState("");
  const [changelog, setChangelog] = useState<ChangelogEntry[]>([]);
  const [saving, setSaving] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      const [pf, cl] = await Promise.all([
        getPersonaFiles(),
        getChangelog().catch(() => ({ entries: [] })),
      ]);
      setFiles(pf.files ?? []);
      setChangelog(cl.entries ?? []);
      if (!selected && pf.files?.length) {
        setSelected(pf.files[0].name);
        setContent(pf.files[0].content);
      }
    } catch { /* offline */ }
  }, [selected]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleSelect = (name: string) => {
    const file = files.find((f) => f.name === name);
    setSelected(name);
    setContent(file?.content ?? "");
  };

  const handleSave = async () => {
    if (!selected) return;
    setSaving(true);
    try {
      await updatePersonaFile(selected, content);
      await reloadPrompt();
      fetchData();
    } catch { /* ignore */ }
    setSaving(false);
  };

  return (
    <div className="h-full flex flex-col">
      <div className="p-4 border-b border-surface-border">
        <h1 className="text-lg font-medium text-text-accent">人格</h1>
      </div>

      <Tabs defaultValue="editor" className="flex-1 flex flex-col">
        <TabsList className="mx-4 mt-2 bg-void-50">
          <TabsTrigger value="editor">文件编辑</TabsTrigger>
          <TabsTrigger value="changelog">进化历史</TabsTrigger>
        </TabsList>

        <TabsContent value="editor" className="flex-1 m-0 flex">
          {/* File list */}
          <div className="w-[200px] border-r border-surface-border p-2 space-y-1">
            {files.map((f) => (
              <button
                key={f.name}
                onClick={() => handleSelect(f.name)}
                className={`w-full text-left px-3 py-1.5 rounded text-sm transition-colors ${
                  selected === f.name
                    ? "bg-surface-active text-text-accent"
                    : "text-text-secondary hover:bg-surface-hover"
                }`}
              >
                {f.name}
              </button>
            ))}
          </div>

          {/* Editor */}
          <div className="flex-1 flex flex-col p-4">
            <Textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              className="flex-1 bg-void-50 border-surface-border text-text-primary font-mono text-sm resize-none"
              disabled={files.find((f) => f.name === selected)?.readonly}
            />
            <div className="flex gap-2 mt-3">
              <Button
                onClick={handleSave}
                disabled={saving || files.find((f) => f.name === selected)?.readonly}
                className="bg-lapwing text-void hover:bg-lapwing-dark"
              >
                {saving ? "保存中..." : "保存 + 重载"}
              </Button>
            </div>
          </div>
        </TabsContent>

        <TabsContent value="changelog" className="flex-1 m-0">
          <ScrollArea className="h-full px-4 py-2">
            <div className="space-y-3">
              {changelog.length === 0 ? (
                <div className="text-sm text-text-muted text-center py-8">暂无进化记录</div>
              ) : (
                changelog.map((entry, i) => (
                  <div key={i} className="border-l-2 border-lapwing-border pl-3 py-1">
                    <div className="text-xs text-text-muted">{entry.timestamp}</div>
                    <div className="text-sm text-text-primary mt-1 whitespace-pre-wrap">
                      {entry.changes}
                    </div>
                  </div>
                ))
              )}
            </div>
          </ScrollArea>
        </TabsContent>
      </Tabs>
    </div>
  );
}
