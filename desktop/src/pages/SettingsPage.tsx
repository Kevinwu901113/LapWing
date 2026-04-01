import { useEffect, useState } from "react";
import { Plus, X, RefreshCw, Shield, Server, Info } from "lucide-react";
import {
  getModelRoutingConfig,
  addModelRoutingProvider,
  updateModelRoutingProvider,
  removeModelRoutingProvider,
  assignModelRoutingSlot,
  reloadModelRouting,
  getPlatformConfig,
  getFeatureFlags,
  getAuthStatus,
  getStatus,
  type ModelRoutingConfig,
  type ModelRoutingProvider,
  type ModelInfo,
  type SlotAssignment,
  type PlatformConfig,
  type FeatureFlags,
  type AuthStatusResponse,
  type StatusResponse,
} from "../api";
import TabBar from "../components/TabBar";
import Toggle from "../components/Toggle";
import ProviderCard from "../components/ProviderCard";
import SlotCard from "../components/SlotCard";

// ── Constants ──────────────────────────────────────────────────────────────

const SLOT_ORDER = [
  "main_conversation",
  "persona_expression",
  "lightweight_judgment",
  "memory_processing",
  "self_reflection",
  "agent_execution",
  "heartbeat_proactive",
];

const TABS = [
  { id: "providers", label: "模型提供商" },
  { id: "slots", label: "槽位分配" },
  { id: "platforms", label: "平台连接" },
  { id: "features", label: "功能开关" },
  { id: "security", label: "安全" },
  { id: "server", label: "服务器" },
  { id: "about", label: "关于" },
];

// ── Types ──────────────────────────────────────────────────────────────────

type ProviderFormState = {
  id: string;
  name: string;
  base_url: string;
  api_key: string;
  api_type: string;
  models_raw: string;
};

function emptyForm(): ProviderFormState {
  return { id: "", name: "", base_url: "", api_key: "", api_type: "openai", models_raw: "" };
}

function providerToForm(p: ModelRoutingProvider): ProviderFormState {
  return {
    id: p.id,
    name: p.name,
    base_url: p.base_url,
    api_key: "",
    api_type: p.api_type,
    models_raw: p.models.map((m) => `${m.id},${m.name}`).join("\n"),
  };
}

function parseModels(raw: string): ModelInfo[] {
  return raw
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const comma = line.indexOf(",");
      if (comma === -1) return { id: line, name: line };
      return { id: line.slice(0, comma).trim(), name: line.slice(comma + 1).trim() };
    });
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("zh-CN", { hour12: false });
  } catch {
    return iso;
  }
}

// ── Sub-components ─────────────────────────────────────────────────────────

