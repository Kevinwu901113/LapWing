import { useState } from "react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { useServerStore } from "@/stores/server";

export default function SettingsPage() {
  const { serverUrl, setServerUrl } = useServerStore();
  const [url, setUrl] = useState(serverUrl);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<string | null>(null);

  const handleTestConnection = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const res = await fetch(`${url}/api/status`, { credentials: "include" });
      if (res.ok) {
        setTestResult("连接成功");
        setServerUrl(url);
      } else {
        setTestResult(`连接失败: HTTP ${res.status}`);
      }
    } catch (e) {
      setTestResult(`连接失败: ${e}`);
    }
    setTesting(false);
  };

  return (
    <div className="h-full flex flex-col">
      <div className="p-4 border-b border-surface-border">
        <h1 className="text-lg font-medium text-text-accent">设置</h1>
      </div>

      <Tabs defaultValue="general" className="flex-1 flex flex-col">
        <TabsList className="mx-4 mt-2 bg-void-50">
          <TabsTrigger value="general">通用</TabsTrigger>
          <TabsTrigger value="features">功能开关</TabsTrigger>
          <TabsTrigger value="about">关于</TabsTrigger>
        </TabsList>

        <TabsContent value="general" className="flex-1 m-0">
          <ScrollArea className="h-full px-4 py-4">
            <div className="space-y-6 max-w-lg">
              <div>
                <label className="text-sm text-text-accent block mb-2">服务器地址</label>
                <div className="flex gap-2">
                  <Input
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    className="bg-surface border-surface-border text-text-primary"
                  />
                  <Button
                    onClick={handleTestConnection}
                    disabled={testing}
                    variant="outline"
                    className="shrink-0"
                  >
                    {testing ? "测试中..." : "测试连接"}
                  </Button>
                </div>
                {testResult && (
                  <p className={`text-xs mt-1 ${testResult.includes("成功") ? "text-green-400" : "text-red-400"}`}>
                    {testResult}
                  </p>
                )}
              </div>

              <div className="flex items-center justify-between">
                <div>
                  <div className="text-sm text-text-accent">消息提示音</div>
                  <div className="text-xs text-text-muted">收到新消息时播放提示音</div>
                </div>
                <Switch />
              </div>
            </div>
          </ScrollArea>
        </TabsContent>

        <TabsContent value="features" className="flex-1 m-0">
          <ScrollArea className="h-full px-4 py-4">
            <p className="text-sm text-text-muted">功能开关将从服务器加载。</p>
          </ScrollArea>
        </TabsContent>

        <TabsContent value="about" className="flex-1 m-0">
          <ScrollArea className="h-full px-4 py-4">
            <div className="space-y-3 text-sm">
              <div className="text-text-accent">Lapwing Desktop v0.1.0</div>
              <div className="text-text-secondary">
                Built with Tauri v2 + React + TypeScript
              </div>
            </div>
          </ScrollArea>
        </TabsContent>
      </Tabs>
    </div>
  );
}
