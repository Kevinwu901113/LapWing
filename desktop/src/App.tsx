import { type FormEvent, useEffect, useRef, useState } from "react";

import {
  API_BASE,
  AuthStatusResponse,
  ChatSummary,
  createApiSession,
  deleteMemory,
  DesktopEvent,
  evolvePrompt,
  getAuthStatus,
  getChats,
  getInterests,
  getLearnings,
  getMemory,
  getOAuthLoginSession,
  getStatus,
  getTask,
  getTasks,
  importCodexCache,
  InterestItem,
  LearningItem,
  MemoryItem,
  postLatencyTelemetry,
  reloadPersona,
  OAuthLoginSession,
  StatusResponse,
  startOpenAICodexOAuth,
  TaskDetail,
  TaskSummary,
} from "./api";

const TOOL_EVENT_UPDATE_THROTTLE_MS = 500;
const LATENCY_TELEMETRY_FLUSH_INTERVAL_MS = 10000;
const LATENCY_TELEMETRY_MIN_BATCH_SIZE = 5;

declare global {
  interface Window {
    __TAURI__?: {
      invoke?: (command: string, args?: Record<string, unknown>) => Promise<unknown>;
    };
  }
}

function formatDate(value: string | null) {
  if (!value) {
    return "暂无";
  }
  return new Date(value).toLocaleString("zh-CN");
}

async function readBootstrapToken() {
  const invoke = window.__TAURI__?.invoke;
  if (!invoke) {
    throw new Error("Tauri runtime unavailable");
  }
  const token = await invoke("read_bootstrap_token");
  if (typeof token !== "string" || token.trim().length === 0) {
    throw new Error("Failed to read bootstrap token");
  }
  return token;
}

