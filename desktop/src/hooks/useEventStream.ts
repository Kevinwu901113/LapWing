import { useEffect, useRef, useState } from "react";
import { API_BASE } from "../api";
import type { DesktopEvent } from "../api";

const MAX_EVENTS = 500;
const RECONNECT_DELAY_MS = 3000;

export function useEventStream() {
  const [events, setEvents] = useState<DesktopEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const unmountedRef = useRef(false);

  useEffect(() => {
    unmountedRef.current = false;

    function connect() {
      if (unmountedRef.current) return;

      const es = new EventSource(`${API_BASE}/api/events/stream`);
      esRef.current = es;

      es.onopen = () => {
        setConnected(true);
      };

      es.onmessage = (event) => {
        try {
          const evt = JSON.parse(event.data as string) as DesktopEvent;
          setEvents((prev) => {
            const next = [...prev, evt];
            return next.length > MAX_EVENTS ? next.slice(next.length - MAX_EVENTS) : next;
          });
        } catch {
          // ignore parse errors
        }
      };

      es.onerror = () => {
        setConnected(false);
        es.close();
        esRef.current = null;
        if (!unmountedRef.current) {
          retryRef.current = setTimeout(connect, RECONNECT_DELAY_MS);
        }
      };
    }

    connect();

    return () => {
      unmountedRef.current = true;
      if (retryRef.current) {
        clearTimeout(retryRef.current);
        retryRef.current = null;
      }
      if (esRef.current) {
        esRef.current.close();
        esRef.current = null;
      }
      setConnected(false);
    };
  }, []);

  return { events, connected };
}
