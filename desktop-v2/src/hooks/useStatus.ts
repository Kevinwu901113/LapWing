import { useEffect } from "react";
import { useStatusStore } from "@/stores/status";

const POLL_INTERVAL_MS = 30_000;

/** Polls /api/v2/status on mount + interval. SSE events update in real-time via useSSEv2 dispatch. */
export function useStatus() {
  const refresh = useStatusStore((s) => s.refresh);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  return useStatusStore((s) => s.status);
}
