import { type FormEvent, useEffect, useState, type ReactNode } from "react";
import { createApiSession, getAuthStatus } from "../api";

declare global {
  interface Window {
    __TAURI__?: {
      invoke?: (command: string, args?: Record<string, unknown>) => Promise<unknown>;
    };
  }
}

async function readBootstrapToken(): Promise<string> {
  const invoke = window.__TAURI__?.invoke;
  if (!invoke) throw new Error("Tauri runtime unavailable");
  const token = await invoke("read_bootstrap_token");
  if (typeof token !== "string" || !token.trim()) throw new Error("Failed to read bootstrap token");
  return token;
}

export default function AuthGuard({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  const [manualMode, setManualMode] = useState(false);
  const [manualToken, setManualToken] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // 先尝试现有 session
        await getAuthStatus();
        if (!cancelled) setReady(true);
      } catch {
        // 尝试 Tauri 自动获取 token
        try {
          const token = await readBootstrapToken();
          await createApiSession(token);
          if (!cancelled) setReady(true);
        } catch {
          // 非 Tauri 环境，进入手动输入模式
          if (!cancelled) setManualMode(true);
        }
      }
    })();
    return () => { cancelled = true; };
  }, []);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!manualToken.trim()) return;
    setSubmitting(true);
    try {
      await createApiSession(manualToken.trim());
      setReady(true);
      setManualMode(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  if (ready) return <>{children}</>;

  return (
    <div className="auth-guard-page">
      <div className="auth-guard-card animate-in">
        <p className="auth-guard-eyebrow">Lapwing Desktop</p>
        {manualMode ? (
          <>
            <h1>输入 Bootstrap Token</h1>
            <p className="auth-guard-hint">
              在远端主机查看 <code>~/.lapwing/auth/api-bootstrap-token</code>
            </p>
            <form onSubmit={handleSubmit} className="auth-guard-form">
              <textarea
                value={manualToken}
                onChange={(e) => setManualToken(e.target.value)}
                placeholder="粘贴 bootstrap token"
                rows={3}
              />
              <button type="submit" className="btn btn-primary" disabled={submitting}>
                {submitting ? "验证中…" : "建立会话"}
              </button>
            </form>
          </>
        ) : error ? (
          <>
            <h1>鉴权失败</h1>
            <p className="auth-guard-hint">{error}</p>
          </>
        ) : (
          <>
            <h1>正在连接…</h1>
            <p className="auth-guard-hint">读取 bootstrap token 并建立本地会话</p>
          </>
        )}
      </div>
    </div>
  );
}
