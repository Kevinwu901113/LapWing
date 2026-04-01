import { useEffect, useState } from "react";
import { getStatus, type StatusResponse } from "../api";

export function useServerStatus(intervalMs = 30_000) {
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [online, setOnline] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function poll() {
      try {
        const s = await getStatus();
        if (!cancelled) {
          setStatus(s);
          setOnline(s.online);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) {
          setOnline(false);
          setError(e instanceof Error ? e.message : "Connection failed");
        }
      }
    }
    void poll();
    const timer = setInterval(poll, intervalMs);
    return () => { cancelled = true; clearInterval(timer); };
  }, [intervalMs]);

  return { status, online, error };
}
