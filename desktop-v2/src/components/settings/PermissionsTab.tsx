import { useEffect, useState, useCallback } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Trash2, Plus } from "lucide-react";
import { getPermissions, setPermission, deletePermission } from "@/lib/api-v2";
import type { UserPermission } from "@/types/permissions";

/** Flatten dict-based users into array for display */
function flattenUsers(usersDict: Record<string, { level: number; name: string; source: string; note?: string }>): UserPermission[] {
  return Object.entries(usersDict).map(([user_id, entry]) => ({
    user_id,
    level: entry.level,
    name: entry.name,
    source: entry.source as "env" | "override",
    note: entry.note,
  }));
}

export function PermissionsTab() {
  const [users, setUsers] = useState<UserPermission[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [newUserId, setNewUserId] = useState("");
  const [newLevel, setNewLevel] = useState(1);
  const [newName, setNewName] = useState("");
  const [newNote, setNewNote] = useState("");

  const fetchPermissions = useCallback(async () => {
    try {
      const data = await getPermissions();
      setUsers(flattenUsers(data.users));
    } catch {
      setError("Failed to load permissions");
    }
  }, []);

  useEffect(() => {
    fetchPermissions();
  }, [fetchPermissions]);

  const handleDelete = async (userId: string) => {
    try {
      await deletePermission(userId);
      fetchPermissions();
    } catch (e) {
      setError(`Delete failed: ${e}`);
    }
  };

  const handleAdd = async () => {
    if (!newUserId.trim()) return;
    try {
      await setPermission(newUserId.trim(), newLevel, newName || undefined, newNote || undefined);
      setNewUserId("");
      setNewName("");
      setNewNote("");
      setShowAdd(false);
      fetchPermissions();
    } catch (e) {
      setError(`Add failed: ${e}`);
    }
  };

  const handleLevelChange = async (userId: string, level: number, name?: string) => {
    try {
      await setPermission(userId, level, name);
      fetchPermissions();
    } catch (e) {
      setError(`Update failed: ${e}`);
    }
  };

  return (
    <div className="space-y-4 max-w-lg">
      {error && <p className="text-xs text-red-400 mb-2">{error}</p>}

      <div className="text-sm text-text-accent">User Permissions</div>

      <div className="space-y-2">
        {users.map((u) => (
          <div key={u.user_id} className="bg-surface border border-surface-border rounded-lg p-3 flex items-center gap-3">
            <div className="flex-1 min-w-0">
              <div className="text-sm text-text-primary truncate">{u.name || u.user_id}</div>
              {u.name && <div className="text-xs text-text-muted truncate">{u.user_id}</div>}
              <div className="flex items-center gap-2 mt-0.5">
                {u.note && <span className="text-xs text-text-secondary">{u.note}</span>}
                <span className="text-[10px] text-text-muted">{u.source}</span>
              </div>
            </div>
            <select
              value={u.level}
              onChange={(e) => handleLevelChange(u.user_id, Number(e.target.value), u.name)}
              className="bg-void-50 border border-surface-border rounded px-2 py-1 text-xs text-text-primary"
            >
              <option value={0}>GUEST</option>
              <option value={1}>TRUSTED</option>
              <option value={2}>OWNER</option>
            </select>
            {u.source === "override" && (
              <button
                onClick={() => handleDelete(u.user_id)}
                className="p-1 hover:bg-surface-hover rounded text-text-muted hover:text-red-400"
                title="Remove override"
              >
                <Trash2 size={14} />
              </button>
            )}
          </div>
        ))}
      </div>

      {showAdd ? (
        <div className="bg-surface border border-surface-border rounded-lg p-3 space-y-2">
          <Input
            value={newUserId}
            onChange={(e) => setNewUserId(e.target.value)}
            placeholder="User ID (e.g. qq:12345)"
            className="bg-void-50 border-surface-border text-text-primary text-sm h-8"
          />
          <div className="flex gap-2">
            <select
              value={newLevel}
              onChange={(e) => setNewLevel(Number(e.target.value))}
              className="bg-void-50 border border-surface-border rounded px-2 py-1 text-xs text-text-primary"
            >
              <option value={0}>GUEST</option>
              <option value={1}>TRUSTED</option>
              <option value={2}>OWNER</option>
            </select>
            <Input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="Display name"
              className="bg-void-50 border-surface-border text-text-primary text-sm h-8 flex-1"
            />
          </div>
          <Input
            value={newNote}
            onChange={(e) => setNewNote(e.target.value)}
            placeholder="Note (optional)"
            className="bg-void-50 border-surface-border text-text-primary text-sm h-8"
          />
          <div className="flex gap-2">
            <Button onClick={handleAdd} size="sm" className="bg-lapwing text-void hover:bg-lapwing-dark">
              Add
            </Button>
            <Button onClick={() => setShowAdd(false)} size="sm" variant="outline">
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <Button
          onClick={() => setShowAdd(true)}
          variant="outline"
          size="sm"
          className="gap-1"
        >
          <Plus size={14} /> Add User
        </Button>
      )}
    </div>
  );
}
