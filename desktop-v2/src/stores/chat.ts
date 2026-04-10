import { create } from "zustand";
import type { ChatMessage, ToolStatusInfo } from "@/types/chat";

type WsStatus = "connecting" | "connected" | "disconnected";

interface ChatState {
  messages: ChatMessage[];
  wsStatus: WsStatus;
  toolStatus: ToolStatusInfo | null;
  chatId: string;
  addMessage: (msg: ChatMessage) => void;
  updateInterim: (id: string, content: string) => void;
  setMessages: (msgs: ChatMessage[]) => void;
  prependMessages: (msgs: ChatMessage[]) => void;
  setWsStatus: (status: WsStatus) => void;
  setToolStatus: (status: ToolStatusInfo | null) => void;
  setChatId: (id: string) => void;
}

export const useChatStore = create<ChatState>((set) => ({
  messages: [],
  wsStatus: "disconnected",
  toolStatus: null,
  chatId: "",
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
}));
