import { useState, useEffect, useCallback } from "react";
import { Bot, XCircle, RefreshCw, ChevronRight, ChevronLeft } from "lucide-react";
import { getAgents, cancelAgent } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { useChatStore } from "@/stores/chat";

interface AgentInfo {
  name: string;
  status: string;
  capabilities: string[];
  current_command_id: string | null;
}

const STATUS_DOT: Record<string, string> = {
  idle: "bg-green-500",
  busy: "bg-blue-500 animate-pulse",
  error: "bg-red-500",
  disabled: "bg-gray-500",
};

export function AgentPanel() {
  const [collapsed, setCollapsed] = useState(false);
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const agentActivities = useChatStore((s) => s.agentActivities);

  const fetchAgentList = useCallback(async () => {
    try {
      const data = await getAgents();
      setAgents(data.agents as AgentInfo[]);
    } catch {
      // offline or not available
    }
  }, []);

  useEffect(() => {
    fetchAgentList();
    const id = setInterval(fetchAgentList, 10_000);
    return () => clearInterval(id);
  }, [fetchAgentList]);

  const handleCancel = async (agentName: string) => {
    try {
      await cancelAgent(agentName);
      fetchAgentList();
    } catch {
      // ignore
    }
  };

  if (collapsed) {
    return (
      <button
        onClick={() => setCollapsed(false)}
        className="flex items-center justify-center w-8 h-full border-l border-surface-border bg-void-100 hover:bg-surface-hover"
        title="展开 Agent 面板"
      >
        <ChevronLeft size={14} className="text-text-muted" />
      </button>
    );
  }

  // Completed activities (most recent first, max 5)
  const completedActivities = agentActivities
    .filter(a => a.state === "done" || a.state === "failed")
    .slice(-5)
    .reverse();

  return (
    <div className="w-[240px] shrink-0 border-l border-surface-border bg-void-100 flex flex-col">
      <div className="flex items-center justify-between px-3 py-2 border-b border-surface-border">
        <div className="flex items-center gap-1.5 text-sm font-medium text-text-accent">
          <Bot size={14} />
          <span>Agents</span>
          <span className="text-xs text-text-muted">({agents.length})</span>
        </div>
        <div className="flex items-center gap-1">
          <button onClick={fetchAgentList} className="p-1 hover:bg-surface-hover rounded" title="刷新">
            <RefreshCw size={12} className="text-text-muted" />
          </button>
          <button onClick={() => setCollapsed(true)} className="p-1 hover:bg-surface-hover rounded" title="折叠">
            <ChevronRight size={12} className="text-text-muted" />
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-2 space-y-2">
        {agents.length === 0 && (
          <div className="text-xs text-text-muted text-center py-4">
            Agent 系统未启用
          </div>
        )}
        {agents.map((agent) => (
          <div key={agent.name} className="bg-surface border border-surface-border rounded-lg p-2">
            <div className="flex items-center gap-2">
              <div className={`w-2 h-2 rounded-full ${STATUS_DOT[agent.status] ?? STATUS_DOT.disabled}`} />
              <span className="text-sm text-text-primary font-medium">{agent.name}</span>
            </div>
            <div className="text-xs text-text-muted mt-1 truncate">
              {agent.capabilities.join(", ")}
            </div>
            {agent.status === "busy" && (
              <Button
                variant="ghost"
                size="sm"
                className="mt-1 h-6 text-xs text-red-400 hover:text-red-300 px-2"
                onClick={() => handleCancel(agent.name)}
              >
                <XCircle size={12} className="mr-1" />
                取消
              </Button>
            )}
          </div>
        ))}

        {completedActivities.length > 0 && (
          <>
            <div className="text-xs text-text-muted pt-2 pb-1">最近任务</div>
            {completedActivities.map((a) => (
              <div key={a.commandId} className="bg-surface border border-surface-border rounded p-2 text-xs">
                <div className="flex items-center gap-1.5">
                  <span className={a.state === "done" ? "text-green-400" : "text-red-400"}>
                    {a.state === "done" ? "✓" : "✗"}
                  </span>
                  <span className="text-text-primary font-medium">{a.agentName}</span>
                </div>
                {a.headline && (
                  <div className="text-text-secondary mt-0.5 line-clamp-2">{a.headline}</div>
                )}
              </div>
            ))}
          </>
        )}
      </div>
    </div>
  );
}
