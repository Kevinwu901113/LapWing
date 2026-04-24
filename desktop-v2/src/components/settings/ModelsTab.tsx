import { useEffect, useState, useCallback } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import {
  createModelProvider,
  deleteModelProvider,
  getModelRouting,
  updateModelProvider,
  updateModelRouting,
} from "@/lib/api-v2";
import type {
  ModelInfo,
  ModelProvider,
  ModelRoutingConfig,
  ProviderPayload,
  SlotDisplayItem,
} from "@/types/models";

function flattenSlots(config: ModelRoutingConfig): SlotDisplayItem[] {
  return Object.entries(config.slots).map(([slot, assignment]) => ({
    slot,
    provider_id: assignment.provider_id,
    model_id: assignment.model_id,
    model_ref: assignment.model_ref,
    fallback_model_ids: assignment.fallback_model_ids,
    description: config.slot_definitions?.[slot]?.description ?? "",
  }));
}

function providerToPayload(provider: ModelProvider): ProviderPayload {
  return {
    id: provider.id,
    name: provider.name,
    base_url: provider.base_url ?? "",
    api_type: provider.api_type ?? "openai",
    auth_type: provider.auth_type ?? "api_key",
    api_key_env: provider.api_key_env ?? "",
    protocol: provider.protocol ?? "",
    models: provider.models ?? [],
  };
}

function serializeModels(models: ModelInfo[] | undefined): string {
  return (models ?? []).map((m) => (m.name && m.name !== m.id ? `${m.id} | ${m.name}` : m.id)).join("\n");
}

function parseModels(raw: string): ModelInfo[] {
  return raw
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [idPart, namePart] = line.split("|", 2);
      const id = idPart.trim();
      const name = (namePart ?? id).trim();
      return { id, name: name || id };
    });
}

function providerModels(config: ModelRoutingConfig, providerId: string): ModelInfo[] {
  return config.providers.find((p) => p.id === providerId)?.models ?? [];
}

