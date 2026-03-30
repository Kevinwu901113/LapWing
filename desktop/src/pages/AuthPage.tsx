import { useEffect, useState } from "react";
import { KeyRound, Download, ExternalLink } from "lucide-react";
import {
  getAuthStatus, importCodexCache, startOpenAICodexOAuth,
  getOAuthLoginSession,
  type AuthStatusResponse, type OAuthLoginSession,
} from "../api";
import DataCard from "../components/DataCard";
import EmptyState from "../components/EmptyState";

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

      <div className="two-col">
        {/* 服务状态 */}
        <DataCard title="服务认证" className="stagger-1">
          <div className="list-stack">
            <div className="list-row">
              <span className="list-row-key">Host</span>
              <span>{authStatus?.serviceAuth.host ?? "127.0.0.1"}</span>
            </div>
            <div className="list-row">
              <span className="list-row-key">Cookie</span>
              <span>{authStatus?.serviceAuth.cookieName ?? "lapwing_session"}</span>
            </div>
            <div className="list-row">
              <span className="list-row-key">保护状态</span>
              <span>{authStatus?.serviceAuth.protected ? "✓ 已保护" : "✗ 未保护"}</span>
            </div>
          </div>
          {oauthNotice && <p className="auth-notice">{oauthNotice}</p>}
          {oauthSession?.authorizeUrl && ["pending", "failed", "expired"].includes(oauthSession.status) && (
            <p className="auth-notice">
              浏览器未自动打开？{" "}
              <a href={oauthSession.authorizeUrl} target="_blank" rel="noreferrer"
                className="auth-link">
                点击手动授权 <ExternalLink size={12} />
              </a>
            </p>
          )}
        </DataCard>

        {/* Profiles */}
        <DataCard title="Auth Profiles" className="stagger-2">
          {(authStatus?.profiles ?? []).length === 0 ? (
            <EmptyState message="尚未导入或登录任何 auth profile。" />
          ) : (
            <div className="list-stack">
              {authStatus!.profiles.map((p) => (
                <div key={p.profileId} className="list-row-block">
                  <div className="list-row">
                    <span className="list-row-key">{p.profileId}</span>
                    <span>{p.provider} · {p.type}</span>
                  </div>
                  <span className="list-row-muted">
                    {p.status}
                    {p.reasonCode ? ` · ${p.reasonCode}` : ""}
                    {p.expiresAt ? ` · 到期 ${formatDate(p.expiresAt)}` : ""}
                  </span>
                </div>
              ))}
            </div>
          )}
        </DataCard>
      </div>

      {/* Routes */}
      <DataCard title="路由配置" className="stagger-3">
        {Object.keys(authStatus?.routes ?? {}).length === 0 ? (
          <EmptyState message="暂无路由配置。" />
        ) : (
          <div className="list-stack">
            {Object.entries(authStatus!.routes!).map(([purpose, route]) => (
              <div key={purpose} className="list-row-block">
                <div className="list-row">
                  <span className="list-row-key">{purpose}</span>
                  <span>{route.provider || "auto"} · {route.model}</span>
                </div>
                <span className="list-row-muted">
                  {route.baseUrl}
                  {route.bindingMismatch ? " · ⚠ binding 不一致" : ""}
                </span>
              </div>
            ))}
          </div>
        )}
      </DataCard>
    </div>
  );
}
