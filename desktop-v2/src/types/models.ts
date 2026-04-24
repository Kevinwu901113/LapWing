/** Backend returns slots as a dict keyed by slot name */
export interface SlotAssignment {
  provider_id: string;
  model_id: string;
  model_ref?: string;
  fallback_model_ids?: string[];
  fallback_model_refs?: string[];
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
  auth_type?: string;
  api_key_env?: string;
  protocol?: string;
  api_key_preview?: string;
  models?: ModelInfo[];
}

export interface ModelInfo {
  id: string;
  name: string;
  capabilities?: Record<string, unknown>;
  limits?: Record<string, unknown>;
  defaults?: Record<string, unknown>;
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
  model_ref?: string;
  fallback_model_ids?: string[];
  description: string;
}

export interface ProviderPayload {
  id: string;
  name: string;
  base_url: string;
  api_key?: string;
  api_type: string;
  auth_type?: string;
  api_key_env?: string;
  protocol?: string;
  models: ModelInfo[];
}
