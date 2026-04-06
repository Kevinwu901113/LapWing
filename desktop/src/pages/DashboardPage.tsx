import { useEffect, useMemo, useState } from "react";
import RingChart from "../components/RingChart";
import AreaChart from "../components/AreaChart";
import HeatmapBar from "../components/HeatmapBar";
import ChannelBadge from "../components/ChannelBadge";
import {
  getSystemStats,
  getApiUsage,
  getHeartbeatStatus,
  getPlatformConfig,
  getChats,
  getMemoryHealth,
  type SystemStats,
  type ApiUsage,
  type HeartbeatStatus,
  type PlatformConfig,
  type ChatSummary,
  type MemoryHealth,
} from "../api";

export default function DashboardPage() {
  const [systemStats, setSystemStats] = useState<SystemStats | null>(null);
  const [apiUsage, setApiUsage] = useState<ApiUsage | null>(null);
  const [heartbeatStatus, setHeartbeatStatus] = useState<HeartbeatStatus | null>(null);
  const [heartbeatLoaded, setHeartbeatLoaded] = useState(false);
  const [platformConfig, setPlatformConfig] = useState<PlatformConfig | null>(null);
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [memoryHealth, setMemoryHealth] = useState<MemoryHealth | null>(null);
  const [fetchError, setFetchError] = useState(false);

  // Poll all data every 30s
  useEffect(() => {
    let cancelled = false;
    async function fetchAll() {
      const results = await Promise.allSettled([
        getSystemStats().then(d => { if (!cancelled) setSystemStats(d); }),
        getApiUsage().then(d => { if (!cancelled) setApiUsage(d); }),
        getHeartbeatStatus().then(d => { if (!cancelled) { setHeartbeatStatus(d); setHeartbeatLoaded(true); } }).catch(() => { if (!cancelled) setHeartbeatLoaded(true); }),
        getPlatformConfig().then(d => { if (!cancelled) setPlatformConfig(d); }),
        getChats().then(d => { if (!cancelled) setChats(d); }),
        getMemoryHealth().then(d => { if (!cancelled) setMemoryHealth(d); }).catch(() => {}),
      ]);
      const allFailed = results.every(r => r.status === "rejected");
      if (!cancelled) setFetchError(allFailed);
    }
    void fetchAll();
    const timer = setInterval(fetchAll, 30_000);
    return () => { cancelled = true; clearInterval(timer); };
  }, []);

  // Helper: build 24h conversation chart data
  const conversationChartData = useMemo(() => {
    const hours = Array.from({ length: 24 }, (_, i) => ({
      name: `${String(i).padStart(2, "0")}:00`,
      value: 0,
    }));
    const cutoff = Date.now() - 24 * 60 * 60 * 1000;
    chats.forEach(chat => {
      const ts = chat.last_interaction ? new Date(chat.last_interaction).getTime() : 0;
      if (ts > cutoff) {
        const hour = new Date(ts).getHours();
        hours[hour].value += 1;
      }
    });
    return hours;
  }, [chats]);

  return (
    <div className="animate-in">
      {fetchError && (
        <div style={{ background: "var(--red-dim)", border: "1px solid var(--red)", borderRadius: "var(--radius-md)", padding: "8px 14px", marginBottom: 16, fontSize: 13, color: "var(--red)" }}>
          无法连接到后端服务器，显示的数据可能不完整。
        </div>
      )}

      {/* Row 1 */}
      <div className="stat-grid-4" style={{ marginBottom: 20 }}>
        {/* CPU */}
        <div className="card" style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 8 }}>
          <span className="card-title">CPU</span>
          <RingChart value={systemStats?.cpu_percent ?? 0} max={100} label={systemStats?.cpu_model ?? "—"} unit="%" color="var(--blue)" />
        </div>
        {/* Memory */}
        <div className="card" style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 8 }}>
          <span className="card-title">内存</span>
          <RingChart
            value={systemStats?.memory_percent ?? 0} max={100}
            label={systemStats ? `${systemStats.memory_used_gb}/${systemStats.memory_total_gb} GB` : "—"}
            unit="%" color="var(--accent)"
          />
        </div>
        {/* Disk */}
        <div className="card" style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 8 }}>
          <span className="card-title">磁盘</span>
          <RingChart
            value={systemStats?.disk_percent ?? 0} max={100}
            label={systemStats ? `${systemStats.disk_used_gb}/${systemStats.disk_total_gb} GB` : "—"}
            unit="%" color="var(--green)"
          />
        </div>
        {/* Channels */}
        <div className="card">
          <div className="card-header"><span className="card-title">通信通道</span></div>
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <ChannelBadge channel="telegram" enabled={platformConfig?.telegram?.enabled ?? false} />
            <ChannelBadge channel="qq" enabled={platformConfig?.qq?.enabled ?? false} />
            <ChannelBadge channel="desktop" enabled={platformConfig?.desktop?.enabled ?? false} />
          </div>
        </div>
      </div>

      {/* Row 2 */}
      <div className="stat-grid-2" style={{ marginBottom: 20 }}>
        {/* API Quota */}
        <div className="card">
          <div className="card-header"><span className="card-title">API 配额</span></div>
          <div style={{ display: "flex", gap: 16, justifyContent: "space-around" }}>
            {(apiUsage?.providers ?? []).map(p => (
              <RingChart key={p.name} value={p.used} max={p.limit || 1} label={p.name} unit={p.unit} size={80} />
            ))}
            {(!apiUsage || apiUsage.providers.length === 0) && (
              <span style={{ color: "var(--text-muted)", fontSize: 13 }}>暂无数据</span>
            )}
          </div>
        </div>
        {/* Conversations */}
        <div className="card">
          <div className="card-header"><span className="card-title">过去 24 小时对话</span></div>
          <AreaChart data={conversationChartData} height={120} />
        </div>
      </div>

      {/* Row 3 — Memory Health + Heartbeat */}
      <div className="stat-grid-2" style={{ marginBottom: 20 }}>
        {/* Memory Health */}
        <div className="card">
          <div className="card-header"><span className="card-title">记忆健康</span></div>
          {memoryHealth ? (
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 12 }}>
              <RingChart value={memoryHealth.score} max={100} label={`共 ${memoryHealth.total} 条`} unit="分" color="var(--accent)" />
              <div style={{ width: "100%", display: "flex", flexDirection: "column", gap: 6, fontSize: 12 }}>
                {Object.entries(memoryHealth.dimensions ?? {}).map(([key, val]) => (
                  <div key={key} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ color: "var(--text-muted)", width: 60, flexShrink: 0 }}>{key}</span>
                    <div style={{ flex: 1, background: "var(--border)", borderRadius: 4, height: 6 }}>
                      <div style={{ width: `${Math.round((val as number) * 100)}%`, height: "100%", background: "var(--accent)", borderRadius: 4 }} />
                    </div>
                    <span style={{ color: "var(--text-secondary)", width: 32, textAlign: "right" }}>{Math.round((val as number) * 100)}%</span>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <p className="empty-hint">暂无数据</p>
          )}
        </div>

        {/* Heartbeat */}
        <div className="card">
          <div className="card-header"><span className="card-title">心跳活动（过去 24 小时）</span></div>
        {!heartbeatLoaded ? (
          <p className="empty-hint">加载中…</p>
        ) : heartbeatStatus && heartbeatStatus.actions.length > 0 ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {heartbeatStatus.actions.map(action => (
              <HeatmapBar
                key={action.name}
                label={action.name}
                segments={action.history_24h?.length === 24 ? action.history_24h : Array(24).fill(0)}
                enabled={action.enabled}
              />
            ))}
          </div>
        ) : (
          <p className="empty-hint">心跳引擎未启动</p>
        )}
        </div>
      </div>
    </div>
  );
}
