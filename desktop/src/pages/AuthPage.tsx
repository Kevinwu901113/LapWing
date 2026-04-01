import { useEffect, useState } from "react";
import { KeyRound, Download, ExternalLink } from "lucide-react";
import {
  getAuthStatus, importCodexCache, startOpenAICodexOAuth,
  getOAuthLoginSession,
  type AuthStatusResponse, type OAuthLoginSession,
} from "../api";

function formatDate(v: string | null) {
  return v ? new Date(v).toLocaleString("zh-CN") : "—";
}

export default function AuthPage() {
  const [authStatus, setAuthStatus] = useState<AuthStatusResponse | null>(null);
  const [importing, setImporting] = useState(false);
  const [startingOAuth, setStartingOAuth] = useState(false);
  const [oauthSession, setOAuthSession] = useState<OAuthLoginSession | null>(null);
  const [oauthNotice, setOAuthNotice] = useState("");

  useEffect(() => {
    void getAuthStatus().then(setAuthStatus);
  }, []);

  // OAuth 轮询
  useEffect(() => {
    if (!oauthSession || !["pending", "completing"].includes(oauthSession.status)) return;
    const timer = setInterval(async () => {
      try {
        const next = await getOAuthLoginSession(oauthSession.loginId);
        setOAuthSession(next);
        if (next.status === "completed") {
          setOAuthNotice(next.completionMessage ?? "OpenAI 登录成功。");
          void getAuthStatus().then(setAuthStatus);
        } else if (["failed", "expired"].includes(next.status)) {
          setOAuthNotice(next.error ?? "登录未完成。");
        }
      } catch {}
    }, 1500);
    return () => clearInterval(timer);
  }, [oauthSession]);

  async function handleImport() {
    setImporting(true);
    try {
      await importCodexCache();
      void getAuthStatus().then(setAuthStatus);
    } finally {
      setImporting(false);
    }
  }

  async function handleOAuth() {
    setStartingOAuth(true);
    try {
      const returnTo = ["http:", "https:"].includes(window.location.protocol)
        ? window.location.href : undefined;
      const session = await startOpenAICodexOAuth(returnTo);
      setOAuthSession(session);
      setOAuthNotice("授权页面已就绪，完成后自动刷新。");
      window.open(session.authorizeUrl, "_blank", "noopener,noreferrer");
    } catch (err) {
      setOAuthNotice(err instanceof Error ? err.message : String(err));
    } finally {
      setStartingOAuth(false);
    }
  }

  return (
    <div className="page">
      <header className="page-header animate-in">
        <div>
          <h1 className="page-title">认证</h1>
          <p className="page-subtitle">Auth Profiles、OAuth 和本机 API 安全</p>
        </div>
        <div className="page-header-actions">
          <button className="btn btn-primary" onClick={handleOAuth}
            disabled={startingOAuth || oauthSession?.status === "pending"}>
            <KeyRound size={16} />
            {startingOAuth ? "跳转中…" : "登录 OpenAI"}
          </button>
          <button className="btn btn-soft" onClick={handleImport} disabled={importing}>
            <Download size={16} />
            {importing ? "导入中…" : "导入 Codex auth.json"}
          </button>
        </div>
      </header>

      <div className="stat-grid-2">
        <div className="card">
          <p className="card-title">服务认证</p>
          <div>
            {[
              { k: "Host", v: authStatus?.serviceAuth.host ?? "127.0.0.1" },
              { k: "Cookie", v: authStatus?.serviceAuth.cookieName ?? "lapwing_session" },
              { k: "保护状态", v: authStatus?.serviceAuth.protected ? "✓ 已保护" : "✗ 未保护" },
            ].map((r) => (
              <div key={r.k} style={{ display: "flex", justifyContent: "space-between", padding: "4px 0" }}>
                <span style={{ fontSize: 12, color: "var(--text-muted)" }}>{r.k}</span>
                <span style={{ fontSize: 13, color: "var(--text-primary)" }}>{r.v}</span>
              </div>
            ))}
          </div>
          {oauthNotice && <p style={{ marginTop: 8, fontSize: 12, color: "var(--text-secondary)" }}>{oauthNotice}</p>}
          {oauthSession?.authorizeUrl && ["pending", "failed", "expired"].includes(oauthSession.status) && (
            <p style={{ marginTop: 8, fontSize: 12, color: "var(--text-secondary)" }}>
              浏览器未自动打开？{" "}
              <a href={oauthSession.authorizeUrl} target="_blank" rel="noreferrer" style={{ color: "var(--accent)" }}>
                点击手动授权 <ExternalLink size={12} />
              </a>
            </p>
          )}
        </div>

        <div className="card">
          <p className="card-title">Auth Profiles</p>
          {(authStatus?.profiles ?? []).length === 0 ? (
            <p className="empty-hint">尚未导入或登录任何 auth profile。</p>
          ) : (
            <div>
              {authStatus!.profiles.map((p) => (
                <div key={p.profileId} style={{ padding: "6px 0", borderBottom: "1px solid var(--border)" }}>
                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>{p.profileId}</span>
                    <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>{p.provider} · {p.type}</span>
                  </div>
                  <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                    {p.status}
                    {p.reasonCode ? ` · ${p.reasonCode}` : ""}
                    {p.expiresAt ? ` · 到期 ${formatDate(p.expiresAt)}` : ""}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <p className="card-title">路由配置</p>
        {Object.keys(authStatus?.routes ?? {}).length === 0 ? (
          <p className="empty-hint">暂无路由配置。</p>
        ) : (
          <div>
            {Object.entries(authStatus!.routes!).map(([purpose, route]) => (
              <div key={purpose} style={{ padding: "6px 0", borderBottom: "1px solid var(--border)" }}>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>{purpose}</span>
                  <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>{route.provider ?? "auto"} · {route.model}</span>
                </div>
                <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                  {route.baseUrl}
                  {route.bindingMismatch ? " · ⚠ binding 不一致" : ""}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
