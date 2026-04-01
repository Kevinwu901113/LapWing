import { useEffect, useRef, useState, useCallback } from "react";
import { getApiBase } from "../api";
import type { LogLine } from "../api";

const MAX_LINES = 2000;
const RECONNECT_DELAY_MS = 3000;

export function useLogStream(level: string, module: string) {
  const [lines, setLines] = useState<LogLine[]>([]);
  const [live, setLiveState] = useState(true);
  const esRef = useRef<EventSource | null>(null);
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const liveRef = useRef(true);

  const closeConnection = useCallback(() => {
    if (retryRef.current) {
      clearTimeout(retryRef.current);
      retryRef.current = null;
    }
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }
  }, []);

  const openConnection = useCallback(() => {
    if (!liveRef.current) return;
    closeConnection();

    const params = new URLSearchParams();
    if (level && level !== "全部") params.set("level", level);
    if (module) params.set("module", module);

    const url = `${getApiBase()}/api/logs/stream?${params.toString()}`;
    const es = new EventSource(url);
    esRef.current = es;

    es.onmessage = (event) => {
      try {
        const line = JSON.parse(event.data as string) as LogLine;
        setLines((prev) => {
          const next = [...prev, line];
          return next.length > MAX_LINES ? next.slice(next.length - MAX_LINES) : next;
        });
      } catch {
        // ignore parse errors
      }
    };

    es.onerror = () => {
      es.close();
      esRef.current = null;
      if (liveRef.current) {
        retryRef.current = setTimeout(() => {
          openConnection();
        }, RECONNECT_DELAY_MS);
      }
    };
  }, [level, module, closeConnection]);

  const setLive = useCallback(
    (v: boolean) => {
      liveRef.current = v;
      setLiveState(v);
      if (v) {
        openConnection();
      } else {
        closeConnection();
      }
    },
    [openConnection, closeConnection],
  );

  const clear = useCallback(() => {
    setLines([]);
  }, []);

  // Open or close connection when live/level/module changes
  useEffect(() => {
    if (liveRef.current) {
      openConnection();
    }
    return () => {
      closeConnection();
    };
  }, [openConnection, closeConnection]);

  return { lines, live, setLive, clear };
}