export default function App() {
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [authStatus, setAuthStatus] = useState<AuthStatusResponse | null>(null);
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [selectedChatId, setSelectedChatId] = useState("");
  const [interests, setInterests] = useState<InterestItem[]>([]);
  const [memoryItems, setMemoryItems] = useState<MemoryItem[]>([]);
  const [learnings, setLearnings] = useState<LearningItem[]>([]);
  const [events, setEvents] = useState<DesktopEvent[]>([]);
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState("");
  const [taskDetail, setTaskDetail] = useState<TaskDetail | null>(null);
  const [eventConnected, setEventConnected] = useState(false);
  const [busyAction, setBusyAction] = useState<"reload" | "evolve" | null>(null);
  const [authReady, setAuthReady] = useState(false);
  const [authError, setAuthError] = useState("");
  const [manualBootstrapRequired, setManualBootstrapRequired] = useState(false);
  const [manualBootstrapToken, setManualBootstrapToken] = useState("");
  const [submittingBootstrapToken, setSubmittingBootstrapToken] = useState(false);
  const [importingCodexCache, setImportingCodexCache] = useState(false);
  const [startingOpenAiOauth, setStartingOpenAiOauth] = useState(false);
  const [oauthSession, setOAuthSession] = useState<OAuthLoginSession | null>(null);
  const [oauthNotice, setOAuthNotice] = useState("");
  const [bootstrapAttempt, setBootstrapAttempt] = useState(0);
  const toolUpdateLastSeenAt = useRef<Record<string, number>>({});
  const startToUiSamplesRef = useRef<number[]>([]);
  const telemetryFlushInFlight = useRef(false);
  const lastTelemetryFlushAt = useRef(0);

  async function flushLatencyTelemetry(force = false) {
    if (telemetryFlushInFlight.current) {
      return;
    }
    const now = Date.now();
    const hasEnoughSamples = startToUiSamplesRef.current.length >= LATENCY_TELEMETRY_MIN_BATCH_SIZE;
    const timedOutSinceLastFlush = now - lastTelemetryFlushAt.current >= LATENCY_TELEMETRY_FLUSH_INTERVAL_MS;
    if (!force && !hasEnoughSamples && !timedOutSinceLastFlush) {
      return;
    }
    if (startToUiSamplesRef.current.length === 0) {
      return;
    }

    telemetryFlushInFlight.current = true;
    const samples = [...startToUiSamplesRef.current];
    startToUiSamplesRef.current = [];
    try {
      await postLatencyTelemetry({
        metric: "tool_execution_start_to_ui",
        samples_ms: samples,
        client_timestamp: new Date().toISOString(),
      });
      lastTelemetryFlushAt.current = now;
    } catch {
      // telemetry 失败不影响主流程
    } finally {
      telemetryFlushInFlight.current = false;
    }
  }

  async function loadOverview(selectDefaultChat = true) {
    const [nextStatus, nextChats] = await Promise.all([getStatus(), getChats()]);
    setStatus(nextStatus);
    setChats(nextChats);

    if (selectDefaultChat && !selectedChatId && nextChats.length > 0) {
      setSelectedChatId(nextChats[0].chat_id);
    }
  }

  async function loadAuthPanel() {
    const nextAuthStatus = await getAuthStatus();
    setAuthStatus(nextAuthStatus);
    return nextAuthStatus;
  }

  async function loadChatData(chatId: string) {
    const [interestResponse, memoryResponse] = await Promise.all([
      getInterests(chatId),
      getMemory(chatId),
    ]);
    setInterests(interestResponse.items);
    setMemoryItems(memoryResponse.items);
  }

  async function loadLearnings() {
    const response = await getLearnings();
    setLearnings(response.items);
  }

  async function loadTasks(chatId?: string) {
    const response = await getTasks(chatId, undefined, 20);
    setTasks(response.items);
    if (response.items.length === 0) {
      setSelectedTaskId("");
      setTaskDetail(null);
      return;
    }

    const selectedExists = response.items.some((item) => item.task_id === selectedTaskId);
    if (!selectedExists) {
      setSelectedTaskId(response.items[0].task_id);
    }
  }

  async function loadTaskDetail(taskId: string) {
    const detail = await getTask(taskId);
    setTaskDetail(detail);
  }

  async function handleDeleteMemory(factKey: string) {
    if (!selectedChatId) {
      return;
    }
    await deleteMemory(selectedChatId, factKey);
    await loadChatData(selectedChatId);
  }

  async function handleReload() {
    setBusyAction("reload");
    try {
      await reloadPersona();
    } finally {
      setBusyAction(null);
    }
  }

  async function handleEvolve() {
    setBusyAction("evolve");
    try {
      await evolvePrompt();
    } finally {
      setBusyAction(null);
    }
  }

  async function handleImportCodexCache() {
    setImportingCodexCache(true);
    try {
      await importCodexCache();
      await loadAuthPanel();
    } finally {
      setImportingCodexCache(false);
    }
  }

  async function handleManualBootstrapSubmit() {
    const token = manualBootstrapToken.trim();
    if (!token) {
      setAuthError("请输入 bootstrap token。");
      return;
    }

    setSubmittingBootstrapToken(true);
    try {
      await createApiSession(token);
      setManualBootstrapRequired(false);
      setAuthError("");
      setManualBootstrapToken("");
      setBootstrapAttempt((value) => value + 1);
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : String(error));
    } finally {
      setSubmittingBootstrapToken(false);
    }
  }

  async function handleStartOpenAiOauth() {
    setStartingOpenAiOauth(true);
    try {
      const returnTo =
        ["http:", "https:"].includes(window.location.protocol) ? window.location.href : undefined;
      const session = await startOpenAICodexOAuth(returnTo);
      setOAuthSession(session);
      setOAuthNotice("OpenAI 登录页面已准备好，完成授权后这里会自动刷新。");
      const popup = window.open(session.authorizeUrl, "_blank", "noopener,noreferrer");
      if (!popup) {
        setOAuthNotice("浏览器拦截了新窗口，请点击下面的授权链接继续。");
      }
    } catch (error) {
      setOAuthNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setStartingOpenAiOauth(false);
    }
  }

  useEffect(() => {
    let stream: EventSource | null = null;
    let cancelled = false;

    async function bootstrap() {
      try {
        let nextAuthStatus: AuthStatusResponse | null = null;
        try {
          nextAuthStatus = await loadAuthPanel();
        } catch (authProbeError) {
          const invoke = window.__TAURI__?.invoke;
          if (!invoke) {
            if (!cancelled) {
              setManualBootstrapRequired(true);
              setAuthReady(false);
              setAuthError("");
            }
            return;
          }
          const bootstrapToken = await readBootstrapToken();
          await createApiSession(bootstrapToken);
          nextAuthStatus = await loadAuthPanel();
        }
        if (cancelled) {
          return;
        }
        setAuthReady(true);
        setManualBootstrapRequired(false);
        setAuthError("");
        setAuthStatus(nextAuthStatus);
        await Promise.all([loadOverview(), loadLearnings()]);

        if ("Notification" in window && Notification.permission === "default") {
          void Notification.requestPermission();
        }

        stream = new EventSource(`${API_BASE}/api/events/stream`, {
          withCredentials: API_BASE.length > 0,
        });
        stream.onopen = () => setEventConnected(true);
        stream.onerror = () => setEventConnected(false);
        stream.onmessage = (message) => {
          const event = JSON.parse(message.data) as DesktopEvent;
          if (event.type === "task.tool_execution_update") {
            const key = event.payload.toolCallId ?? "__global__";
            const now = Date.now();
            const last = toolUpdateLastSeenAt.current[key] ?? 0;
            if (now - last < TOOL_EVENT_UPDATE_THROTTLE_MS) {
              return;
            }
            toolUpdateLastSeenAt.current[key] = now;
          }

          if (event.type === "task.tool_execution_start") {
            const eventTimestamp = Date.parse(event.timestamp);
            if (!Number.isNaN(eventTimestamp)) {
              const delayMs = Date.now() - eventTimestamp;
              if (delayMs >= 0 && delayMs <= 60000) {
                startToUiSamplesRef.current.push(delayMs);
                void flushLatencyTelemetry();
              }
            }
          }

          setEvents((previous) => [event, ...previous].slice(0, 5));
          if (event.type.startsWith("task.")) {
            void loadTasks(selectedChatId || undefined);
          }

          const shouldNotify = [
            "interest_proactive",
            "proactive_message",
            "reminder_message",
          ].includes(event.type);

          if (shouldNotify && "Notification" in window && Notification.permission === "granted") {
            const title = event.type === "interest_proactive" ? "Lapwing 主动分享" : "Lapwing 主动消息";
            const suffix = event.payload.topic ? `\n主题：${event.payload.topic}` : "";
            new Notification(title, {
              body: `${event.payload.text ?? "收到新消息"}${suffix}`,
            });
          }
        };
      } catch (error) {
        if (!cancelled) {
          setAuthReady(false);
          setAuthError(error instanceof Error ? error.message : String(error));
        }
      }
    }

    void bootstrap();

    return () => {
      cancelled = true;
      setEventConnected(false);
      void flushLatencyTelemetry(true);
      stream?.close();
    };
  }, [bootstrapAttempt]);

  useEffect(() => {
    if (!authReady) {
      return;
    }
    const timer = window.setInterval(() => {
      void flushLatencyTelemetry();
    }, LATENCY_TELEMETRY_FLUSH_INTERVAL_MS);
    return () => {
      window.clearInterval(timer);
    };
  }, [authReady]);

  useEffect(() => {
    if (!authReady) {
      return;
    }
    if (!selectedChatId) {
      setInterests([]);
      setMemoryItems([]);
      void loadTasks(undefined);
    } else {
      void loadChatData(selectedChatId);
      void loadTasks(selectedChatId);
    }
  }, [authReady, selectedChatId]);

  useEffect(() => {
    if (!authReady) {
      return;
    }
    if (!selectedTaskId) {
      setTaskDetail(null);
      return;
    }
    void loadTaskDetail(selectedTaskId);
  }, [authReady, selectedTaskId]);

  useEffect(() => {
    if (!authReady) {
      return;
    }
    const timer = window.setInterval(() => {
      void loadOverview(false);
      void loadAuthPanel();
      if (selectedChatId) {
        void loadChatData(selectedChatId);
      }
      void loadTasks(selectedChatId || undefined);
      if (selectedTaskId) {
        void loadTaskDetail(selectedTaskId);
      }
    }, 30000);

    return () => {
      window.clearInterval(timer);
    };
  }, [authReady, selectedChatId, selectedTaskId]);

  useEffect(() => {
    if (!authReady || oauthSession === null) {
      return;
    }
    if (!["pending", "completing"].includes(oauthSession.status)) {
      return;
    }

    const loginId = oauthSession.loginId;
    let cancelled = false;

    async function pollOauthSession() {
      try {
        const nextSession = await getOAuthLoginSession(loginId);
        if (cancelled) {
          return;
        }
        setOAuthSession(nextSession);
        if (nextSession.status === "completed") {
          setOAuthNotice(nextSession.completionMessage ?? "OpenAI 登录成功。");
          await loadAuthPanel();
        } else if (nextSession.status === "failed" || nextSession.status === "expired") {
          setOAuthNotice(nextSession.error ?? "OpenAI 登录没有完成。");
        }
      } catch (error) {
        if (!cancelled) {
          setOAuthNotice(error instanceof Error ? error.message : String(error));
        }
      }
    }

    void pollOauthSession();
    const timer = window.setInterval(() => {
      void pollOauthSession();
    }, 1500);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [authReady, oauthSession]);

  if (!authReady && manualBootstrapRequired) {
    return (
      <div className="shell">
        <section className="hero auth-guard">
          <div>
            <p className="eyebrow">Lapwing Desktop</p>
            <h1>浏览器模式需要本机 bootstrap token</h1>
            <p className="subtitle">
              当前不是 Tauri 桌面环境，所以无法自动读取远端机器上的 token。
              请在远端主机上查看 `~/.lapwing/auth/api-bootstrap-token`，然后粘贴到下面。
            </p>
            <div className="auth-form">
              <textarea
                className="token-input"
                value={manualBootstrapToken}
                onChange={(event) => setManualBootstrapToken(event.target.value)}
                placeholder="粘贴 bootstrap token"
                rows={4}
              />
              <div className="auth-actions">
                <button type="button" onClick={() => void handleManualBootstrapSubmit()} disabled={submittingBootstrapToken}>
                  {submittingBootstrapToken ? "验证中..." : "建立本机会话"}
                </button>
              </div>
            </div>
            {authError ? <p className="subtitle">{authError}</p> : null}
          </div>
        </section>
      </div>
    );
  }

  if (!authReady && authError) {
    return (
      <div className="shell">
        <section className="hero auth-guard">
          <div>
            <p className="eyebrow">Lapwing Desktop</p>
            <h1>本地 API 鉴权失败</h1>
            <p className="subtitle">{authError}</p>
          </div>
        </section>
      </div>
    );
  }

  if (!authReady) {
    return (
      <div className="shell">
        <section className="hero auth-guard">
          <div>
            <p className="eyebrow">Lapwing Desktop</p>
            <h1>正在建立本机安全会话</h1>
            <p className="subtitle">读取 bootstrap token 并换取本地 session cookie…</p>
          </div>
        </section>
      </div>
    );
  }

  return (
    <div className="shell">
      <header className="hero">
        <div>
          <p className="eyebrow">Lapwing Desktop</p>
          <h1>本地观测台</h1>
          <p className="subtitle">
            连接 Telegram 后端，查看记忆、兴趣、学习日志、任务和本机 auth 状态。
          </p>
        </div>
        <div className="hero-actions">
          <button onClick={handleReload} disabled={busyAction !== null}>
            {busyAction === "reload" ? "重载中..." : "重载人格"}
          </button>
          <button className="secondary" onClick={handleEvolve} disabled={busyAction !== null}>
            {busyAction === "evolve" ? "进化中..." : "触发进化"}
          </button>
          <button
            className="secondary"
            onClick={handleStartOpenAiOauth}
            disabled={startingOpenAiOauth || oauthSession?.status === "pending" || oauthSession?.status === "completing"}
          >
            {startingOpenAiOauth ? "跳转中..." : "登录 OpenAI"}
          </button>
          <button className="secondary" onClick={handleImportCodexCache} disabled={importingCodexCache}>
            {importingCodexCache ? "导入中..." : "导入 ~/.codex/auth.json"}
          </button>
        </div>
      </header>

      <section className="toolbar">
        <label>
          当前 Chat
          <select
            value={selectedChatId}
            onChange={(event) => setSelectedChatId(event.target.value)}
          >
            {chats.length === 0 ? <option value="">暂无 chat</option> : null}
            {chats.map((chat) => (
              <option key={chat.chat_id} value={chat.chat_id}>
                {chat.chat_id}
              </option>
            ))}
          </select>
        </label>
        <div className="status-pill">
          <span className={status?.online ? "dot online" : "dot offline"} />
          后端 {status?.online ? "在线" : "离线"}
        </div>
        <div className="status-pill">
          <span className={eventConnected ? "dot online" : "dot offline"} />
          事件流 {eventConnected ? "已连接" : "未连接"}
        </div>
        <div className="status-pill">
          <span className={authStatus?.serviceAuth.protected ? "dot online" : "dot offline"} />
          本机 API {authStatus?.serviceAuth.protected ? "已受保护" : "未保护"}
        </div>
      </section>

      <main className="grid">
        <section className="panel">
          <div className="panel-head">
            <h2>状态</h2>
          </div>
          <div className="stats">
            <article>
              <span>Chat 数量</span>
              <strong>{status?.chat_count ?? 0}</strong>
            </article>
            <article>
              <span>最后活跃</span>
              <strong>{formatDate(status?.last_interaction ?? null)}</strong>
            </article>
            <article>
              <span>服务启动</span>
              <strong>{formatDate(status?.started_at ?? null)}</strong>
            </article>
          </div>
        </section>

        <section className="panel">
          <div className="panel-head">
            <h2>Auth 状态</h2>
          </div>
          <div className="auth-summary">
            <p className="muted">Host: {authStatus?.serviceAuth.host ?? "127.0.0.1"}</p>
            <p className="muted">Cookie: {authStatus?.serviceAuth.cookieName ?? "lapwing_session"}</p>
            <div className="auth-actions">
              <button
                onClick={handleStartOpenAiOauth}
                disabled={startingOpenAiOauth || oauthSession?.status === "pending" || oauthSession?.status === "completing"}
              >
                {startingOpenAiOauth ? "跳转中..." : "用 OpenAI 账号登录"}
              </button>
              <button className="secondary" onClick={handleImportCodexCache} disabled={importingCodexCache}>
                {importingCodexCache ? "导入中..." : "导入 Codex auth.json"}
              </button>
            </div>
            {oauthSession ? (
              <article className="binding-item auth-login-state">
                <div>
                  <span>OAuth 会话</span>
                  <strong>{oauthSession.status}</strong>
                </div>
                <span className="muted">
                  {oauthSession.resolvedProfileId ?? oauthSession.profileIdHint ?? "openai"}
                </span>
              </article>
            ) : null}
            {oauthNotice ? <p className="muted">{oauthNotice}</p> : null}
            {oauthSession?.authorizeUrl && ["pending", "failed", "expired"].includes(oauthSession.status) ? (
              <p className="muted">
                如果浏览器没有自动打开：
                {" "}
                <a
                  className="inline-link"
                  href={oauthSession.authorizeUrl}
                  target="_blank"
                  rel="noreferrer"
                >
                  点这里继续 OpenAI 授权
                </a>
              </p>
            ) : null}
            <div className="binding-list">
              {Object.entries(authStatus?.bindings ?? {}).map(([purpose, profileId]) => (
                <article key={purpose} className="binding-item">
                  <span>{purpose}</span>
                  <strong>{profileId}</strong>
                </article>
              ))}
              {Object.keys(authStatus?.bindings ?? {}).length === 0 ? (
                <p className="empty">当前没有显式 auth binding，仍会回退到 .env。</p>
              ) : null}
            </div>
            <div className="binding-list">
              {Object.entries(authStatus?.routes ?? {}).map(([purpose, route]) => (
                <article key={`route-${purpose}`} className="binding-item">
                  <div>
                    <span>{purpose}</span>
                    <strong>{route.provider || "auto"} · {route.model}</strong>
                  </div>
                  <span className="muted">
                    {route.baseUrl}
                    {route.bindingMismatch ? " · binding 与 provider 不一致，已回退 .env" : ""}
                  </span>
                </article>
              ))}
            </div>
            <div className="memory-list">
              {(authStatus?.profiles ?? []).length === 0 ? (
                <p className="empty">还没有导入或登录任何 auth profile。</p>
              ) : null}
              {(authStatus?.profiles ?? []).map((profile) => (
                <article key={profile.profileId} className="memory-item compact-item">
                  <div>
                    <p className="memory-key">{profile.profileId}</p>
                    <p className="memory-value">{profile.provider} · {profile.type}</p>
                    <span className="muted">
                      {profile.status}
                      {profile.reasonCode ? ` · ${profile.reasonCode}` : ""}
                      {profile.expiresAt ? ` · 到期 ${formatDate(profile.expiresAt)}` : ""}
                    </span>
                  </div>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section className="panel">
          <div className="panel-head">
            <h2>兴趣图谱</h2>
          </div>
          <div className="interest-list">
            {interests.length === 0 ? <p className="empty">这个 chat 还没有明显兴趣记录。</p> : null}
            {interests.map((item) => (
              <article key={item.topic} className="interest-item">
                <div className="interest-row">
                  <span>{item.topic}</span>
                  <strong>{item.weight.toFixed(1)}</strong>
                </div>
                <div className="bar">
                  <div
                    className="bar-fill"
                    style={{ width: `${Math.min(item.weight * 12, 100)}%` }}
                  />
                </div>
              </article>
            ))}
          </div>
        </section>

        <section className="panel span-2">
          <div className="panel-head">
            <h2>记忆管理</h2>
          </div>
          <div className="memory-list">
            {memoryItems.length === 0 ? <p className="empty">当前没有可见记忆。</p> : null}
            {memoryItems.map((item) => (
              <article key={item.fact_key} className="memory-item">
                <div>
                  <p className="memory-key">
                    #{item.index} [{item.fact_key}]
                  </p>
                  <p className="memory-value">{item.fact_value}</p>
                  <span className="muted">更新于 {formatDate(item.updated_at)}</span>
                </div>
                <button className="danger" onClick={() => void handleDeleteMemory(item.fact_key)}>
                  删除
                </button>
              </article>
            ))}
          </div>
        </section>

        <section className="panel">
          <div className="panel-head">
            <h2>事件流</h2>
          </div>
          <div className="event-list">
            {events.length === 0 ? <p className="empty">等待来自 SSE 的主动消息与任务事件。</p> : null}
            {events.map((event, index) => (
              <article key={`${event.timestamp}-${index}`} className="event-item">
                <span className="event-type">{event.type}</span>
                <p>{event.payload.text ?? "（无文本）"}</p>
                <span className="muted">
                  {event.payload.chat_id ?? "unknown"} · {formatDate(event.timestamp)}
                  {event.payload.task_id ? ` · ${event.payload.task_id}` : ""}
                  {event.payload.phase ? ` · ${event.payload.phase}` : ""}
                  {event.payload.tool_name ? ` · ${event.payload.tool_name}` : ""}
                </span>
              </article>
            ))}
          </div>
        </section>

        <section className="panel span-2">
          <div className="panel-head">
            <h2>任务视图</h2>
          </div>
          <div className="memory-list">
            {tasks.length === 0 ? <p className="empty">暂无任务记录。</p> : null}
            {tasks.map((task) => (
              <article
                key={task.task_id}
                className="memory-item"
                onClick={() => setSelectedTaskId(task.task_id)}
                style={{
                  cursor: "pointer",
                  border: selectedTaskId === task.task_id ? "1px solid #4a90e2" : undefined,
                }}
              >
                <div>
                  <p className="memory-key">{task.task_id}</p>
                  <p className="memory-value">{task.text || "（无文本）"}</p>
                  <span className="muted">
                    {task.chat_id} · {task.status} · {formatDate(task.updated_at ?? null)}
                  </span>
                </div>
              </article>
            ))}
            {taskDetail ? (
              <article className="learning-item">
                <div className="learning-head">
                  <strong>任务详情：{taskDetail.task_id}</strong>
                  <span className="muted">{taskDetail.status}</span>
                </div>
                <pre>{JSON.stringify(taskDetail.events, null, 2)}</pre>
              </article>
            ) : null}
          </div>
        </section>

        <section className="panel span-2">
          <div className="panel-head">
            <h2>学习日志</h2>
          </div>
          <div className="learning-list">
            {learnings.length === 0 ? <p className="empty">`data/learnings/` 里还没有日志。</p> : null}
            {learnings.map((item) => (
              <article key={item.filename} className="learning-item">
                <div className="learning-head">
                  <strong>{item.date}</strong>
                  <span className="muted">{formatDate(item.updated_at)}</span>
                </div>
                <pre>{item.content}</pre>
              </article>
            ))}
          </div>
        </section>
      </main>
    </div>
  );
}
