import { useEffect, useState, useCallback } from "react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { RefreshCw, Cpu, HardDrive, MemoryStick, Wifi, WifiOff, Clock, Brain } from "lucide-react";
import { getSystemInfo, getSystemEvents } from "@/lib/api-v2";
import type { SystemInfo, SystemEvent } from "@/types/system";

function formatUptime(seconds: number): string {
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h ${mins}m`;
  if (hours > 0) return `${hours}h ${mins}m`;
  return `${mins}m`;
}

function formatBytes(bytes: number): string {
  const gb = bytes / (1024 * 1024 * 1024);
  return `${gb.toFixed(1)} GB`;
}

function ProgressBar({ percent, color = "bg-lapwing" }: { percent: number; color?: string }) {
  return (
    <div className="h-2 bg-void-50 rounded-full overflow-hidden">
      <div className={`h-full rounded-full ${color}`} style={{ width: `${Math.min(percent, 100)}%` }} />
    </div>
  );
}

function formatTime(ts: string): string {
  try {
    return new Date(ts).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return "";
  }
}

export default function SystemPage() {
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const [events, setEvents] = useState<SystemEvent[]>([]);
  const [eventFilter, setEventFilter] = useState("");
  const [loading, setLoading] = useState(false);

  const fetchInfo = useCallback(async () => {
    try {
      const data = await getSystemInfo();
      setInfo(data);
    } catch { /* offline */ }
  }, []);

  const fetchEvents = useCallback(async () => {
    try {
      const params: { event_type?: string; limit: number } = { limit: 50 };
      if (eventFilter.trim()) params.event_type = eventFilter.trim();
      const data = await getSystemEvents(params);
      setEvents(data.events ?? []);
    } catch { /* offline */ }
  }, [eventFilter]);

  const refresh = useCallback(async () => {
    setLoading(true);
    await Promise.all([fetchInfo(), fetchEvents()]);
    setLoading(false);
  }, [fetchInfo, fetchEvents]);

  useEffect(() => {
    refresh();
    const id = setInterval(fetchInfo, 10_000);
    return () => clearInterval(id);
  }, [refresh, fetchInfo]);

  useEffect(() => { fetchEvents(); }, [fetchEvents]);

  const channelEntries = info ? Object.entries(info.channels) : [];

  return (
    <div className="h-full flex flex-col">
      <div className="p-4 border-b border-surface-border flex items-center justify-between">
        <h1 className="text-lg font-medium text-text-accent">System</h1>
        <Button
          size="sm"
          variant="outline"
          onClick={refresh}
          disabled={loading}
          className="gap-1.5 h-7"
        >
          <RefreshCw size={12} className={loading ? "animate-spin" : ""} /> Refresh
        </Button>
      </div>

      <ScrollArea className="flex-1">
        <div className="p-4 space-y-4">
          {/* Uptime + Resources */}
          <div className="grid grid-cols-2 gap-4">
            {/* Uptime */}
            <div className="bg-surface border border-surface-border rounded-lg p-4">
              <div className="flex items-center gap-2 text-text-muted text-xs mb-2">
                <Clock size={12} /> Uptime
              </div>
              <div className="text-xl font-medium text-text-accent">
                {info ? formatUptime(info.uptime_seconds) : "--"}
              </div>
            </div>

            {/* CPU */}
            <div className="bg-surface border border-surface-border rounded-lg p-4">
              <div className="flex items-center justify-between text-xs mb-2">
                <div className="flex items-center gap-2 text-text-muted">
                  <Cpu size={12} /> CPU
                </div>
                <span className="text-text-primary">{info?.cpu_percent ?? 0}%</span>
              </div>
              <ProgressBar percent={info?.cpu_percent ?? 0} />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            {/* Memory */}
            <div className="bg-surface border border-surface-border rounded-lg p-4">
              <div className="flex items-center justify-between text-xs mb-2">
                <div className="flex items-center gap-2 text-text-muted">
                  <MemoryStick size={12} /> Memory
                </div>
                <span className="text-text-primary">{info?.memory.percent ?? 0}%</span>
              </div>
              <ProgressBar percent={info?.memory.percent ?? 0} color="bg-blue-400" />
              {info && (
                <div className="text-[10px] text-text-muted mt-1">
                  {formatBytes(info.memory.available)} available / {formatBytes(info.memory.total)} total
                </div>
              )}
            </div>

            {/* Disk */}
            <div className="bg-surface border border-surface-border rounded-lg p-4">
              <div className="flex items-center justify-between text-xs mb-2">
                <div className="flex items-center gap-2 text-text-muted">
                  <HardDrive size={12} /> Disk
                </div>
                <span className="text-text-primary">{info?.disk.percent ?? 0}%</span>
              </div>
              <ProgressBar percent={info?.disk.percent ?? 0} color="bg-lapwing-light" />
              {info && (
                <div className="text-[10px] text-text-muted mt-1">
                  {formatBytes(info.disk.free)} free / {formatBytes(info.disk.total)} total
                </div>
              )}
            </div>
          </div>

          {/* Consciousness + Channels */}
          <div className="grid grid-cols-2 gap-4">
            {/* Consciousness */}
            {info?.consciousness && (
              <div className="bg-surface border border-surface-border rounded-lg p-4">
                <div className="flex items-center gap-2 text-text-muted text-xs mb-3">
                  <Brain size={12} /> Consciousness Loop
                </div>
                <div className="space-y-1.5 text-sm">
                  <div className="flex justify-between">
                    <span className="text-text-secondary">Interval</span>
                    <span className="text-text-primary">
                      {info.consciousness.current_interval != null ? `${info.consciousness.current_interval}s` : "—"}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-text-secondary">Next tick</span>
                    <span className="text-text-primary">
                      {info.consciousness.next_tick_at
                        ? new Date(info.consciousness.next_tick_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })
                        : "—"}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-text-secondary">Idle streak</span>
                    <span className="text-text-primary">{info.consciousness.idle_streak}</span>
                  </div>
                </div>
              </div>
            )}

            {/* Channels */}
            <div className="bg-surface border border-surface-border rounded-lg p-4">
              <div className="text-xs text-text-muted mb-3">Channels</div>
              <div className="space-y-2">
                {channelEntries.length === 0 ? (
                  <div className="text-xs text-text-muted">No channels</div>
                ) : (
                  channelEntries.map(([name, status]) => {
                    const connected = status === true || status === "via_websocket";
                    return (
                      <div key={name} className="flex items-center gap-2 text-sm">
                        {connected ? (
                          <Wifi size={12} className="text-green-400" />
                        ) : (
                          <WifiOff size={12} className="text-red-400" />
                        )}
                        <span className="text-text-primary capitalize">{name}</span>
                        <span className="text-xs text-text-muted ml-auto">
                          {typeof status === "string" ? status : connected ? "connected" : "disconnected"}
                        </span>
                      </div>
                    );
                  })
                )}
              </div>
            </div>
          </div>

          {/* Event Log */}
          <div className="bg-surface border border-surface-border rounded-lg p-4">
            <div className="flex items-center justify-between mb-3">
              <div className="text-xs text-text-muted">Event Log</div>
              <Input
                value={eventFilter}
                onChange={(e) => setEventFilter(e.target.value)}
                placeholder="Filter by event type..."
                className="bg-void-50 border-surface-border text-text-primary text-xs h-7 w-48"
              />
            </div>
            <div className="space-y-1">
              {events.length === 0 ? (
                <div className="text-xs text-text-muted text-center py-4">No events</div>
              ) : (
                events.map((evt) => (
                  <div key={evt.event_id} className="flex gap-3 text-xs py-0.5">
                    <span className="text-text-muted shrink-0 w-[60px] font-mono">
                      {formatTime(evt.timestamp)}
                    </span>
                    <span className="text-lapwing shrink-0 w-[160px] truncate">{evt.event_type}</span>
                    <span className="text-text-secondary truncate flex-1">
                      {evt.actor && `[${evt.actor}] `}
                      {JSON.stringify(evt.payload).slice(0, 100)}
                    </span>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      </ScrollArea>
    </div>
  );
}
