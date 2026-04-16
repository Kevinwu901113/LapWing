import { useEffect, useState, useCallback } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { getModelRouting, updateModelRouting } from "@/lib/api-v2";
import type { ModelRoutingConfig, SlotDisplayItem } from "@/types/models";

/** Flatten dict-based slots into an array for display */
function flattenSlots(config: ModelRoutingConfig): SlotDisplayItem[] {
  return Object.entries(config.slots).map(([slot, assignment]) => ({
    slot,
    provider_id: assignment.provider_id,
    model_id: assignment.model_id,
    description: config.slot_definitions?.[slot]?.description ?? "",
  }));
}

export function ModelsTab() {
  const [config, setConfig] = useState<ModelRoutingConfig | null>(null);
  const [edits, setEdits] = useState<Map<string, { provider_id: string; model_id: string }>>(new Map());
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchConfig = useCallback(async () => {
    try {
      const data = await getModelRouting();
      setConfig(data);
    } catch {
      setError("Failed to load model config");
    }
  }, []);

  useEffect(() => {
    fetchConfig();
  }, [fetchConfig]);

  const handleSlotEdit = (slot: string, field: "provider_id" | "model_id", value: string) => {
    const assignment = config?.slots[slot];
    const current = edits.get(slot) ?? {
      provider_id: assignment?.provider_id ?? "",
      model_id: assignment?.model_id ?? "",
    };
    setEdits(new Map(edits).set(slot, { ...current, [field]: value }));
  };

  const handleSave = async () => {
    if (edits.size === 0) return;
    setSaving(true);
    setError(null);
    try {
      const payload: Record<string, { provider_id: string; model_id: string }> = {};
      edits.forEach((v, k) => { payload[k] = v; });
      await updateModelRouting(payload);
      setEdits(new Map());
      await fetchConfig();
    } catch (e) {
      setError(`Save failed: ${e}`);
    }
    setSaving(false);
  };

  if (!config) {
    return <div className="text-sm text-text-muted py-4">Loading...</div>;
  }

  const slots = flattenSlots(config);

  return (
    <div className="space-y-4 max-w-2xl">
      {error && <p className="text-xs text-red-400">{error}</p>}

      {slots.map((slot) => {
        const edit = edits.get(slot.slot);
        return (
          <div key={slot.slot} className="bg-surface border border-surface-border rounded-lg p-4">
            <div className="flex items-center gap-2 mb-3">
              <span className="text-sm font-medium text-text-accent">{slot.slot}</span>
              {slot.description && (
                <span className="text-xs text-text-muted">({slot.description})</span>
              )}
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-text-muted block mb-1">Provider</label>
                <Input
                  value={edit?.provider_id ?? slot.provider_id}
                  onChange={(e) => handleSlotEdit(slot.slot, "provider_id", e.target.value)}
                  className="bg-void-50 border-surface-border text-text-primary text-sm h-8"
                />
              </div>
              <div>
                <label className="text-xs text-text-muted block mb-1">Model</label>
                <Input
                  value={edit?.model_id ?? slot.model_id}
                  onChange={(e) => handleSlotEdit(slot.slot, "model_id", e.target.value)}
                  className="bg-void-50 border-surface-border text-text-primary text-sm h-8"
                />
              </div>
            </div>
          </div>
        );
      })}

      {config.providers.length > 0 && (
        <div className="border-t border-surface-border pt-4">
          <div className="text-sm text-text-accent mb-2">Providers</div>
          <div className="flex flex-wrap gap-2">
            {config.providers.map((p) => (
              <Badge key={p.id} variant="outline" className="text-xs">
                {p.name} ({p.api_type})
              </Badge>
            ))}
          </div>
        </div>
      )}

      <div className="flex gap-2 pt-2">
        <Button
          onClick={handleSave}
          disabled={saving || edits.size === 0}
          className="bg-lapwing text-void hover:bg-lapwing-dark"
        >
          {saving ? "Saving..." : "Save"}
        </Button>
        {edits.size > 0 && (
          <Button variant="outline" onClick={() => setEdits(new Map())}>
            Reset
          </Button>
        )}
      </div>
    </div>
  );
}
