import { useEffect } from "react";
import { useServerStore } from "@/stores/server";
import { getSystemStats, getStatus } from "@/lib/api";

export function StatusBar() {
  const { stats, setStats, setConnected } = useServerStore();

  useEffect(() => {
    let active = true;
    const poll = async () => {
      try {
        const [s, st] = await Promise.all([getStatus(), getSystemStats()]);
        if (!active) return;
        setConnected(s.online);
        setStats(st);
      } catch {
        if (active) {
          setConnected(false);
          setStats(null);
        }
      }
    };
    poll();
    const id = setInterval(poll, 30_000);
    return () => { active = false; clearInterval(id); };
  }, [setConnected, setStats]);

  if (!stats) {
    return (
      <div className="px-3 py-2 text-[12px] text-text-muted">
        Disconnected
      </div>
    );
  }

  return (
    <div className="px-3 py-2 text-[12px] text-text-secondary flex gap-3">
      <span>CPU {stats.cpu_percent}%</span>
      <span>RAM {stats.memory_percent}%</span>
    </div>
  );
}
