import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { createDesktopToken, getStatus } from "../api";
import "../styles/pages.css";

export default function ConnectionPage() {
  const navigate = useNavigate();
  const [serverUrl, setServerUrl] = useState("http://127.0.0.1:8765");
  const [bootstrapToken, setBootstrapToken] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(true);

  // On mount: if already have token + server URL, verify and redirect
  useEffect(() => {
    const token = localStorage.getItem("lapwing_token");
    const savedUrl = localStorage.getItem("lapwing_server_url");
    if (token && savedUrl) {
      getStatus()
        .then(() => {
          navigate("/", { replace: true });
        })
        .catch(() => {
          // Verification failed — stay on connect page
          setChecking(false);
        });
    } else {
      setChecking(false);
    }
  }, [navigate]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      // Store server URL first so fetchJson can use it on next load
      localStorage.setItem("lapwing_server_url", serverUrl.trim());

      // Call the backend — note: api.ts reads API_BASE at module load time,
      // so this call still uses the original module-level constant.
      // Step 12 will rewrite api.ts to read from localStorage dynamically.
      const result = await createDesktopToken(bootstrapToken);
      localStorage.setItem("lapwing_token", result.token);
      navigate("/", { replace: true });
    } catch (err) {
      localStorage.removeItem("lapwing_server_url");
      setError(err instanceof Error ? err.message : "连接失败");
    } finally {
      setLoading(false);
    }
  }

  if (checking) {
    // Briefly show nothing while we verify existing credentials
    return null;
  }

  return (
    <div className="connect-page">
      <div className="connect-card">
        <div style={{ textAlign: "center" }}>
          <div style={{ fontSize: 32, fontWeight: 700, letterSpacing: "-0.5px", color: "var(--accent)" }}>
            Lapwing
          </div>
          <div style={{ fontSize: 14, color: "var(--text-muted)", marginTop: 4 }}>
            连接到 Lapwing 服务器
          </div>
        </div>

        <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <label style={{ fontSize: 13, color: "var(--text-secondary)", fontWeight: 500 }}>
              服务器地址
            </label>
            <input
              type="text"
              value={serverUrl}
              onChange={(e) => setServerUrl(e.target.value)}
              placeholder="http://host:port"
              required
              disabled={loading}
              style={{
                background: "var(--bg-input, var(--bg-base))",
                border: "1px solid var(--border)",
                borderRadius: "var(--radius-md, 6px)",
                color: "var(--text-primary)",
                fontSize: 14,
                padding: "8px 12px",
                outline: "none",
                width: "100%",
                boxSizing: "border-box",
              }}
            />
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <label style={{ fontSize: 13, color: "var(--text-secondary)", fontWeight: 500 }}>
              启动令牌
            </label>
            <input
              type="password"
              value={bootstrapToken}
              onChange={(e) => setBootstrapToken(e.target.value)}
              placeholder="粘贴启动令牌"
              required
              disabled={loading}
              style={{
                background: "var(--bg-input, var(--bg-base))",
                border: "1px solid var(--border)",
                borderRadius: "var(--radius-md, 6px)",
                color: "var(--text-primary)",
                fontSize: 14,
                padding: "8px 12px",
                outline: "none",
                width: "100%",
                boxSizing: "border-box",
              }}
            />
          </div>

          {error && (
            <div
              style={{
                fontSize: 13,
                color: "var(--error, #ef4444)",
                background: "var(--error-bg, rgba(239,68,68,0.08))",
                border: "1px solid var(--error-border, rgba(239,68,68,0.2))",
                borderRadius: "var(--radius-md, 6px)",
                padding: "8px 12px",
              }}
            >
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            style={{
              background: "var(--accent)",
              color: "#fff",
              border: "none",
              borderRadius: "var(--radius-md, 6px)",
              fontSize: 14,
              fontWeight: 600,
              padding: "10px 0",
              cursor: loading ? "not-allowed" : "pointer",
              opacity: loading ? 0.7 : 1,
              transition: "opacity 0.15s",
            }}
          >
            {loading ? "连接中…" : "连接"}
          </button>
        </form>
      </div>
    </div>
  );
}
