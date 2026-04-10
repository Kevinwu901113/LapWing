import { useEffect, useState, useCallback } from "react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Input } from "@/components/ui/input";
import { Trash2 } from "lucide-react";
import { getMemory, getLearnings, getInterests } from "@/lib/api";
import type { MemoryItem, LearningItem } from "@/types/api";

export default function MemoryPage() {
  const [facts, setFacts] = useState<MemoryItem[]>([]);
  const [learnings, setLearnings] = useState<LearningItem[]>([]);
  const [interests, setInterests] = useState<{ topic: string; weight: number }[]>([]);
  const [search, setSearch] = useState("");

  const fetchData = useCallback(async () => {
    try {
      const [mem, learn, int] = await Promise.all([
        getMemory().catch(() => ({ facts: [] })),
        getLearnings().catch(() => ({ items: [] })),
        getInterests().catch(() => ({ interests: [] })),
      ]);
      setFacts(mem.facts ?? []);
      setLearnings((learn as { items: LearningItem[] }).items ?? []);
      setInterests(int.interests ?? []);
    } catch { /* offline */ }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const filteredFacts = facts.filter((f) =>
    !search || f.key.includes(search) || f.value.includes(search)
  );

  return (
    <div className="h-full flex flex-col">
      <div className="p-4 border-b border-surface-border">
        <h1 className="text-lg font-medium text-text-accent mb-3">记忆</h1>
        <Input
          placeholder="搜索..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="bg-surface border-surface-border text-text-primary"
        />
      </div>

      <Tabs defaultValue="facts" className="flex-1 flex flex-col">
        <TabsList className="mx-4 mt-2 bg-void-50">
          <TabsTrigger value="facts">用户画像</TabsTrigger>
          <TabsTrigger value="journal">自省日志</TabsTrigger>
          <TabsTrigger value="interests">兴趣图谱</TabsTrigger>
        </TabsList>

        <TabsContent value="facts" className="flex-1 m-0">
          <ScrollArea className="h-full px-4 py-2">
            <div className="space-y-2">
              {filteredFacts.map((fact, i) => (
                <div key={i} className="bg-surface border border-surface-border rounded-lg p-3 group">
                  <div className="flex items-start justify-between">
                    <div>
                      <div className="text-xs text-lapwing font-mono">{fact.key}</div>
                      <div className="text-sm text-text-primary mt-1">{fact.value}</div>
                    </div>
                    <button className="opacity-0 group-hover:opacity-100 text-text-muted hover:text-red-400">
                      <Trash2 size={14} />
                    </button>
                  </div>
                  <div className="text-xs text-text-muted mt-1">{fact.source}</div>
                </div>
              ))}
            </div>
          </ScrollArea>
        </TabsContent>

        <TabsContent value="journal" className="flex-1 m-0">
          <ScrollArea className="h-full px-4 py-2">
            <div className="space-y-2">
              {learnings.map((entry, i) => (
                <div key={i} className="bg-surface border border-surface-border rounded-lg p-3">
                  <div className="text-xs text-lapwing">{entry.date}</div>
                  <div className="text-sm text-text-primary mt-1 whitespace-pre-wrap">
                    {entry.preview}
                  </div>
                </div>
              ))}
            </div>
          </ScrollArea>
        </TabsContent>

        <TabsContent value="interests" className="flex-1 m-0">
          <ScrollArea className="h-full px-4 py-2">
            <div className="space-y-2">
              {interests.map((item, i) => (
                <div key={i} className="flex items-center gap-3">
                  <span className="text-sm text-text-primary w-32 truncate">{item.topic}</span>
                  <div className="flex-1 h-2 bg-void-50 rounded-full">
                    <div
                      className="h-full bg-lapwing rounded-full"
                      style={{ width: `${Math.min(item.weight * 10, 100)}%` }}
                    />
                  </div>
                  <span className="text-xs text-text-muted w-8 text-right">{item.weight}</span>
                </div>
              ))}
            </div>
          </ScrollArea>
        </TabsContent>
      </Tabs>
    </div>
  );
}
