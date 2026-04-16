/** Backend returns users as a dict keyed by user_id */
export interface UserPermissionEntry {
  level: number;
  name: string;
  source: "env" | "override";
  note?: string;
}

/** Flattened for frontend iteration */
export interface UserPermission {
  user_id: string;
  level: number;
  name: string;
  source: "env" | "override";
  note?: string;
}

export interface PermissionsResponse {
  users: Record<string, UserPermissionEntry>;
  defaults: Record<string, string>;
  operation_auth: Record<string, string>;
  default_auth: string;
}

export interface PermissionDefaultsResponse {
  desktop_default_owner: string;
  default_auth: string;
  default_auth_level: number;
  operation_auth: Record<string, { level: number; name: string }>;
}

export const LEVEL_LABELS: Record<number, string> = {
  0: "GUEST",
  1: "TRUSTED",
  2: "OWNER",
  3: "ADMIN",
};
