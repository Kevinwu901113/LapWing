import { useEffect, useState, useCallback } from "react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { TaskFlowCard } from "@/components/tasks/TaskFlowCard";
import { ToolCallDetail } from "@/components/tasks/ToolCallDetail";
import { getTaskFlows, cancelTaskFlow } from "@/lib/api";
import type { TaskFlow } from "@/types/tasks";

export default function TaskCenterPage() {
  const [flows, setFlows] = useState<TaskFlow[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const fetchFlows = useCallback(async () => {
    try {
      const res = await getTaskFlows();
      setFlows(res.flows ?? []);
    } catch { /* offline */ }
  }, []);

  useEffect(() => {
    fetchFlows();
    const id = setInterval(fetchFlows, 5000);
    return () => clearInterval(id);
  }, [fetchFlows]);

  const selected = flows.find((f) => f.flow_id === selectedId) ?? null;

  const handleCancel = async () => {
    if (!selectedId) return;
    try {
      await cancelTaskFlow(selectedId);
      fetchFlows();
    } catch { /* ignore */ }
  };

  return (
    <div className="h-full flex">
      {/* Task list */}
      <div className="w-[320px] border-r border-surface-border flex flex-col">
        <div className="p-4 border-b border-surface-border">
          <h1 className="text-lg font-medium text-text-accent">任务中心</h1>
        </div>
        <ScrollArea className="flex-1 p-3">
          <div className="space-y-2">
            {flows.length === 0 ? (
              <div className="text-sm text-text-muted text-center py-8">暂无任务</div>
            ) : (
              flows.map((flow) => (
                <TaskFlowCard
                  key={flow.flow_id}
                  flow={flow}
                  selected={selectedId === flow.flow_id}
                  onClick={() => setSelectedId(flow.flow_id)}
                />
              ))
            )}
          </div>
        </ScrollArea>
      </div>

      {/* Detail panel */}
      <div className="flex-1 flex flex-col">
        {selected ? (
          <>
            <div className="p-4 border-b border-surface-border flex items-center justify-between">
              <div>
                <h2 className="text-sm font-medium text-text-accent">{selected.title}</h2>
                <span className="text-xs text-text-muted">{selected.status}</span>
              </div>
              {selected.status === "running" && (
                <Button variant="destructive" size="sm" onClick={handleCancel}>
                  取消任务
                </Button>
              )}
            </div>
            <ScrollArea className="flex-1 p-4">
              <div className="space-y-2">
                {selected.steps.map((step) => (
                  <ToolCallDetail key={step.step_id} step={step} />
                ))}
              </div>
            </ScrollArea>
          </>
        ) : (
          <div className="flex-1 flex items-center justify-center text-text-muted text-sm">
            选择一个任务查看详情
          </div>
        )}
      </div>
    </div>
  );
}