// Tab 1: Providers
function ProvidersTab({
  config,
  onRefresh,
}: {
  config: ModelRoutingConfig | null;
  onRefresh: () => Promise<void>;
}) {
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<ProviderFormState>(emptyForm());
  const [formError, setFormError] = useState("");

  function openAdd() {
    setForm(emptyForm());
    setEditingId(null);
    setFormError("");
    setShowForm(true);
  }

  function openEdit(p: ModelRoutingProvider) {
    setForm(providerToForm(p));
    setEditingId(p.id);
    setFormError("");
    setShowForm(true);
  }

  async function handleSubmit() {
    setFormError("");
    const models = parseModels(form.models_raw);
    try {
      if (editingId) {
        const updates: Parameters<typeof updateModelRoutingProvider>[1] = {
          name: form.name,
          base_url: form.base_url,
          api_type: form.api_type,
          models,
        };
        if (form.api_key) updates.api_key = form.api_key;
        await updateModelRoutingProvider(editingId, updates);
      } else {
        await addModelRoutingProvider({
          id: form.id,
          name: form.name,
          base_url: form.base_url,
          api_key: form.api_key,
          api_type: form.api_type,
          models,
        });
      }
      setShowForm(false);
      await onRefresh();
    } catch (e) {
      setFormError(String(e));
    }
  }

  async function handleDelete(providerId: string) {
    if (!confirm(`确认删除 Provider "${providerId}"？`)) return;
    try {
      await removeModelRoutingProvider(providerId);
      await onRefresh();
    } catch (e) {
      setFormError(String(e));
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h2 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>
          模型提供商
        </h2>
        <button className="btn btn-sm btn-ghost" onClick={openAdd}>
          <Plus size={13} />
          添加提供商
        </button>
      </div>

      {showForm && (
        <div className="card" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
              {editingId ? `编辑 ${editingId}` : "添加提供商"}
            </span>
            <button className="btn btn-sm btn-icon btn-ghost" onClick={() => setShowForm(false)}>
              <X size={14} />
            </button>
          </div>
          {!editingId && (
            <input
              className="routing-input"
              placeholder="ID（如 minimax）"
              value={form.id}
              onChange={(e) => setForm((f) => ({ ...f, id: e.target.value }))}
            />
          )}
          <input
            className="routing-input"
            placeholder="名称（如 MiniMax）"
            value={form.name}
            onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
          />
          <input
            className="routing-input"
            placeholder="Base URL"
            value={form.base_url}
            onChange={(e) => setForm((f) => ({ ...f, base_url: e.target.value }))}
          />
          <input
            className="routing-input"
            placeholder={editingId ? "API Key（留空则保持不变）" : "API Key"}
            type="password"
            value={form.api_key}
            onChange={(e) => setForm((f) => ({ ...f, api_key: e.target.value }))}
          />
          <select
            className="routing-select"
            value={form.api_type}
            onChange={(e) => setForm((f) => ({ ...f, api_type: e.target.value }))}
          >
            <option value="openai">OpenAI-compatible</option>
            <option value="minimax">MiniMax</option>
            <option value="anthropic">Anthropic</option>
          </select>
          <textarea
            className="routing-textarea"
            placeholder={"模型列表（每行一个，格式: model-id,显示名称）"}
            value={form.models_raw}
            onChange={(e) => setForm((f) => ({ ...f, models_raw: e.target.value }))}
            rows={4}
          />
          {formError && <div className="routing-error">{formError}</div>}
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <button className="btn btn-sm" onClick={() => setShowForm(false)}>
              取消
            </button>
            <button className="btn btn-sm btn-primary" onClick={() => void handleSubmit()}>
              {editingId ? "保存" : "添加"}
            </button>
          </div>
        </div>
      )}

      {!config || config.providers.length === 0 ? (
        <p className="empty-hint">尚未添加任何提供商。</p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {config.providers.map((p) => (
            <ProviderCard
              key={p.id}
              name={p.name}
              apiType={p.api_type}
              baseUrl={p.base_url}
              models={p.models.map((m) => m.name)}
              onEdit={() => openEdit(p)}
              onDelete={() => void handleDelete(p.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// Tab 2: Slot assignments
function SlotsTab({
  config,
  slotDraft,
  onProviderChange,
  onModelChange,
  onReload,
  saving,
  saveMsg,
}: {
  config: ModelRoutingConfig | null;
  slotDraft: Record<string, SlotAssignment>;
  onProviderChange: (slotId: string, providerId: string) => void;
  onModelChange: (slotId: string, modelId: string) => void;
  onReload: () => Promise<void>;
  saving: boolean;
  saveMsg: string;
}) {
  const providerOptions = (config?.providers ?? []).map((p) => ({
    id: p.id,
    name: p.name,
    models: p.models.map((m) => m.id),
  }));

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h2 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>
          槽位分配
        </h2>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          {saveMsg && (
            <span style={{ fontSize: 12, color: "var(--green)" }}>{saveMsg}</span>
          )}
          <button
            className="btn btn-sm btn-ghost"
            onClick={() => void onReload()}
            disabled={saving}
          >
            <RefreshCw size={13} />
            {saving ? "保存中…" : "重载路由"}
          </button>
        </div>
      </div>

      {!config || config.providers.length === 0 ? (
        <p className="empty-hint">请先在「模型提供商」标签中添加至少一个提供商。</p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {SLOT_ORDER.map((slotId) => {
            const def = config.slot_definitions[slotId];
            const draft = slotDraft[slotId];
            return (
              <SlotCard
                key={slotId}
                slotId={slotId}
                slotName={def?.name ?? slotId}
                description={def?.description}
                selectedProvider={draft?.provider_id ?? ""}
                selectedModel={draft?.model_id ?? ""}
                providers={providerOptions}
                onChange={(providerId, modelId) => {
                  onProviderChange(slotId, providerId);
                  onModelChange(slotId, modelId);
                }}
              />
            );
          })}
          <p style={{ fontSize: 12, color: "var(--text-muted)", margin: 0 }}>
            修改槽位分配后点击「重载路由」生效，无需重启。
          </p>
        </div>
      )}
    </div>
  );
}

// Tab 3: Platform connections
function PlatformsTab({ data }: { data: PlatformConfig | null }) {
  const platforms = [
    {
      key: "telegram",
      label: "Telegram",
      enabled: data?.telegram?.enabled ?? false,
      details: data?.telegram
        ? [
            data.telegram.token_preview
              ? `Token: ${data.telegram.token_preview}`
              : "Token: —",
            data.telegram.kevin_id ? `Kevin ID: ${data.telegram.kevin_id}` : null,
            data.telegram.proxy_url ? `代理: ${data.telegram.proxy_url}` : null,
          ].filter(Boolean)
        : [],
    },
    {
      key: "qq",
      label: "QQ (NapCat)",
      enabled: data?.qq?.enabled ?? false,
      details: data?.qq
        ? [
            data.qq.ws_url ? `WebSocket: ${data.qq.ws_url}` : "WS URL: —",
            data.qq.self_id ? `Bot ID: ${data.qq.self_id}` : null,
            data.qq.kevin_id ? `Kevin ID: ${data.qq.kevin_id}` : null,
            data.qq.cooldown_seconds != null
              ? `冷却: ${data.qq.cooldown_seconds}s`
              : null,
          ].filter(Boolean)
        : [],
    },
    {
      key: "desktop",
      label: "桌面端",
      enabled: data?.desktop?.enabled ?? false,
      details: data?.desktop
        ? [data.desktop.token_preview ? `Token: ${data.desktop.token_preview}` : null].filter(
            Boolean,
          )
        : [],
    },
  ];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <h2 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>
        平台连接
      </h2>
      <p style={{ margin: 0, fontSize: 12, color: "var(--text-muted)" }}>
        只读显示。修改需要重启服务器才能生效。
      </p>
      {!data ? (
        <p className="empty-hint">加载中…</p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {platforms.map((plat) => (
            <div key={plat.key} className="card">
              <div
                style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}
              >
                <span
                  style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}
                >
                  {plat.label}
                </span>
                <span
                  className={`badge ${plat.enabled ? "badge-green" : "badge-amber"}`}
                >
                  {plat.enabled ? "已启用" : "未启用"}
                </span>
              </div>
              {plat.details.length > 0 && (
                <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 2 }}>
                  {plat.details.map((d, i) => (
                    <span
                      key={i}
                      style={{ fontSize: 12, color: "var(--text-muted)", fontFamily: "var(--font-mono, monospace)" }}
                    >
                      {d}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      <div
        className="card"
        style={{ background: "var(--amber-dim, rgba(245,158,11,0.08))", borderColor: "var(--amber-dim, rgba(245,158,11,0.2))" }}
      >
        <p style={{ margin: 0, fontSize: 12, color: "var(--amber)" }}>
          修改平台配置需要通过环境变量或配置文件完成，并重启才能生效。
        </p>
      </div>
    </div>
  );
}

// Tab 4: Feature flags
function FeaturesTab({ data }: { data: FeatureFlags | null }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <h2 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>
        功能开关
      </h2>
      <p style={{ margin: 0, fontSize: 12, color: "var(--text-muted)" }}>
        只读显示，V1 暂不支持通过界面修改。
      </p>
      {!data ? (
        <p className="empty-hint">加载中…</p>
      ) : data.flags.length === 0 ? (
        <p className="empty-hint">暂无功能开关。</p>
      ) : (
        <div className="card" style={{ display: "flex", flexDirection: "column", gap: 0 }}>
          {data.flags.map((flag, i) => (
            <div
              key={flag.key}
              style={{
                padding: "12px 0",
                borderBottom: i < data.flags.length - 1 ? "1px solid var(--border)" : "none",
              }}
            >
              <Toggle
                checked={flag.enabled}
                onChange={() => undefined}
                label={flag.label}
                description={flag.description}
                disabled={true}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// Tab 5: Security
function SecurityTab({ data }: { data: AuthStatusResponse | null }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <Shield size={16} style={{ color: "var(--text-secondary)" }} />
        <h2 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>
          安全
        </h2>
      </div>
      {!data ? (
        <p className="empty-hint">加载中…</p>
      ) : (
        <>
          <div className="card">
            <p
              style={{ margin: "0 0 10px", fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}
            >
              服务认证
            </p>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <InfoRow label="受保护" value={data.serviceAuth.protected ? "是" : "否"} />
              <InfoRow label="Host" value={data.serviceAuth.host} />
              <InfoRow label="Cookie 名称" value={data.serviceAuth.cookieName} />
            </div>
          </div>

          <div className="card">
            <p
              style={{ margin: "0 0 10px", fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}
            >
              认证档案
            </p>
            {data.profiles.length === 0 ? (
              <p className="empty-hint" style={{ padding: "8px 0" }}>
                无认证档案。
              </p>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {data.profiles.map((profile) => (
                  <div
                    key={profile.profileId}
                    style={{
                      padding: "10px 12px",
                      background: "var(--bg-surface)",
                      borderRadius: "var(--radius-md)",
                      border: "1px solid var(--border)",
                    }}
                  >
                    <div
                      style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}
                    >
                      <span style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>
                        {profile.profileId}
                      </span>
                      <span
                        className={`badge ${
                          profile.status === "valid" ? "badge-green" : "badge-amber"
                        }`}
                      >
                        {profile.status}
                      </span>
                    </div>
                    <div style={{ display: "flex", gap: 16 }}>
                      <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                        {profile.provider} · {profile.type}
                      </span>
                      {profile.expiresAt && (
                        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                          过期: {formatDate(profile.expiresAt)}
                        </span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

// Tab 6: Server
function ServerTab({ data }: { data: AuthStatusResponse | null }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <Server size={16} style={{ color: "var(--text-secondary)" }} />
        <h2 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>
          服务器
        </h2>
      </div>
      <div className="card">
        <p
          style={{ margin: "0 0 10px", fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}
        >
          API 服务器
        </p>
        <InfoRow label="Host" value={data?.serviceAuth.host ?? "—"} />
      </div>
      <div
        className="card"
        style={{ background: "var(--bg-surface)" }}
      >
        <p style={{ margin: 0, fontSize: 12, color: "var(--text-muted)" }}>
          服务器配置通过环境变量管理，修改后需要重启才能生效。
        </p>
      </div>
    </div>
  );
}

// Tab 7: About
function AboutTab({ data }: { data: StatusResponse | null }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <Info size={16} style={{ color: "var(--text-secondary)" }} />
        <h2 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>
          关于
        </h2>
      </div>
      <div className="card">
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <InfoRow label="版本" value="Lapwing v0.2" />
          {data && (
            <>
              <InfoRow label="启动时间" value={formatDate(data.started_at)} />
              <InfoRow label="对话次数" value={String(data.chat_count)} />
              <InfoRow label="最后交互" value={formatDate(data.last_interaction)} />
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// Shared helper
function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
      <span style={{ fontSize: 12, color: "var(--text-muted)", flexShrink: 0 }}>{label}</span>
      <span
        style={{
          fontSize: 12,
          color: "var(--text-primary)",
          fontFamily: "var(--font-mono, monospace)",
          textAlign: "right",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {value}
      </span>
    </div>
  );
}

// ── Main SettingsPage ───────────────────────────────────────────────────────

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState("providers");

  // Model routing state
  const [config, setConfig] = useState<ModelRoutingConfig | null>(null);
  const [slotDraft, setSlotDraft] = useState<Record<string, SlotAssignment>>({});
  const [routingError, setRoutingError] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState("");

  // Other tabs data
  const [platformData, setPlatformData] = useState<PlatformConfig | null>(null);
  const [featureData, setFeatureData] = useState<FeatureFlags | null>(null);
  const [authData, setAuthData] = useState<AuthStatusResponse | null>(null);
  const [statusData, setStatusData] = useState<StatusResponse | null>(null);

  async function loadRouting() {
    try {
      const c = await getModelRoutingConfig();
      setConfig(c);
      setSlotDraft({ ...c.slots });
    } catch (e) {
      setRoutingError(String(e));
    }
  }

  useEffect(() => {
    void Promise.allSettled([
      loadRouting(),
      getPlatformConfig()
        .then(setPlatformData)
        .catch(() => undefined),
      getFeatureFlags()
        .then(setFeatureData)
        .catch(() => undefined),
      getAuthStatus()
        .then(setAuthData)
        .catch(() => undefined),
      getStatus()
        .then(setStatusData)
        .catch(() => undefined),
    ]);
  }, []);

  function handleSlotChange(slotId: string, providerId: string, modelId: string) {
    setSlotDraft((d) => ({ ...d, [slotId]: { provider_id: providerId, model_id: modelId } }));
  }

  // Slot provider change resets model to first available
  function handleSlotProviderChange(slotId: string, providerId: string) {
    const provider = config?.providers.find((p) => p.id === providerId);
    const firstModel = provider?.models[0]?.id ?? "";
    setSlotDraft((d) => ({ ...d, [slotId]: { provider_id: providerId, model_id: firstModel } }));
  }

  function handleSlotModelChange(slotId: string, modelId: string) {
    setSlotDraft((d) => ({
      ...d,
      [slotId]: { ...(d[slotId] ?? { provider_id: "", model_id: "" }), model_id: modelId },
    }));
  }

  async function handleReloadRouting() {
    if (!config) return;
    setSaving(true);
    setSaveMsg("");
    setRoutingError("");
    try {
      for (const slotId of SLOT_ORDER) {
        const draft = slotDraft[slotId];
        const current = config.slots[slotId];
        if (
          draft &&
          (current?.provider_id !== draft.provider_id || current?.model_id !== draft.model_id)
        ) {
          await assignModelRoutingSlot(slotId, draft.provider_id, draft.model_id);
        }
      }
      await reloadModelRouting();
      await loadRouting();
      setSaveMsg("已保存并应用");
      setTimeout(() => setSaveMsg(""), 3000);
    } catch (e) {
      setRoutingError(String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="page">
      <header className="page-header animate-in">
        <div>
          <h1 className="page-title">设置</h1>
          <p className="page-subtitle" style={{ fontSize: 13, color: "var(--text-muted)", margin: 0 }}>
            系统配置与信息
          </p>
        </div>
      </header>

      {routingError && (
        <div className="routing-error" style={{ marginBottom: 12 }}>
          {routingError}
        </div>
      )}

      <div className="settings-layout">
        {/* Left sidebar */}
        <nav className="settings-nav">
          <TabBar
            tabs={TABS}
            activeTab={activeTab}
            onChange={setActiveTab}
            orientation="vertical"
          />
        </nav>

        {/* Content area */}
        <div className="settings-content">
          {activeTab === "providers" && (
            <ProvidersTab config={config} onRefresh={loadRouting} />
          )}
          {activeTab === "slots" && (
            <SlotsTab
              config={config}
              slotDraft={slotDraft}
              onProviderChange={handleSlotProviderChange}
              onModelChange={handleSlotModelChange}
              onReload={handleReloadRouting}
              saving={saving}
              saveMsg={saveMsg}
            />
          )}
          {activeTab === "platforms" && <PlatformsTab data={platformData} />}
          {activeTab === "features" && <FeaturesTab data={featureData} />}
          {activeTab === "security" && <SecurityTab data={authData} />}
          {activeTab === "server" && <ServerTab data={authData} />}
          {activeTab === "about" && <AboutTab data={statusData} />}
        </div>
      </div>
    </div>
  );
}