export function ModelsTab() {
  const [config, setConfig] = useState<ModelRoutingConfig | null>(null);
  const [slotEdits, setSlotEdits] = useState<Map<string, { provider_id: string; model_id: string }>>(new Map());
  const [providerDrafts, setProviderDrafts] = useState<Record<string, ProviderPayload>>({});
  const [newProvider, setNewProvider] = useState<ProviderPayload>({
    id: "",
    name: "",
    base_url: "",
    api_type: "openai",
    auth_type: "api_key",
    api_key_env: "",
    protocol: "",
    models: [],
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchConfig = useCallback(async () => {
    try {
      const data = await getModelRouting();
      setConfig(data);
      setProviderDrafts(Object.fromEntries(data.providers.map((p) => [p.id, providerToPayload(p)])));
    } catch {
      setError("Failed to load model config");
    }
  }, []);

  useEffect(() => {
    fetchConfig();
  }, [fetchConfig]);

  const handleSlotEdit = (slot: string, field: "provider_id" | "model_id", value: string) => {
    if (!config) return;
    const assignment = config.slots[slot];
    const current = slotEdits.get(slot) ?? {
      provider_id: assignment?.provider_id ?? "",
      model_id: assignment?.model_id ?? "",
    };
    const next = { ...current, [field]: value };
    if (field === "provider_id") {
      next.model_id = providerModels(config, value)[0]?.id ?? "";
    }
    setSlotEdits(new Map(slotEdits).set(slot, next));
  };

  const handleSaveSlots = async () => {
    if (slotEdits.size === 0) return;
    setSaving(true);
    setError(null);
    try {
      const payload: Record<string, { provider_id: string; model_id: string }> = {};
      slotEdits.forEach((v, k) => { payload[k] = v; });
      await updateModelRouting(payload);
      setSlotEdits(new Map());
      await fetchConfig();
    } catch (e) {
      setError(`Save failed: ${e}`);
    } finally {
      setSaving(false);
    }
  };

  const updateDraft = (providerId: string, patch: Partial<ProviderPayload>) => {
    const current = providerDrafts[providerId];
    if (!current) return;
    setProviderDrafts({ ...providerDrafts, [providerId]: { ...current, ...patch } });
  };

  const saveProvider = async (providerId: string) => {
    const draft = providerDrafts[providerId];
    if (!draft) return;
    setSaving(true);
    setError(null);
    try {
      const { id: _id, api_key, ...payload } = draft;
      await updateModelProvider(providerId, {
        ...payload,
        ...(api_key ? { api_key } : {}),
      });
      await fetchConfig();
    } catch (e) {
      setError(`Provider save failed: ${e}`);
    } finally {
      setSaving(false);
    }
  };

  const removeProvider = async (providerId: string) => {
    setSaving(true);
    setError(null);
    try {
      await deleteModelProvider(providerId);
      await fetchConfig();
    } catch (e) {
      setError(`Provider delete failed: ${e}`);
    } finally {
      setSaving(false);
    }
  };

  const addProvider = async () => {
    if (!newProvider.id.trim() || !newProvider.name.trim()) {
      setError("Provider id and name are required");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await createModelProvider(newProvider);
      setNewProvider({
        id: "",
        name: "",
        base_url: "",
        api_type: "openai",
        auth_type: "api_key",
        api_key_env: "",
        protocol: "",
        models: [],
      });
      await fetchConfig();
    } catch (e) {
      setError(`Provider create failed: ${e}`);
    } finally {
      setSaving(false);
    }
  };

  if (!config) {
    return <div className="text-sm text-text-muted py-4">Loading...</div>;
  }

  const slots = flattenSlots(config);

  return (
    <div className="space-y-5 max-w-5xl">
      {error && <p className="text-xs text-red-400">{error}</p>}

      <section className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-sm font-medium text-text-accent">Slot assignments</h2>
          <div className="flex gap-2">
            {slotEdits.size > 0 && (
              <Button variant="outline" size="sm" onClick={() => setSlotEdits(new Map())}>
                Reset
              </Button>
            )}
            <Button
              size="sm"
              onClick={handleSaveSlots}
              disabled={saving || slotEdits.size === 0}
              className="bg-lapwing text-void hover:bg-lapwing-dark"
            >
              {saving ? "Saving..." : "Save slots"}
            </Button>
          </div>
        </div>

        <div className="space-y-2">
          {slots.map((slot) => {
            const edit = slotEdits.get(slot.slot);
            const providerId = edit?.provider_id ?? slot.provider_id;
            const modelId = edit?.model_id ?? slot.model_id;
            const models = providerModels(config, providerId);
            return (
              <div key={slot.slot} className="border border-surface-border bg-surface px-3 py-3 rounded-lg">
                <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
                  <div>
                    <div className="text-sm font-medium text-text-accent">{slot.slot}</div>
                    {slot.description && <div className="text-xs text-text-muted">{slot.description}</div>}
                  </div>
                  {slot.model_ref && <Badge variant="outline">{slot.model_ref}</Badge>}
                </div>
                <div className="grid gap-3 md:grid-cols-2">
                  <label className="space-y-1">
                    <span className="text-xs text-text-muted">Provider</span>
                    <select
                      value={providerId}
                      onChange={(e) => handleSlotEdit(slot.slot, "provider_id", e.target.value)}
                      className="h-8 w-full rounded-lg border border-surface-border bg-void-50 px-2 text-sm text-text-primary"
                    >
                      <option value="">Unassigned</option>
                      {config.providers.map((p) => (
                        <option key={p.id} value={p.id}>{p.name} ({p.id})</option>
                      ))}
                    </select>
                  </label>
                  <label className="space-y-1">
                    <span className="text-xs text-text-muted">Model</span>
                    <select
                      value={modelId}
                      onChange={(e) => handleSlotEdit(slot.slot, "model_id", e.target.value)}
                      className="h-8 w-full rounded-lg border border-surface-border bg-void-50 px-2 text-sm text-text-primary"
                    >
                      <option value="">Unassigned</option>
                      {models.map((m) => (
                        <option key={m.id} value={m.id}>{m.name || m.id}</option>
                      ))}
                    </select>
                  </label>
                </div>
              </div>
            );
          })}
        </div>
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-medium text-text-accent">Provider registry</h2>
        <div className="space-y-3">
          {config.providers.map((provider) => {
            const draft = providerDrafts[provider.id] ?? providerToPayload(provider);
            return (
              <div key={provider.id} className="border border-surface-border bg-surface px-3 py-3 rounded-lg space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-text-accent">{provider.name}</span>
                    <Badge variant="outline">{provider.id}</Badge>
                    <Badge variant="secondary">{provider.api_type}</Badge>
                  </div>
                  <div className="flex gap-2">
                    <Button size="sm" variant="outline" disabled={saving} onClick={() => saveProvider(provider.id)}>
                      Save
                    </Button>
                    <Button size="sm" variant="destructive" disabled={saving} onClick={() => removeProvider(provider.id)}>
                      Delete
                    </Button>
                  </div>
                </div>
                <div className="grid gap-3 md:grid-cols-2">
                  <Input value={draft.name} onChange={(e) => updateDraft(provider.id, { name: e.target.value })} placeholder="Display name" />
                  <Input value={draft.base_url} onChange={(e) => updateDraft(provider.id, { base_url: e.target.value })} placeholder="Base URL" />
                  <select
                    value={draft.api_type}
                    onChange={(e) => updateDraft(provider.id, { api_type: e.target.value })}
                    className="h-8 rounded-lg border border-surface-border bg-void-50 px-2 text-sm text-text-primary"
                  >
                    <option value="anthropic">anthropic</option>
                    <option value="openai">openai</option>
                    <option value="codex_oauth">codex_oauth</option>
                  </select>
                  <select
                    value={draft.auth_type ?? "api_key"}
                    onChange={(e) => updateDraft(provider.id, { auth_type: e.target.value })}
                    className="h-8 rounded-lg border border-surface-border bg-void-50 px-2 text-sm text-text-primary"
                  >
                    <option value="api_key">api_key</option>
                    <option value="oauth">oauth</option>
                    <option value="none">none</option>
                  </select>
                  <Input value={draft.api_key_env ?? ""} onChange={(e) => updateDraft(provider.id, { api_key_env: e.target.value })} placeholder="API key env, e.g. LLM_API_KEY" />
                  <Input value={draft.protocol ?? ""} onChange={(e) => updateDraft(provider.id, { protocol: e.target.value })} placeholder="Protocol override" />
                </div>
                <Textarea
                  value={serializeModels(draft.models)}
                  onChange={(e) => updateDraft(provider.id, { models: parseModels(e.target.value) })}
                  className="min-h-20 bg-void-50 border-surface-border text-text-primary text-sm"
                  placeholder={"model-id | Display name\nanother-model"}
                />
              </div>
            );
          })}
        </div>
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-medium text-text-accent">Add provider</h2>
        <div className="border border-surface-border bg-surface px-3 py-3 rounded-lg space-y-3">
          <div className="grid gap-3 md:grid-cols-2">
            <Input value={newProvider.id} onChange={(e) => setNewProvider({ ...newProvider, id: e.target.value })} placeholder="provider-id" />
            <Input value={newProvider.name} onChange={(e) => setNewProvider({ ...newProvider, name: e.target.value })} placeholder="Display name" />
            <Input value={newProvider.base_url} onChange={(e) => setNewProvider({ ...newProvider, base_url: e.target.value })} placeholder="Base URL" />
            <Input value={newProvider.api_key_env ?? ""} onChange={(e) => setNewProvider({ ...newProvider, api_key_env: e.target.value })} placeholder="API key env" />
            <select
              value={newProvider.api_type}
              onChange={(e) => setNewProvider({ ...newProvider, api_type: e.target.value })}
              className="h-8 rounded-lg border border-surface-border bg-void-50 px-2 text-sm text-text-primary"
            >
              <option value="anthropic">anthropic</option>
              <option value="openai">openai</option>
              <option value="codex_oauth">codex_oauth</option>
            </select>
            <select
              value={newProvider.auth_type ?? "api_key"}
              onChange={(e) => setNewProvider({ ...newProvider, auth_type: e.target.value })}
              className="h-8 rounded-lg border border-surface-border bg-void-50 px-2 text-sm text-text-primary"
            >
              <option value="api_key">api_key</option>
              <option value="oauth">oauth</option>
              <option value="none">none</option>
            </select>
          </div>
          <Textarea
            value={serializeModels(newProvider.models)}
            onChange={(e) => setNewProvider({ ...newProvider, models: parseModels(e.target.value) })}
            className="min-h-20 bg-void-50 border-surface-border text-text-primary text-sm"
            placeholder={"model-id | Display name\nanother-model"}
          />
          <Button size="sm" disabled={saving} onClick={addProvider} className="bg-lapwing text-void hover:bg-lapwing-dark">
            Add provider
          </Button>
        </div>
      </section>
    </div>
  );
}
