/** Backend returns slots as a dict keyed by slot name */
export interface SlotAssignment {
  provider_id: string;
  model_id: string;
}

export interface SlotDefinition {
  name: string;
  description: string;
}

export interface ModelProvider {
  id: string;
  name: string;
  base_url: string;
  api_type: string;
  api_key_preview?: string;
  models?: { id: string; name: string }[];
}

export interface ModelRoutingConfig {
  providers: ModelProvider[];
  slots: Record<string, SlotAssignment>;
  slot_definitions?: Record<string, SlotDefinition>;
}

/** Flattened slot for frontend display */
export interface SlotDisplayItem {
  slot: string;
  provider_id: string;
  model_id: string;
  description: string;
}
