import { useState } from "react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { useServerStore } from "@/stores/server";
import { useChatStore } from "@/stores/chat";
import { Wifi, WifiOff } from "lucide-react";

export function ConnectionTab() {
  const { serverUrl, token, setServerUrl, setToken } = useServerStore();
  const wsStatus = useChatStore((s) => s.wsStatus);
  const [url, setUrl] = useState(serverUrl);
  const [tok, setTok] = useState(token);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<string | null>(null);

  const handleTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const headers: HeadersInit = { "Content-Type": "application/json" };
      if (tok) headers["Authorization"] = `Bearer ${tok}`;
      const res = await fetch(`${url}/api/status`, { headers });
      if (res.ok) {
        setTestResult("Connection successful");
        setServerUrl(url);
        setToken(tok);
      } else {
        setTestResult(`Failed: HTTP ${res.status}`);
      }
    } catch (e) {
      setTestResult(`Failed: ${e}`);
    }
    setTesting(false);
  };

  const handleSave = () => {
    setServerUrl(url);
    setToken(tok);
  };

  return (
    <div className="space-y-6 max-w-lg">
      <div>
        <label className="text-sm text-text-accent block mb-2">Server URL</label>
        <Input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="http://127.0.0.1:8765"
          className="bg-surface border-surface-border text-text-primary"
        />
      </div>

      <div>
        <label className="text-sm text-text-accent block mb-2">Auth Token</label>
        <Input
          type="password"
          value={tok}
          onChange={(e) => setTok(e.target.value)}
          placeholder="Desktop token"
          className="bg-surface border-surface-border text-text-primary"
        />
      </div>

      <div className="flex items-center gap-3">
        <Button onClick={handleSave} variant="outline">
          Save
        </Button>
        <Button
          onClick={handleTest}
          disabled={testing}
          className="bg-lapwing text-void hover:bg-lapwing-dark"
        >
          {testing ? "Testing..." : "Test Connection"}
        </Button>
      </div>

      {testResult && (
        <p className={`text-xs ${testResult.includes("successful") ? "text-green-400" : "text-red-400"}`}>
          {testResult}
        </p>
      )}

      <div className="border-t border-surface-border pt-4">
        <div className="text-sm text-text-accent mb-2">Connection Status</div>
        <div className="space-y-2 text-sm">
          <div className="flex items-center gap-2 text-text-secondary">
            {wsStatus === "connected" ? (
              <Wifi size={14} className="text-green-400" />
            ) : (
              <WifiOff size={14} className="text-red-400" />
            )}
            <span>WebSocket: {wsStatus}</span>
          </div>
        </div>
      </div>
    </div>
  );
}
