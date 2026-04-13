import { create } from "zustand";
import type { ChatMessage, ToolStatusInfo, ToolCallEvent, AgentActivity } from "@/types/chat";

type WsStatus = "connecting" | "connected" | "disconnected";

interface ChatState {
  messages: ChatMessage[];
  wsStatus: WsStatus;
  toolStatus: ToolStatusInfo | null;
  chatId: string;
  isStreaming: boolean;
  activeToolCalls: ToolCallEvent[];
  agentActivities: AgentActivity[];
  lapwingStatus: "idle" | "thinking" | "using_tool" | "delegating";
  addMessage: (msg: ChatMessage) => void;
  updateInterim: (id: string, content: string) => void;
  setMessages: (msgs: ChatMessage[]) => void;
  prependMessages: (msgs: ChatMessage[]) => void;
  setWsStatus: (status: WsStatus) => void;
  setToolStatus: (status: ToolStatusInfo | null) => void;
  setChatId: (id: string) => void;
  setIsStreaming: (v: boolean) => void;
  addToolCall: (tc: ToolCallEvent) => void;
  completeToolCall: (id: string, result: string, success: boolean) => void;
  clearToolCalls: () => void;
  upsertAgentActivity: (activity: AgentActivity) => void;
  clearAgentActivities: () => void;
  setLapwingStatus: (status: "idle" | "thinking" | "using_tool" | "delegating") => void;
}

export const useChatStore = create<ChatState>((set) => ({
  messages: [],
  wsStatus: "disconnected",
  toolStatus: null,
  chatId: "",
  isStreaming: false,
  activeToolCalls: [],
  agentActivities: [],
  lapwingStatus: "idle",
  addMessage: (msg) =>
    set((s) => {
      const updated = [...s.messages, msg];
      return { messages: updated.length > 500 ? updated.slice(-500) : updated };
    }),
  updateInterim: (id, content) =>
    set((s) => {
      const last = s.messages[s.messages.length - 1];
      if (last?.role === "assistant" && last.id === id) {
        return { messages: [...s.messages.slice(0, -1), { ...last, content }] };
      }
      return {
        messages: [
          ...s.messages,
          { id, role: "assistant", content, timestamp: new Date().toISOString() },
        ],
      };
    }),
  setMessages: (messages) => set({ messages }),
  prependMessages: (msgs) => set((s) => ({ messages: [...msgs, ...s.messages] })),
  setWsStatus: (wsStatus) => set({ wsStatus }),
  setToolStatus: (toolStatus) => set({ toolStatus }),
  setChatId: (chatId) => set({ chatId }),
  setIsStreaming: (isStreaming) => set({ isStreaming }),
  addToolCall: (tc) => set((s) => ({ activeToolCalls: [...s.activeToolCalls, tc] })),
  completeToolCall: (id, result, success) =>
    set((s) => ({
      activeToolCalls: s.activeToolCalls.map((tc) =>
        tc.id === id ? { ...tc, result, success, completedAt: Date.now() } : tc
      ),
    })),
  clearToolCalls: () => set({ activeToolCalls: [] }),
  upsertAgentActivity: (activity) =>
    set((s) => {
      const idx = s.agentActivities.findIndex((a) => a.commandId === activity.commandId);
      const isDone = activity.state === "done" || activity.state === "failed";
      const entry = isDone ? { ...activity, completedAt: Date.now() } : activity;
      if (idx >= 0) {
        const updated = [...s.agentActivities];
        updated[idx] = entry;
        return { agentActivities: updated };
      }
      return { agentActivities: [...s.agentActivities, entry] };
    }),
  clearAgentActivities: () => set({ agentActivities: [] }),
  setLapwingStatus: (lapwingStatus) => set({ lapwingStatus }),
}));
