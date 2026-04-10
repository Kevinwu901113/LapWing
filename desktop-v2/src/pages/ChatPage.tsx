import { useChatStore } from "@/stores/chat";
import { useWebSocket } from "@/hooks/useWebSocket";
import { MessageList } from "@/components/chat/MessageList";
import { MessageInput } from "@/components/chat/MessageInput";

export default function ChatPage() {
  const wsStatus = useChatStore((s) => s.wsStatus);
  const { send } = useWebSocket();

  return (
    <div className="h-full flex flex-col">
      {/* Connection status bar */}
      {wsStatus !== "connected" && (
        <div className="px-4 py-1.5 text-xs text-center bg-surface border-b border-surface-border text-text-muted">
          {wsStatus === "connecting" ? "正在连接服务器..." : "连接已断开，正在重连..."}
        </div>
      )}

      {/* Messages */}
      <MessageList />

      {/* Input */}
      <MessageInput onSend={send} disabled={wsStatus !== "connected"} />
    </div>
  );
}
