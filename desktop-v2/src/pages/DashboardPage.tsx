import { useEffect, useState, useCallback } from "react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { MetricCard } from "@/components/dashboard/MetricCard";
import { ResourceRing } from "@/components/dashboard/ResourceRing";
import { ChannelStatus } from "@/components/dashboard/ChannelStatus";
import { HeartbeatCard } from "@/components/dashboard/HeartbeatCard";
import { ReminderList } from "@/components/dashboard/ReminderList";
import { useSSE, type DesktopEvent } from "@/hooks/useSSE";
import {
  getStatus, getSystemStats, getChannels, getHeartbeatStatus,
  getReminders, deleteReminder,
} from "@/lib/api";
import type {
  ServerStatus, SystemStats, ChannelInfo,
  HeartbeatStatus, ReminderItem,
} from "@/types/api";

function formatUptime(startedAt: string): string {
  const ms = Date.now() - new Date(startedAt).getTime();
  const mins = Math.floor(ms / 60000);
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  const rm = mins % 60;
  return `${hrs}h${rm}m`;
}

export default function DashboardPage() {
  const [status, setStatus] = useState<ServerStatus | null>(null);
  const [stats, setStats] = useState<SystemStats | null>(null);
  const [channels, setChannels] = useState<ChannelInfo[]>([]);
  const [heartbeat, setHeartbeat] = useState<HeartbeatStatus | null>(null);
  const [reminders, setReminders] = useState<ReminderItem[]>([]);
  const { events } = useSSE();

  const fetchAll = useCallback(async () => {
    try {
      const [s, st, ch, hb, rem] = await Promise.all([
        getStatus(), getSystemStats(), getChannels(),
        getHeartbeatStatus().catch(() => null),
        getReminders().catch(() => ({ reminders: [] })),
      ]);
      setStatus(s);
      setStats(st);
      setChannels(ch.platforms ?? []);
      setHeartbeat(hb);
      setReminders(rem.reminders ?? []);
    } catch {
      // offline
    }
  }, []);

  useEffect(() => {
    fetchAll();
    const id = setInterval(fetchAll, 30_000);
    return () => clearInterval(id);
  }, [fetchAll]);

  const handleDeleteReminder = async (id: number) => {
    try {
      await deleteReminder(id, "");
      setReminders((prev) => prev.filter((r) => r.id !== id));
    } catch { /* ignore */ }
  };

  const recentEvents = events.slice(-20).reverse();

  return (
    <ScrollArea className="h-full">
      <div className="p-6 space-y-6">
        <h1 className="text-lg font-medium text-text-accent">仪表盘</h1>

        {/* Metric cards */}
        <div className="grid grid-cols-4 gap-4">
          <MetricCard label="运行时长" value={status ? formatUptime(status.started_at) : "--"} />
          <MetricCard label="对话数" value={status?.chat_count ?? "--"} />
          <MetricCard label="通道数" value={channels.length} />
          <MetricCard label="提醒数" value={reminders.length} />
        </div>

        <div className="grid grid-cols-2 gap-4">
          {/* System resources */}
          <div className="bg-surface border border-surface-border rounded-lg p-4">
            <h2 className="text-sm font-medium text-text-accent mb-4">系统资源</h2>
            {stats ? (
              <div className="flex justify-around">
                <ResourceRing label="CPU" percent={stats.cpu_percent} />
                <ResourceRing label="RAM" percent={stats.memory_percent} color="#7ba4e0" />
                <ResourceRing label="Disk" percent={stats.disk_percent} color="#e0eaff" />
              </div>
            ) : (
              <div className="text-sm text-text-muted text-center py-4">加载中...</div>
            )}
          </div>

          {/* Channel status */}
          <div className="bg-surface border border-surface-border rounded-lg p-4">
            <h2 className="text-sm font-medium text-text-accent mb-4">通道状态</h2>
            <ChannelStatus channels={channels} />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4">
          {/* Heartbeat */}
          <div className="bg-surface border border-surface-border rounded-lg p-4">
            <h2 className="text-sm font-medium text-text-accent mb-4">心跳引擎</h2>
            <HeartbeatCard status={heartbeat} />
          </div>

          {/* Reminders */}
          <div className="bg-surface border border-surface-border rounded-lg p-4">
            <h2 className="text-sm font-medium text-text-accent mb-4">提醒列表</h2>
            <ReminderList reminders={reminders} onDelete={handleDeleteReminder} />
          </div>
        </div>

        {/* Recent activity */}
        <div className="bg-surface border border-surface-border rounded-lg p-4">
          <h2 className="text-sm font-medium text-text-accent mb-3">最近活动</h2>
          <div className="space-y-1.5">
            {recentEvents.length === 0 ? (
              <div className="text-sm text-text-muted">暂无活动</div>
            ) : (
              recentEvents.map((evt: DesktopEvent, i: number) => (
                <div key={i} className="flex gap-3 text-xs">
                  <span className="text-text-muted shrink-0 w-[50px]">
                    {new Date(evt.timestamp).toLocaleTimeString("zh-CN", {
                      hour: "2-digit", minute: "2-digit",
                    })}
                  </span>
                  <span className="text-text-secondary truncate">
                    {evt.type}: {JSON.stringify(evt.payload).slice(0, 80)}
                  </span>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </ScrollArea>
  );
}
