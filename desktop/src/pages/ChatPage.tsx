import { useRef, useState, useEffect } from "react";
import { useWebSocket } from "../hooks/useWebSocket";
import ChatBubble from "../components/ChatBubble";
import ToolStatus from "../components/ToolStatus";
import AgentPanel from "../components/AgentPanel";
import StatusDot from "../components/StatusDot";
import type { ChatMessage } from "../api";
import type { ToolLogEntry } from "../components/AgentPanel";

export default function ChatPage() {
  const { messages, status, toolStatus, send, reconnect } = useWebSocket();
  const [input, setInput] = useState("");
  const [toolLog, setToolLog] = useState<ToolLogEntry[]>([]);
  const [allMessages, setAllMessages] = useState<ChatMessage[]>([]);
  const prevMessagesLen = useRef(0);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Sync new messages from hook into allMessages
  useEffect(() => {
    const newMessages = messages.slice(prevMessagesLen.current);
    if (newMessages.length > 0) {
      prevMessagesLen.current = messages.length;
      setAllMessages(prev => {
        const combined = [...prev, ...newMessages];
        return combined.length > 500 ? combined.slice(-500) : combined;
      });
    }
  }, [messages]);

  // Auto-scroll to bottom when new messages or tool status arrives
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [allMessages, toolStatus]);

  // Track tool calls into toolLog for AgentPanel
  useEffect(() => {
    if (toolStatus?.phase === "executing" && toolStatus.toolName) {
      const entry: ToolLogEntry = {
        toolName: toolStatus.toolName,
        status: "running",
        timestamp: new Date().toISOString(),
      };
      setToolLog(prev => [...prev.slice(-49), entry]); // keep last 50
    } else if (toolStatus === null) {
      // Mark last running tool as done
      setToolLog(prev =>
        prev.map((e, i) =>
          i === prev.length - 1 && e.status === "running"
            ? { ...e, status: "done" as const }
            : e
        )
      );
    }
  }, [toolStatus]);

  const handleSend = () => {
    const text = input.trim();
    if (!text) return;

    const userMsg: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user" as const,
      content: text,
      timestamp: new Date().toISOString(),
    };
    setAllMessages(prev => [...prev, userMsg]);
    setInput("");
    send(text);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const autoResize = () => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, 120)}px`;
  };

  const isOnline = status === "connected";

  return (
    <div className="chat-layout">
      {/* Chat area */}
      <div className="chat-main">
        {/* Header */}
        <div
          style={{
            padding: "12px 16px",
            borderBottom: "1px solid var(--border)",
            display: "flex",
            alignItems: "center",
            gap: 10,
            flexShrink: 0,
          }}
        >
          <div
            style={{
              width: 36,
              height: 36,
              borderRadius: "50%",
              background: "var(--accent-dim)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: 18,
            }}
          >
            🐦
          </div>
          <div>
            <div style={{ fontWeight: 500, color: "var(--text-primary)", fontSize: 14 }}>
              Lapwing
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <StatusDot online={isOnline} />
              <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
                {status === "connected"
                  ? "已连接"
                  : status === "connecting"
                  ? "连接中…"
                  : "已断线"}
              </span>
              {status === "disconnected" && (
                <button
                  className="btn-icon"
                  onClick={reconnect}
                  style={{ fontSize: 11, padding: "2px 6px" }}
                >
                  重连
                </button>
              )}
            </div>
          </div>
        </div>

        {/* Message list */}
        <div style={{ flex: 1, overflowY: "auto", padding: "16px" }}>
          {allMessages.length === 0 ? (
            <p className="empty-hint">向 Lapwing 发送消息开始对话</p>
          ) : (
            allMessages.map(msg => <ChatBubble key={msg.id} {...msg} />)
          )}
          {toolStatus && toolStatus.phase !== "done" && (
            <ToolStatus
              phase={toolStatus.phase}
              text={toolStatus.text}
              toolName={toolStatus.toolName}
            />
          )}
          <div ref={bottomRef} />
        </div>

        {/* Input area */}
        <div
          style={{
            padding: "12px 16px",
            borderTop: "1px solid var(--border)",
            display: "flex",
            gap: 10,
            alignItems: "flex-end",
            flexShrink: 0,
          }}
        >
          <textarea
            ref={textareaRef}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            onInput={autoResize}
            placeholder="输入消息… (Enter 发送，Shift+Enter 换行)"
            rows={1}
            disabled={!isOnline}
            style={{
              flex: 1,
              resize: "none",
              maxHeight: 120,
              background: "var(--bg-input)",
              border: "1px solid var(--border-input)",
              borderRadius: "var(--radius-md)",
              color: "var(--text-primary)",
              padding: "8px 12px",
              fontSize: 13,
              fontFamily: "var(--font-sans)",
              lineHeight: 1.5,
            }}
          />
          <button
            className="btn btn-primary btn-sm"
            onClick={handleSend}
            disabled={!isOnline || !input.trim()}
          >
            发送
          </button>
        </div>
      </div>

      {/* Right panel */}
      <AgentPanel
        status={status}
        toolLog={toolLog}
        sessionInfo={{ channel: "desktop" }}
      />
    </div>
  );
}
