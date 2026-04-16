import { create } from "zustand";
import type { TaskV2, AgentMessage } from "@/types/tasks-v2";
import { getTasksV2, getTaskMessages } from "@/lib/api-v2";

interface TasksState {
  tasks: Map<string, TaskV2>;
  agentMessages: Map<string, AgentMessage[]>;
  loading: boolean;

  upsertTask: (task: TaskV2) => void;
  removeTask: (taskId: string) => void;
  setAgentMessages: (taskId: string, messages: AgentMessage[]) => void;
  addAgentMessage: (taskId: string, msg: AgentMessage) => void;
  loadTasks: () => Promise<void>;
  loadTaskMessages: (taskId: string) => Promise<void>;
}

export const useTasksStore = create<TasksState>((set, get) => ({
  tasks: new Map(),
  agentMessages: new Map(),
  loading: false,

  upsertTask: (task) =>
    set((s) => {
      const next = new Map(s.tasks);
      next.set(task.task_id, task);
      return { tasks: next };
    }),

  removeTask: (taskId) =>
    set((s) => {
      const next = new Map(s.tasks);
      next.delete(taskId);
      return { tasks: next };
    }),

  setAgentMessages: (taskId, messages) =>
    set((s) => {
      const next = new Map(s.agentMessages);
      next.set(taskId, messages);
      return { agentMessages: next };
    }),

  addAgentMessage: (taskId, msg) =>
    set((s) => {
      const next = new Map(s.agentMessages);
      const existing = next.get(taskId) ?? [];
      next.set(taskId, [...existing, msg]);
      return { agentMessages: next };
    }),

  loadTasks: async () => {
    set({ loading: true });
    try {
      const data = await getTasksV2();
      const map = new Map<string, TaskV2>();
      for (const t of data.tasks) map.set(t.task_id, t);
      set({ tasks: map });
    } catch {
      // offline
    } finally {
      set({ loading: false });
    }
  },

  loadTaskMessages: async (taskId) => {
    try {
      const data = await getTaskMessages(taskId);
      get().setAgentMessages(taskId, data.messages);
    } catch {
      // offline
    }
  },
}));
