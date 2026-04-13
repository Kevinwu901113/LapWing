import { useEffect, useCallback } from "react";
import { useChatStore } from "@/stores/chat";
import { useWebSocket } from "@/hooks/useWebSocket";
import { getChatHistory } from "@/lib/api";
import { MessageList } from "@/components/chat/MessageList";
import { MessageInput } from "@/components/chat/MessageInput";
import { ChatHeader } from "@/components/chat/ChatHeader";
import { AgentPanel } from "@/components/chat/AgentPanel";

export default function ChatPage() {
  const wsStatus = useChatStore((s) => s.wsStatus);
  const chatId = useChatStore((s) => s.chatId);
  const messages = useChatStore((s) => s.messages);
  const { send } = useWebSocket();

  // Load chat history on mount (only if no messages loaded yet)
  const loadHistory = useCallback(async () => {
    if (!chatId || messages.length > 0) return;
    try {
      const data = await getChatHistory(chatId);
      if (data.messages.length > 0) {
        useChatStore.getState().setMessages(data.messages);
      }
    } catch {
      // offline
    }
  }, [chatId, messages.length]);

  useEffect(() => {
    loadHistory();
  }, [loadHistory]);

  return (
    <div className="h-full flex">
      {/* Main chat area */}
      <div className="flex-1 flex flex-col min-w-0">
        <ChatHeader />
        <MessageList />
        <MessageInput onSend={send} disabled={wsStatus !== "connected"} />
      </div>

      {/* Agent panel (right sidebar) */}
      <AgentPanel />
    </div>
  );
}
