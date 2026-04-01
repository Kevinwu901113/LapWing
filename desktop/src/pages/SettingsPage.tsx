import { useEffect, useState } from "react";
import { Plus, Pencil, Trash2, Save, Server, X } from "lucide-react";
import {
  getModelRoutingConfig,
  addModelRoutingProvider,
  updateModelRoutingProvider,
  removeModelRoutingProvider,
  assignModelRoutingSlot,
  reloadModelRouting,
  type ModelRoutingConfig,
  type ModelRoutingProvider,
  type ModelInfo,
  type SlotAssignment,
} from "../api";

const SLOT_ORDER = [
  "main_conversation",
  "persona_expression",
  "lightweight_judgment",
  "memory_processing",
  "self_reflection",
  "agent_execution",
  "heartbeat_proactive",
];

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

export default function SettingsPage() {
  const [config, setConfig] = useState<ModelRoutingConfig | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState("");
  const [slotDraft, setSlotDraft] = useState<Record<string, SlotAssignment>>({});
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<ProviderFormState>(emptyForm());
  const [formError, setFormError] = useState("");

  async function load() {
    try {
      const c = await getModelRoutingConfig();
      setConfig(c);
      setSlotDraft({ ...c.slots });
    } catch (e) {
      setError(String(e));
    }
  }

  useEffect(() => {
    void load();
  }, []);

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

  async function handleFormSubmit() {
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
      await load();
    } catch (e) {
      setFormError(String(e));
    }
  }

  async function handleDelete(providerId: string) {
    if (!confirm(`确认删除 Provider "${providerId}"？`)) return;
    try {
      await removeModelRoutingProvider(providerId);
      await load();
    } catch (e) {
      setError(String(e));
    }
  }

  async function handleSaveAll() {
    if (!config) return;
    setSaving(true);
    setSaveMsg("");
    setError("");
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
      await load();
      setSaveMsg("已保存并应用");
      setTimeout(() => setSaveMsg(""), 3000);
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

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

  if (!config && !error) {
    return (
      <div className="page">
        <header className="page-header animate-in">
          <div>
            <h1 className="page-title">模型路由</h1>
            <p className="page-subtitle">加载中…</p>
          </div>
        </header>
      </div>
    );
  }

  return (
    <div className="page">
      <header className="page-header animate-in">
        <div>
          <h1 className="page-title">模型路由</h1>
          <p className="page-subtitle">管理 AI Provider 与模型分配</p>
        </div>
        <div className="page-header-actions">
          {saveMsg && <span className="routing-save-msg">{saveMsg}</span>}
          <button className="btn btn-primary" onClick={() => void handleSaveAll()} disabled={saving}>
            <Save size={14} />
            {saving ? "保存中…" : "保存并应用"}
          </button>
        </div>
      </header>

      {error && <div className="routing-error">{error}</div>}

      <div className="card" style={{ marginBottom: 16 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <p className="card-title" style={{ margin: 0 }}>Provider 注册</p>
          <button className="btn btn-sm btn-ghost" onClick={openAdd}>
            <Plus size={13} />
            添加 Provider
          </button>
        </div>
        {showForm && (
          <div className="routing-form">
            <div className="routing-form-head">
              <h3>{editingId ? `编辑 ${editingId}` : "添加 Provider"}</h3>
              <button className="btn btn-sm btn-icon" onClick={() => setShowForm(false)}>
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
              <option value="anthropic">Anthropic</option>
              <option value="openai_codex">OpenAI Codex</option>
            </select>
            <textarea
              className="routing-textarea"
              placeholder={"模型列表（每行一个，格式: model-id,显示名称）"}
              value={form.models_raw}
              onChange={(e) => setForm((f) => ({ ...f, models_raw: e.target.value }))}
              rows={4}
            />
            {formError && <div className="routing-error">{formError}</div>}
            <div className="routing-form-actions">
              <button className="btn btn-sm" onClick={() => setShowForm(false)}>取消</button>
              <button className="btn btn-sm btn-primary" onClick={() => void handleFormSubmit()}>
                {editingId ? "保存" : "添加"}
              </button>
            </div>
          </div>
        )}

        {!config || config.providers.length === 0 ? (
          <p className="empty-hint">尚未添加任何 Provider。</p>
        ) : (
          <div className="list-stack">
            {config.providers.map((p) => (
              <div key={p.id} className="routing-provider-card">
                <div className="routing-provider-icon">
                  <Server size={16} />
                </div>
                <div className="routing-provider-info">
                  <div className="routing-provider-name">{p.name}</div>
                  <div className="routing-provider-url">{p.base_url}</div>
                  <div className="routing-provider-meta">
                    <span className="routing-badge">{p.api_type}</span>
                    <span className="list-row-muted">
                      {p.models.map((m) => m.name).join(" · ")}
                    </span>
                  </div>
                </div>
                <div className="routing-provider-actions">
                  <button className="btn btn-sm btn-icon" onClick={() => openEdit(p)} title="编辑">
                    <Pencil size={13} />
                  </button>
                  <button
                    className="btn btn-sm btn-icon btn-danger"
                    onClick={() => void handleDelete(p.id)}
                    title="删除"
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="card">
        <p className="card-title">模型分配</p>
        {!config || config.providers.length === 0 ? (
          <p className="empty-hint">请先添加至少一个 Provider。</p>
        ) : (
          <div className="routing-slots">
            {SLOT_ORDER.map((slotId) => {
              const def = config.slot_definitions[slotId];
              const draft = slotDraft[slotId];
              const selectedProvider = config.providers.find((p) => p.id === draft?.provider_id);
              return (
                <div key={slotId} className="routing-slot-row">
                  <div className="routing-slot-info">
                    <div className="routing-slot-name">{def?.name ?? slotId}</div>
                    <div className="routing-slot-desc">{def?.description}</div>
                  </div>
                  <div className="routing-slot-controls">
                    <select
                      className="routing-select"
                      value={draft?.provider_id ?? ""}
                      onChange={(e) => handleSlotProviderChange(slotId, e.target.value)}
                    >
                      <option value="">— 选择 Provider —</option>
                      {config.providers.map((p) => (
                        <option key={p.id} value={p.id}>{p.name}</option>
                      ))}
                    </select>
                    <select
                      className="routing-select"
                      value={draft?.model_id ?? ""}
                      onChange={(e) => handleSlotModelChange(slotId, e.target.value)}
                      disabled={!selectedProvider}
                    >
                      <option value="">— 选择模型 —</option>
                      {(selectedProvider?.models ?? []).map((m) => (
                        <option key={m.id} value={m.id}>{m.name}</option>
                      ))}
                    </select>
                  </div>
                </div>
              );
            })}
            <p className="routing-hint">
              修改分配后点击「保存并应用」生效，无需重启。
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
