import { create } from "zustand";
import type { LapwingStatus } from "@/types/status-v2";
import { getStatusV2 } from "@/lib/api-v2";

interface StatusState {
  status: LapwingStatus;
  loading: boolean;
  refresh: () => Promise<void>;
  setState: (state: LapwingStatus["state"]) => void;
  setCurrentTask: (taskId: string | null, request?: string | null) => void;
  setActiveAgents: (agents: string[]) => void;
}

const DEFAULT_STATUS: LapwingStatus = {
  state: "idle",
  current_task_id: null,
  current_task_request: null,
  last_interaction: null,
  active_agents: [],
};

export const useStatusStore = create<StatusState>((set) => ({
  status: DEFAULT_STATUS,
  loading: false,

  refresh: async () => {
    set({ loading: true });
    try {
      const status = await getStatusV2();
      set({ status });
    } catch {
      // offline — keep current state
    } finally {
      set({ loading: false });
    }
  },

  setState: (state) =>
    set((s) => ({ status: { ...s.status, state } })),

  setCurrentTask: (taskId, request) =>
    set((s) => ({
      status: { ...s.status, current_task_id: taskId, current_task_request: request ?? null },
    })),

  setActiveAgents: (agents) =>
    set((s) => ({ status: { ...s.status, active_agents: agents } })),
}));
