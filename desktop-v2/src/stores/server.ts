import { create } from "zustand";
import type { SystemStats } from "@/types/api";

interface ServerState {
  serverUrl: string;
  token: string;
  connected: boolean;
  stats: SystemStats | null;
  setServerUrl: (url: string) => void;
  setToken: (token: string) => void;
  setConnected: (connected: boolean) => void;
  setStats: (stats: SystemStats | null) => void;
}

export const useServerStore = create<ServerState>((set) => ({
  serverUrl: localStorage.getItem("lapwing_server_url") || "http://127.0.0.1:8765",
  token: localStorage.getItem("lapwing_desktop_token") || "",
  connected: false,
  stats: null,
  setServerUrl: (url) => {
    localStorage.setItem("lapwing_server_url", url);
    set({ serverUrl: url });
  },
  setToken: (token) => {
    localStorage.setItem("lapwing_desktop_token", token);
    set({ token });
  },
  setConnected: (connected) => set({ connected }),
  setStats: (stats) => set({ stats }),
}));
