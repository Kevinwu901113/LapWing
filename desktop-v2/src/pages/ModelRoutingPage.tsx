import { useEffect, useState, useCallback } from "react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { getApiBase } from "@/lib/api";

interface Provider {
  id: string;
  name: string;
  base_url: string;
  api_type: string;
  models?: string[];
}

interface SlotConfig {
  slot: string;
  provider_id: string;
  model: string;
}

export default function ModelRoutingPage() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [slots, setSlots] = useState<SlotConfig[]>([]);
  const [testMessage, setTestMessage] = useState("");
  const [testSlot, setTestSlot] = useState("lightweight_judgment");
  const [testResult, setTestResult] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);

  const fetchConfig = useCallback(async () => {
    try {
      const base = getApiBase();
      const res = await fetch(`${base}/api/model-routing/config`, { credentials: "include" });
      if (res.ok) {
        const data = await res.json();
        setProviders(data.providers ?? []);
        setSlots(data.slots ?? []);
      }
    } catch { /* offline */ }
  }, []);

  useEffect(() => { fetchConfig(); }, [fetchConfig]);

  const handleTest = async () => {
    if (!testMessage.trim()) return;
    setTesting(true);
    setTestResult(null);
    try {
      const base = getApiBase();
      const res = await fetch(`${base}/api/model-routing/test`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: testMessage, slot: testSlot }),
      });
      const data = await res.json();
      setTestResult(`[${data.elapsed_ms}ms] ${data.reply}`);
    } catch (e) {
      setTestResult(`错误: ${e}`);
    }
    setTesting(false);
  };

  return (
    <div className="h-full flex flex-col">
      <div className="p-4 border-b border-surface-border">
        <h1 className="text-lg font-medium text-text-accent">模型路由</h1>
      </div>

      <Tabs defaultValue="providers" className="flex-1 flex flex-col">
        <TabsList className="mx-4 mt-2 bg-void-50">
          <TabsTrigger value="providers">Provider</TabsTrigger>
          <TabsTrigger value="slots">Slot 分配</TabsTrigger>
          <TabsTrigger value="test">模型测试</TabsTrigger>
        </TabsList>

        <TabsContent value="providers" className="flex-1 m-0">
          <ScrollArea className="h-full px-4 py-2">
            <div className="space-y-3">
              {providers.map((p) => (
                <div key={p.id} className="bg-surface border border-surface-border rounded-lg p-4">
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-text-accent font-medium">{p.name}</span>
                    <Badge variant="outline">{p.api_type}</Badge>
                  </div>
                  <div className="text-xs text-text-muted mt-1 font-mono truncate">{p.base_url}</div>
                </div>
              ))}
            </div>
          </ScrollArea>
        </TabsContent>

        <TabsContent value="slots" className="flex-1 m-0">
          <ScrollArea className="h-full px-4 py-2">
            <div className="space-y-3">
              {slots.map((s) => (
                <div key={s.slot} className="bg-surface border border-surface-border rounded-lg p-4">
                  <div className="text-sm text-text-accent">{s.slot}</div>
                  <div className="text-xs text-text-secondary mt-1">
                    {s.provider_id} / {s.model}
                  </div>
                </div>
              ))}
            </div>
          </ScrollArea>
        </TabsContent>

        <TabsContent value="test" className="flex-1 m-0 p-4 flex flex-col">
          <div className="flex gap-2 mb-3">
            <Input
              value={testSlot}
              onChange={(e) => setTestSlot(e.target.value)}
              placeholder="Slot 名称"
              className="w-[200px] bg-surface border-surface-border"
            />
          </div>
          <Textarea
            value={testMessage}
            onChange={(e) => setTestMessage(e.target.value)}
            placeholder="输入测试消息..."
            className="bg-surface border-surface-border text-text-primary"
            rows={3}
          />
          <Button
            onClick={handleTest}
            disabled={testing}
            className="mt-3 bg-lapwing text-void hover:bg-lapwing-dark w-fit"
          >
            {testing ? "测试中..." : "发送测试"}
          </Button>
          {testResult && (
            <pre className="mt-3 text-sm text-text-primary bg-void-50 rounded-lg p-3 whitespace-pre-wrap">
              {testResult}
            </pre>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
